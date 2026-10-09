"""Official app-server transport; original conversations are never resumed."""
import json
import os
import pathlib
import queue
import signal
import shutil
import subprocess
import threading
import time
import tomllib
from status_core import PROMPT, SCHEMA, fingerprint, redact


class RpcError(RuntimeError):
    pass


class ClassificationError(RpcError):
    def __init__(self, reason, usage=None):
        super().__init__(reason)
        self.usage = usage


def resolve_command(command):
    if command != "auto":
        return command
    override = os.environ.get("CODEX_SEMANTIC_STATUS_CODEX")
    if override:
        return override
    candidates = [
        "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
        str(pathlib.Path.home() / "Applications/Codex.app/Contents/Resources/codex"),
    ]
    for path in candidates:
        if pathlib.Path(path).is_file() and os.access(path, os.X_OK):
            return path
    path = shutil.which("codex")
    if not path:
        raise RpcError("Codex executable not found")
    return path


class CodexRPC:
    def __init__(self, command="auto", disable_hooks=True):
        self.command = resolve_command(command)
        self.seq = 0
        self.inbox = queue.Queue()
        self.notifications = []
        self.goal_cache = {}
        env = dict(os.environ, CODEX_SEMANTIC_STATUS_CLASSIFIER="1")
        command_args = [self.command, "app-server", "--stdio"]
        if disable_hooks:
            command_args += ["--disable", "hooks"]
        self.process = subprocess.Popen(command_args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, env=env, start_new_session=True)
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.request("initialize", {"clientInfo": {"name": "codex-semantic-status", "version": "1.2.0"},
                                        "capabilities": {"experimentalApi": True}}, timeout=12)
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                if len(line) > 64 * 1024 * 1024:
                    raise RpcError("RPC response too large")
                self.inbox.put(json.loads(line))
        except Exception as e:
            self.inbox.put(e)
        finally:
            self.inbox.put(RpcError("app-server closed"))

    def send(self, obj):
        self.process.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def receive(self, timeout):
        try:
            obj = self.inbox.get(timeout=max(.01, timeout))
        except queue.Empty as e:
            raise TimeoutError("app-server timeout") from e
        if isinstance(obj, Exception):
            raise obj
        return obj

    def request(self, method, params, timeout=12):
        self.seq += 1
        i = self.seq
        self.send({"id": i, "method": method, "params": params})
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            obj = self.receive(end - time.monotonic())
            if "method" in obj:
                if "id" in obj:
                    self.send({"id": obj["id"], "error": {"code": -32601, "message": "No client tools permitted"}})
                else:
                    self.notifications.append(obj)
                continue
            if obj.get("id") == i:
                if "error" in obj:
                    # Do not echo full server errors: provider errors can contain credentials.
                    err = obj["error"]
                    raise RpcError(str(err.get("code")) + " " + redact(err.get("message", "RPC error"))[:240])
                return obj.get("result", {})
        raise TimeoutError(method)

    def close(self):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=2)
        for stream in [self.process.stdin, self.process.stdout]:
            if stream:
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def models(self):
        return self.request("model/list", {"limit": 100}).get("data", [])

    def select_model(self, requested):
        models = self.models()
        if requested == "auto":
            choices = []
            for family in ["luna", "nano", "mini"]:
                choices = [m for m in models if family in m.get("model", "").lower()]
                if choices:
                    break
            if not choices:
                raise RpcError("No advertised luna/nano/mini model; configure model explicitly")
            return choices[0]["model"]
        if requested not in [m.get("model") for m in models]:
            raise RpcError("Configured model is not advertised; fallback is disabled")
        return requested

    def snapshot(self, tid):
        meta = self.request("thread/read", {"threadId": tid, "includeTurns": False})["thread"]
        try:
            turns = self.request("thread/turns/list", {"threadId": tid, "limit": 3,
                "sortDirection": "desc", "itemsView": "full"}).get("data", [])
            turns = list(reversed(turns))
        except RpcError:
            turns = self.request("thread/read", {"threadId": tid, "includeTurns": True})["thread"].get("turns", [])[-3:]
        messages = []
        evidence = []
        for turn in turns:
            for item in turn.get("items", []):
                kind = item.get("type")
                if kind == "userMessage":
                    text = "\n".join(x.get("text", "") for x in item.get("content", []) if x.get("type") == "text")
                    if text and not text.lstrip().startswith(("# AGENTS.md instructions", "<environment_context>", "<external_codex_apps_open_page>")):
                        messages.append({"role": "user", "text": redact(text)})
                elif kind == "agentMessage" and item.get("phase") in [None, "final", "final_answer"]:
                    if item.get("text"):
                        messages.append({"role": "assistant", "text": redact(item["text"])})
                elif kind == "commandExecution" and item.get("exitCode") is not None:
                    command = redact(item.get("command", "").split("\n")[0])[:160]
                    evidence.append({"role": "evidence", "text": "command=" + command + "; exitCode=" + str(item["exitCode"])})
        # Keep evidence bounded without displacing the newest user request and final answer.
        messages = evidence[-2:] + messages
        if tid not in self.goal_cache:
            root_goal = ""
            try:
                first_turns = self.request("thread/turns/list", {"threadId": tid, "limit": 4,
                    "sortDirection": "asc", "itemsView": "full"}).get("data", [])
                for t in first_turns:
                    for item in t.get("items", []):
                        if item.get("type") != "userMessage":
                            continue
                        text = "\n".join(c.get("text", "") for c in item.get("content", []) if c.get("type") == "text")
                        if text and not text.lstrip().startswith(("# AGENTS.md", "<environment_context>", "<external_codex_apps_open_page>")):
                            root_goal = redact(text)
                            break
                    if root_goal:
                        break
            except RpcError:
                pass
            preview = meta.get("preview", "")
            self.goal_cache[tid] = root_goal or ("" if preview.lstrip().startswith(("# AGENTS.md", "<environment_context>")) else preview)
        goal = self.goal_cache[tid]
        content = {"goal": goal, "messages": messages}
        version = fingerprint({"content": content, "turns": [(t.get("id"), t.get("status"),
                   [i.get("id") for i in t.get("items", [])]) for t in turns]})
        last_status = turns[-1].get("status") if turns else None
        return {"title": meta.get("name"), "goal": goal, "messages": messages,
                "fingerprint": fingerprint(content), "version": version,
                "ready": bool(messages) and last_status not in ["inProgress", "in_progress", "active"]}

    def set_name(self, tid, name):
        return self.request("thread/name/set", {"threadId": tid, "name": name})

    def classify(self, payload, config):
        if config.get("backend", "codex") == "api":
            from api_provider import classify
            return classify(payload, config)
        selected_provider = config.get("model_provider", "")
        model = config["model"] if selected_provider else self.select_model(config["model"])
        # Inherit provider/auth settings, but do not load global skills, hooks, or integrations.
        cfg_path = pathlib.Path(os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex"))) / "config.toml"
        inherited = tomllib.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        available_skills = self.request("skills/list", {"cwds": [str(pathlib.Path(__file__).resolve().parent)]})
        skill_paths = {s["path"] for row in available_skills.get("data", []) for s in row.get("skills", [])}
        isolated = {"project_doc_max_bytes": 0, "web_search": "disabled",
                    "skills": {"config": [{"path": p, "enabled": False} for p in sorted(skill_paths)]},
                    "features": {"hooks": False, "plugins": False, "apps": False, "multi_agent": False,
                                 "shell_tool": False, "unified_exec": False, "view_image": False,
                                 "skip_host_skill_discovery": True, "unbounded_connection_retries": False},
                    "mcp_servers": {k: {"enabled": False} for k in inherited.get("mcp_servers", {})},
                    "plugins": {k: {"enabled": False} for k in inherited.get("plugins", {})}}
        provider = selected_provider or inherited.get("model_provider", "openai")
        definition = config.get("model_providers", {}).get(selected_provider, {}) if selected_provider else {}
        native = {k: v for k, v in definition.items()
                  if k in ["name", "base_url", "wire_api", "env_key", "requires_openai_auth"]}
        if definition:
            native.setdefault("name", provider)
            native.setdefault("wire_api", "responses")
            native.setdefault("requires_openai_auth", False)
        native.update({"request_max_retries": 0, "stream_max_retries": 0})
        isolated["model_providers"] = {provider: native}
        params = {"model": model, "allowProviderModelFallback": False,
            "ephemeral": True, "baseInstructions": PROMPT, "developerInstructions": "",
            "approvalPolicy": "never", "sandbox": "read-only", "selectedCapabilityRoots": [],
            "environments": [], "dynamicTools": [], "cwd": str(pathlib.Path(__file__).resolve().parent),
            "config": isolated, "serviceName": "semantic-status-classifier"}
        if selected_provider:
            params["modelProvider"] = selected_provider
        start = self.request("thread/start", params)
        tid = start["thread"]["id"]
        if start.get("model") != model:
            raise RpcError("Requested lightweight model was changed; refusing fallback")
        if selected_provider and start.get("modelProvider") != selected_provider:
            raise RpcError("Requested provider was changed; refusing fallback")
        self.notifications.clear()
        end = time.monotonic() + config["classification_timeout_seconds"]
        turn = self.request("turn/start", {"threadId": tid,
            "input": [{"type": "text", "text": payload}], "effort": "low",
            "outputSchema": SCHEMA, "environments": []}, timeout=min(12, config["classification_timeout_seconds"]))
        turn_id = turn["turn"]["id"]
        text = ""
        usage = None
        backlog = list(self.notifications)
        self.notifications.clear()
        try:
            while time.monotonic() < end:
                obj = backlog.pop(0) if backlog else self.receive(end - time.monotonic())
                method = obj.get("method", "")
                params = obj.get("params", {})
                if "id" in obj and method:
                    self.send({"id": obj["id"], "error": {"code": -32601, "message": "Classifier tools disabled"}})
                    raise RpcError("Classifier requested a tool")
                if params.get("threadId") != tid:
                    continue
                item = params.get("item", {})
                if method in ["item/started", "item/completed"] and item.get("type") not in [None, "userMessage", "agentMessage", "reasoning"]:
                    raise RpcError("Classifier attempted tool activity")
                if method == "thread/tokenUsage/updated":
                    usage = params.get("tokenUsage", {}).get("total", {}).get("totalTokens")
                elif method == "item/agentMessage/delta":
                    text += params.get("delta", "")
                    if len(text) > 6000:
                        raise RpcError("Classifier output too long")
                elif method == "item/completed" and item.get("type") == "agentMessage":
                    text = item.get("text", text)
                elif method == "turn/completed":
                    if params["turn"].get("status") != "completed":
                        raise RpcError("Classification turn did not complete")
                    return json.loads(text), usage
            raise TimeoutError("classification timeout")
        except Exception as error:
            try:
                self.request("turn/interrupt", {"threadId": tid, "turnId": turn_id}, timeout=2)
            except Exception:
                pass
            raise ClassificationError(type(error).__name__, usage=usage) from error
        finally:
            try:
                self.request("thread/unsubscribe", {"threadId": tid}, timeout=2)
            except Exception:
                pass
