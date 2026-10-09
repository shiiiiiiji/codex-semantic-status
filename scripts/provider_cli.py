"""Provider management and a terminal setup wizard; never performs inference."""
import copy
import getpass
import json
import os
import pathlib
import sys
import tempfile
from provider_config import credential_ready, save_key
from status_core import validate_config


def save_config(directory, cfg):
    validate_config(cfg)
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix="config-", suffix=".tmp", delete=False) as handle:
        handle.write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        temp = pathlib.Path(handle.name)
    try:
        os.replace(temp, directory / "config.json")
    finally:
        temp.unlink(missing_ok=True)


def add_parser(sub):
    parser = sub.add_parser("provider", help="Manage custom providers; no inference")
    actions = parser.add_subparsers(dest="provider_command", required=True)
    actions.add_parser("list", help="List provider definitions and credential readiness")
    actions.add_parser("setup", help="Interactive direct-API setup, with hidden key input")
    actions.add_parser("reset", help="Return to inherited Codex provider and auto model")
    add = actions.add_parser("add", help="Register an OpenAI-compatible endpoint")
    add.add_argument("name")
    add.add_argument("--base-url", required=True)
    add.add_argument("--protocol", choices=["chat", "responses"], default="chat")
    add.add_argument("--model", required=True)
    auth = add.add_mutually_exclusive_group()
    auth.add_argument("--env-key", help="Environment variable name, not a key value")
    auth.add_argument("--key-file", type=pathlib.Path, help="Existing owner-only key file")
    auth.add_argument("--save-key", action="store_true", help="Prompt invisibly and store outside Git")
    auth.add_argument("--codex-auth", action="store_true", help="Codex login; use only with Codex Responses backend")
    add.add_argument("--response-format", choices=["json_object", "json_schema", "none"], default="json_object")
    add.add_argument("--token-limit-field", choices=["max_tokens", "max_completion_tokens"], default="max_tokens")
    add.add_argument("--replace", action="store_true", help="Replace an existing definition")
    use = actions.add_parser("use", help="Select a stored or existing Codex provider")
    use.add_argument("name")
    use.add_argument("--model", help="Override the stored default model")
    use.add_argument("--backend", choices=["api", "codex"], help="Default: API for stored providers, Codex for inherited providers")
    remove = actions.add_parser("remove", help="Remove an inactive definition; retains key files")
    remove.add_argument("name")


def choose(cfg, pid, model=None, backend=None):
    definition = cfg["model_providers"].get(pid, {})
    cfg["model_provider"] = pid
    cfg["backend"] = backend or ("api" if definition and not definition.get("requires_openai_auth") else "codex")
    cfg["model"] = model or definition.get("default_model", "auto")
    return validate_config(cfg)


def prompt(label, default=None):
    value = input(label + (" [" + default + "]" if default else "") + ": ").strip()
    return value or default or ""


def read_key():
    if not sys.stdin.isatty():
        raise ValueError("hidden key input requires a terminal; use --key-file or --env-key")
    return getpass.getpass("API key (hidden; stored only in a private local file): ")


def manage(args, directory, config):
    cfg = copy.deepcopy(config)
    action = args.provider_command
    if action == "list":
        return {"backend": cfg["backend"], "model_provider": cfg["model_provider"], "model": cfg["model"],
                "providers": [{"id": pid, "selected": pid == cfg["model_provider"], **definition,
                               "credential_ready": credential_ready(definition)}
                              for pid, definition in sorted(cfg["model_providers"].items())]}
    if action == "reset":
        cfg.update(backend="codex", model_provider="", model="auto")
    elif action == "use":
        choose(cfg, args.name, args.model, args.backend)
    elif action == "remove":
        if args.name == cfg["model_provider"]:
            raise ValueError("select another provider or run provider reset before removing the active provider")
        if args.name not in cfg["model_providers"]:
            raise ValueError("provider definition not found")
        del cfg["model_providers"][args.name]
    else:
        if action == "setup":
            print("Configure the background classifier. This sends bounded conversation text to the chosen endpoint.")
            print("No connection or model call is made during setup. Budgets and source-thread settings are preserved.")
            pid = prompt("Provider ID (letters, digits, _ or -)")
            definition = {"base_url": prompt("API base URL, including /v1 if required"),
                          "default_model": prompt("Exact model name"), "wire_api": prompt("Protocol: chat or responses", "chat")}
            auth = prompt("Authentication: file (hidden key), env, existing-file or none", "file")
            if auth not in ["file", "env", "existing-file", "none"]:
                raise ValueError("unsupported authentication choice")
            if auth == "env":
                definition["env_key"] = prompt("Environment variable NAME")
            elif auth == "existing-file":
                definition["api_key_file"] = str(pathlib.Path(prompt("Absolute key file path")).expanduser().resolve())
            hidden = auth == "file"
            replace = False
        else:
            pid = args.name
            definition = {"base_url": args.base_url, "default_model": args.model, "wire_api": args.protocol,
                          "response_format": args.response_format, "token_limit_field": args.token_limit_field}
            if args.env_key:
                definition["env_key"] = args.env_key
            if args.key_file:
                definition["api_key_file"] = str(args.key_file.expanduser().resolve())
            if args.codex_auth:
                definition["requires_openai_auth"] = True
            hidden = args.save_key
            replace = args.replace
        if pid in cfg["model_providers"] and not replace:
            raise ValueError("provider exists; use provider add --replace to change its definition")
        cfg["model_providers"][pid] = definition
        if action == "setup":
            choose(cfg, pid)
        # Validate the complete choice before collecting or storing any credential.
        validate_config(cfg)
        if hidden:
            definition["api_key_file"] = str(pathlib.Path(directory).resolve() / "keys" / (pid + ".key"))
            validate_config(cfg)
            definition["api_key_file"] = save_key(directory, pid, read_key())
    save_config(directory, cfg)
    return {"saved": True, "backend": cfg["backend"], "model_provider": cfg["model_provider"],
            "model": cfg["model"], "calls_model": False}
