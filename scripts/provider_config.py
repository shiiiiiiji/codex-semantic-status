"""Provider selection and credential references, independent of conversation storage."""
import ipaddress
import os
import pathlib
import re
import tempfile
from urllib.parse import urlsplit

PROVIDER_FIELDS = {"name", "base_url", "wire_api", "env_key", "api_key_file",
                   "requires_openai_auth", "default_model", "response_format", "token_limit_field"}


def validate_providers(cfg):
    if cfg.get("backend", "codex") not in ["codex", "api"]:
        raise ValueError("backend must be codex or api")
    selected = cfg.get("model_provider", "")
    if not isinstance(selected, str) or (selected and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", selected)):
        raise ValueError("invalid model_provider identifier")
    definitions = cfg.get("model_providers", {})
    if not isinstance(definitions, dict):
        raise ValueError("model_providers must be an object")
    for pid, definition in definitions.items():
        if not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pid):
            raise ValueError("invalid provider identifier")
        if not isinstance(definition, dict) or set(definition) - PROVIDER_FIELDS:
            raise ValueError("unsupported provider property; use credential references instead of inline keys")
        for key, value in definition.items():
            if key == "requires_openai_auth":
                if not isinstance(value, bool):
                    raise ValueError("requires_openai_auth must be boolean")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError("provider fields must be nonempty strings: " + key)
        url = urlsplit(definition.get("base_url", ""))
        try:
            port = url.port
        except ValueError as error:
            raise ValueError("invalid provider port") from error
        if url.scheme not in ["https", "http"] or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("base_url must be an HTTP(S) API root without credentials, query or fragment")
        if url.scheme == "http" and url.hostname != "localhost":
            try:
                loopback = ipaddress.ip_address(url.hostname).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise ValueError("remote providers require HTTPS; HTTP is supported for loopback only")
        if definition.get("wire_api", "chat") not in ["chat", "responses"]:
            raise ValueError("wire_api must be chat or responses")
        if definition.get("response_format", "json_object") not in ["json_object", "json_schema", "none"]:
            raise ValueError("response_format must be json_object, json_schema or none")
        if definition.get("token_limit_field", "max_tokens") not in ["max_tokens", "max_completion_tokens"]:
            raise ValueError("invalid chat token limit field")
        if definition.get("env_key") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", definition["env_key"]):
            raise ValueError("env_key must name an environment variable")
        if definition.get("api_key_file") and not pathlib.Path(definition["api_key_file"]).expanduser().is_absolute():
            raise ValueError("api_key_file must be absolute")
        if definition.get("env_key") and definition.get("api_key_file"):
            raise ValueError("choose env_key or api_key_file, not both")
        if definition.get("requires_openai_auth") and (definition.get("env_key") or definition.get("api_key_file")):
            raise ValueError("Codex login authentication cannot be combined with API-key references")
    if selected and cfg["model"] == "auto":
        raise ValueError("custom providers require an explicit model")
    if cfg.get("backend") == "api":
        if not selected or selected not in definitions or cfg["model"] == "auto":
            raise ValueError("API backend requires a defined provider and explicit model")
        if definitions[selected].get("requires_openai_auth"):
            raise ValueError("API backend uses API keys or no authentication; Codex login requires backend=codex")
    elif selected in definitions:
        definition = definitions[selected]
        if definition.get("wire_api", "responses") != "responses":
            raise ValueError("Codex backend requires Responses API; use backend=api for chat")
        if definition.get("api_key_file"):
            raise ValueError("api_key_file is supported by the API backend; use env_key for Codex providers")
    return cfg


def effective_budget(cfg):
    if cfg.get("backend", "codex") == "api":
        return dict(cfg, token_reservation=cfg["api_token_reservation"])
    return cfg


def credential(definition):
    if definition.get("env_key"):
        key = os.environ.get(definition["env_key"], "").strip()
        if not key:
            raise ValueError("configured credential environment variable is unavailable")
    elif definition.get("api_key_file"):
        path = pathlib.Path(definition["api_key_file"]).expanduser()
        if path.stat().st_mode & 0o077:
            raise ValueError("credential file must have owner-only permissions, for example chmod 600")
        if path.stat().st_size > 8192:
            raise ValueError("credential file is too large")
        key = path.read_text().strip()
        if not key:
            raise ValueError("credential file is empty")
    else:
        return None
    if len(key) > 8192 or "\r" in key or "\n" in key:
        raise ValueError("invalid credential value")
    return key


def credential_ready(definition):
    try:
        credential(definition)
        return True
    except (OSError, ValueError):
        return False


def save_key(directory, pid, key):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pid) or not key.strip() or "\n" in key or "\r" in key or len(key) > 8192:
        raise ValueError("invalid provider identifier or credential")
    directory = pathlib.Path(directory).resolve()
    if any((parent / ".git").exists() for parent in [directory, *directory.parents]):
        raise ValueError("store credentials outside Git checkouts")
    target_dir = directory / "keys"
    real_target = target_dir.resolve()
    if not real_target.is_relative_to(directory) or any((parent / ".git").exists() for parent in [real_target, *real_target.parents]):
        raise ValueError("credential directory must remain outside Git and inside the plugin data directory")
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = target_dir / (pid + ".key")
    with tempfile.NamedTemporaryFile(mode="w", dir=target_dir, delete=False) as handle:
        handle.write(key.strip() + "\n")
        temp = pathlib.Path(handle.name)
    try:
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return str(target)
