#!/usr/bin/env python3
"""Management commands. Hook execution itself is silent and always non-blocking."""
import argparse
import json
import pathlib
import sys
import time
from codex_rpc import CodexRPC
from status_core import DEFAULTS, context_payload, data_dir, load_config, managed_title, validate_decision
from status_store import Store
from status_worker import enqueue_hook, file_lock, run_worker
from provider_cli import add_parser as add_provider_parser, manage as manage_provider, save_config
from provider_config import credential_ready, effective_budget


def output(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Codex semantic title status, asynchronous and budgeted")
    parser.add_argument("--data-dir", type=pathlib.Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hook")
    sub.add_parser("worker")
    sub.add_parser("status")
    logs = sub.add_parser("logs")
    logs.add_argument("--limit", type=int, default=20)
    sub.add_parser("doctor")
    add_provider_parser(sub)
    c = sub.add_parser("configure")
    c.add_argument("--set", action="append", default=[], metavar="KEY=JSON")
    for n in ["pause", "resume", "enqueue", "preview"]:
        c = sub.add_parser(n)
        c.add_argument("thread_id")
    install = sub.add_parser("install")
    install.add_argument("--replace-source", action="store_true",
                         help="Replace this plugin's previously registered checkout path")
    args = parser.parse_args()
    directory = (args.data_dir or data_dir()).expanduser().resolve()
    if args.command == "hook":
        try:
            content = sys.stdin.read(2 * 1024 * 1024 + 1)
            if len(content) <= 2 * 1024 * 1024:
                enqueue_hook(json.loads(content), directory)
        except Exception:
            pass
        print("{}")  # Stop requires JSON; no additionalContext or systemMessage.
        return
    cfg = load_config(directory)
    if args.command == "worker":
        run_worker(directory)
    elif args.command == "status":
        report = Store(directory).report() if (directory / "state.sqlite3").exists() else {"usage": [], "threads": [], "pending": 0}
        output({"config": cfg, "data_dir": str(directory), **report})
    elif args.command == "logs":
        if not 1 <= args.limit <= 200:
            raise ValueError("event limit must be 1..200")
        output(Store(directory).events(args.limit) if (directory / "state.sqlite3").exists() else [])
    elif args.command == "configure":
        for entry in args.set:
            key, value = entry.split("=", 1)
            if key not in DEFAULTS:
                raise ValueError("unknown configuration key: " + key)
            cfg[key] = json.loads(value)
        save_config(directory, cfg)
        output(cfg)
    elif args.command == "provider":
        output(manage_provider(args, directory, cfg))
    elif args.command in ["pause", "resume"]:
        Store(directory).pause(args.thread_id, args.command == "pause")
        output({"thread_id": args.thread_id, "paused": args.command == "pause"})
    elif args.command == "enqueue":
        output({"queued": enqueue_hook({"hook_event_name": "Stop", "session_id": args.thread_id,
            "turn_id": "manual-" + str(time.time_ns())}, directory)})
    elif args.command == "doctor":
        report = {"python": sys.version.split()[0], "model": cfg["model"], "data_dir": str(directory),
                  "backend": cfg["backend"], "model_provider": cfg["model_provider"],
                  "background_concurrency": 1, "calls_model": False, "budget_is_admission_control": True,
                  "effective_token_reservation": effective_budget(cfg)["token_reservation"]}
        if cfg["backend"] == "api":
            definition = cfg["model_providers"][cfg["model_provider"]]
            report.update(base_url=definition["base_url"], protocol=definition.get("wire_api", "chat"),
                          credential_ready=credential_ready(definition), connectivity_checked=False,
                          source_reader_requires_codex=True)
        else:
            with CodexRPC(cfg["codex_command"]) as rpc:
                if cfg["model_provider"]:
                    definition = cfg["model_providers"].get(cfg["model_provider"], {})
                    report.update(credential_ready=credential_ready(definition), provider_model_verified=False)
                else:
                    report["model"] = rpc.select_model(cfg["model"])
        output(report)
    elif args.command == "preview":
        with file_lock(directory, "worker.lock", blocking=False) as lock:
            if lock is None:
                raise RuntimeError("worker busy; preview would compete for the budget")
            store = Store(directory)
            with CodexRPC(cfg["codex_command"]) as rpc:
                snapshot = rpc.snapshot(args.thread_id)
                budget = effective_budget(cfg)
                rid = store.reserve(args.thread_id, budget)
                if rid is None:
                    raise RuntimeError("daily budget exhausted")
                payload = context_payload(snapshot["goal"], snapshot["messages"],
                    store.current(args.thread_id)["decision"], cfg["max_input_chars"])
                try:
                    raw, usage = rpc.classify(payload, cfg)
                except Exception as error:
                    observed = getattr(error, "usage", None)
                    if isinstance(observed, int) and observed >= 0:
                        store.settle(rid, max(budget["token_reservation"], observed))
                    raise
                store.settle(rid, usage)
                d = validate_decision(raw, cfg, not json.loads(payload)["incomplete_user_context"])
                output({"decision": raw, "accepted": bool(d), "tokens": usage,
                        "proposed_title": managed_title(snapshot["title"], d["state"]) if d else None,
                        "title_written": False})
    elif args.command == "install":
        root = pathlib.Path(__file__).resolve().parents[1]
        with CodexRPC(cfg["codex_command"]) as rpc:
            source = {"source_type": "local", "source": str(root)}
            existing = rpc.request("config/read", {"includeLayers": False}).get("config", {}).get("marketplaces", {}).get("semantic-status-local")
            conflict = existing is not None and any(existing.get(k) != v for k, v in source.items())
            if conflict and not args.replace_source:
                raise RuntimeError("semantic-status-local is registered elsewhere; use install --replace-source to move it")
            if existing is None or conflict:
                rpc.request("config/value/write", {"keyPath": "marketplaces.semantic-status-local",
                    "value": source, "mergeStrategy": "upsert"})
            output(rpc.request("plugin/install", {"pluginName": "codex-semantic-status",
                "marketplacePath": str(root / ".agents/plugins/marketplace.json")}, timeout=30))
        print("Installed through Codex. Review and trust the Stop hook in Codex before use.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # No raw provider exception text or account credentials in management output.
        print("semantic-status: " + type(e).__name__ + ": " + str(e)[:240], file=sys.stderr)
        sys.exit(1)
