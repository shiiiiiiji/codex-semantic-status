# Custom providers and models

Commands run from the repository root, or from an installed copy using the absolute script path. These settings affect only the background classifier. They do not change the source conversation's model or global Codex settings.

## Interactive setup

```sh
python3 scripts/semantic_status.py provider setup
```

Enter a provider ID, API base URL, exact model name and protocol (`chat` or `responses`). Select hidden key input, an existing key file, an environment variable, or no authentication. The wizard saves and selects the provider; it makes no network request. Ctrl+C cancels setup.

Hidden input requires a terminal and saves the key to `keys/PROVIDER.key` under the plugin data directory, with mode 600, outside Git. Configuration contains only the file reference. An environment variable exported in a terminal may be unavailable to Codex launched from the Dock; a local key file is usually simpler for desktop Hooks.

## Commands

Register a definition, then select it:

```sh
python3 scripts/semantic_status.py provider add gateway \
  --base-url https://your-provider.example/v1 --protocol chat \
  --model your-small-model --env-key MY_PROVIDER_API_KEY
python3 scripts/semantic_status.py provider use gateway
python3 scripts/semantic_status.py provider list
python3 scripts/semantic_status.py doctor
```

Replace the example endpoint and model with your provider's values. `base-url` is the API root, not the full `/chat/completions` or `/responses` path. Remote endpoints require HTTPS; localhost/loopback may use HTTP. Adding an existing ID requires `--replace`.

Use `--save-key` instead of `--env-key` to prompt invisibly, or `--key-file /absolute/path` to reference an existing owner-only file. No inline API-key argument is accepted. Provider listing and doctor never print credential values.

```sh
python3 scripts/semantic_status.py provider use gateway --model another-small-model
python3 scripts/semantic_status.py provider reset
python3 scripts/semantic_status.py provider remove gateway
```

`reset` restores the inherited Codex provider and `model=auto`. It retains definitions, budgets, usage, titles and paused conversations. Switch/reset before removing an active definition. Removal retains the credential file.

## Direct API backend

`api` calls an OpenAI-compatible endpoint with only the bounded classification instructions and conversation selection. It avoids Codex framework input overhead and uses a child process with an absolute inference deadline. Both backends remain asynchronous and share one persistent budget ledger. Codex is still required to read source conversations and write titles.

Supported protocols are Chat Completions and Responses. Compatibility options on `provider add`:

| Option | Values | Default |
| --- | --- | --- |
| `--protocol` | `chat`, `responses` | `chat` |
| `--response-format` | `json_object`, `json_schema`, `none` | `json_object` |
| `--token-limit-field` | `max_tokens`, `max_completion_tokens` (Chat only) | `max_tokens` |

The output cap is `max_output_tokens=512`, configurable from 64 to 4096. The default API reservation is 8,000 Tokens; the Codex reservation remains 20,000. Neither is a price estimate or hard cost ceiling. Missing usage and timed-out requests retain their reservation. Failures with observed usage charge at least the larger of the reservation and reported usage. Changing provider does not reset daily totals or attempt counts.

There are no automatic retries, redirects, tool definitions or model fallbacks. Unsupported output formats/caps fail. The local JSON, confidence and completion checks still apply with format `none`. Responses requests set `store=false`; retention ultimately depends on the selected provider's policy.

## Codex backend

The default `codex` backend continues to reuse Codex login and its inherited provider. To select a provider already defined in Codex:

```sh
python3 scripts/semantic_status.py provider use existing-provider --backend codex --model exact-model-name
```

To define a provider only for ephemeral Codex classification, use `provider add` with `--protocol responses`, `--env-key ENV_NAME` (or `--codex-auth` for Codex login), then `provider use NAME --backend codex`. Codex classification accepts Responses providers and environment-variable credentials. Key files belong to the direct API backend.

Provider overrides apply only to classifier threads, never global Codex configuration. An explicit provider's model is not checked against the inherited provider's catalog. Runtime model/provider fallback is refused. Doctor cannot verify custom model availability without inference.

## Validation and privacy

Setup, list and doctor do not contact a provider. In API mode, doctor checks local configuration and credential readiness; `connectivity_checked=false` does not confirm endpoint access or model availability. A subsequent Stop event can use the selected endpoint: choose one you trust with the bounded conversation text. `preview THREAD_ID` explicitly performs a budgeted classification without changing the title.

Common secret patterns are redacted from conversation content as a best-effort filter. Provider error bodies, credentials and raw network exception text are not printed or stored in diagnostics. Do not commit key files or publish unredacted configuration/status output. See [security and privacy](../SECURITY.md).
