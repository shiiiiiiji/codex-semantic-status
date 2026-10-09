"""Bounded, tool-free HTTP inference in a child with an absolute deadline."""
import http.client
import json
import pathlib
import subprocess
import sys
from urllib.parse import urlsplit
from provider_config import credential, validate_providers
from status_core import PROMPT, SCHEMA
from codex_rpc import ClassificationError

MAX_RESPONSE_BYTES = 256 * 1024
MAX_IPC_CHARS = 128 * 1024


def request_body(payload, cfg, definition):
    wire = definition.get("wire_api", "chat")
    mode = definition.get("response_format", "json_object")
    schema = {"name": "semantic_status", "strict": True, "schema": SCHEMA}
    if wire == "chat":
        body = {"model": cfg["model"], "messages": [{"role": "system", "content": PROMPT},
                {"role": "user", "content": payload}], "stream": False,
                definition.get("token_limit_field", "max_tokens"): cfg["max_output_tokens"]}
        if mode != "none":
            body["response_format"] = {"type": mode}
            if mode == "json_schema":
                body["response_format"]["json_schema"] = schema
    else:
        body = {"model": cfg["model"], "instructions": PROMPT, "input": payload,
                "stream": False, "store": False, "max_output_tokens": cfg["max_output_tokens"]}
        if mode != "none":
            body["text"] = {"format": {"type": mode}}
            if mode == "json_schema":
                body["text"]["format"].update(schema)
    return body


def usage_tokens(obj):
    usage = obj.get("usage") or {}
    def integer(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    total = usage.get("total_tokens")
    if integer(total):
        return total
    for a, b in [("input_tokens", "output_tokens"), ("prompt_tokens", "completion_tokens")]:
        if integer(usage.get(a)) and integer(usage.get(b)):
            return usage[a] + usage[b]
    return None


def classify_http(payload, cfg):
    usage = None
    connection = None
    try:
        validate_providers(cfg)
        definition = cfg["model_providers"][cfg["model_provider"]]
        key = credential(definition)
        url = urlsplit(definition["base_url"])
        wire = definition.get("wire_api", "chat")
        path = url.path.rstrip("/") + ("/responses" if wire == "responses" else "/chat/completions")
        cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        connection = cls(url.hostname, port=url.port, timeout=cfg["classification_timeout_seconds"])
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        connection.request("POST", path, json.dumps(request_body(payload, cfg, definition)).encode(), headers)
        response = connection.getresponse()
        # http.client performs neither redirects nor automatic retries.
        if response.status != 200:
            raise ClassificationError("provider HTTP " + str(response.status))
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ClassificationError("provider response too large")
        obj = json.loads(raw)
        usage = usage_tokens(obj)
        if wire == "chat":
            choices = obj["choices"]
            if len(choices) != 1:
                raise ValueError("unexpected number of choices")
            choice = choices[0]
            msg = choice["message"]
            if msg.get("tool_calls") or msg.get("function_call") or msg.get("refusal"):
                raise ValueError("unexpected tool or refusal")
            if choice.get("finish_reason", "stop") != "stop":
                raise ValueError("incomplete response")
            text = msg["content"]
        else:
            if obj.get("status", "completed") != "completed" or obj.get("error"):
                raise ValueError("incomplete response")
            parts = []
            for item in obj["output"]:
                if item["type"] == "reasoning":
                    continue
                if item["type"] != "message":
                    raise ValueError("unexpected tool output")
                for content in item["content"]:
                    if content["type"] != "output_text":
                        raise ValueError("unexpected message content")
                    parts.append(content["text"])
            text = "".join(parts)
        if not isinstance(text, str) or len(text) > 6000:
            raise ValueError("invalid classifier text")
        return json.loads(text), usage
    except ClassificationError:
        raise
    except Exception as error:
        # Provider bodies, credentials, endpoint exception text and transcripts never leave this adapter.
        raise ClassificationError("provider " + type(error).__name__, usage=usage) from None
    finally:
        if connection:
            connection.close()


def classify(payload, config):
    # Send only the selected definition; unrelated stored providers do not belong to this request.
    isolated = {k: config[k] for k in ["backend", "model", "model_provider", "max_output_tokens",
                                     "classification_timeout_seconds"]}
    isolated["model_providers"] = {config["model_provider"]: config["model_providers"][config["model_provider"]]}
    incoming = json.dumps({"payload": payload, "config": isolated}, ensure_ascii=False)
    if len(incoming) > MAX_IPC_CHARS:
        raise ClassificationError("provider invocation too large")
    try:
        result = subprocess.run([sys.executable, "-B", str(pathlib.Path(__file__).resolve())],
            input=incoming, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=config["classification_timeout_seconds"])
    except subprocess.TimeoutExpired:
        raise ClassificationError("provider deadline exceeded") from None
    if result.returncode != 0 or len(result.stdout) > MAX_RESPONSE_BYTES:
        raise ClassificationError("provider child failed")
    try:
        reply = json.loads(result.stdout)
        if "error" in reply:
            raise ClassificationError(reply["error"], usage=reply.get("usage"))
        return reply["decision"], reply.get("usage")
    except (KeyError, ValueError):
        raise ClassificationError("provider child returned invalid output") from None


if __name__ == "__main__":
    try:
        raw = sys.stdin.read(MAX_IPC_CHARS + 1)
        if len(raw) > MAX_IPC_CHARS:
            raise ValueError("invocation too large")
        incoming = json.loads(raw)
        decision, used = classify_http(incoming["payload"], incoming["config"])
        print(json.dumps({"decision": decision, "usage": used}))
    except ClassificationError as error:
        print(json.dumps({"error": str(error), "usage": error.usage}))
    except Exception:
        print(json.dumps({"error": "invalid provider invocation", "usage": None}))
