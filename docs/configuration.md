# Configuration and troubleshooting

Commands below run from the repository root. They also work from an installed copy when invoked by absolute script path. Settings belong to this plugin and do not change the source conversation's model.

## Settings

Use `configure --set KEY=JSON` with one or more entries. Values are validated before an atomic file replacement.

| Key | Default | Purpose |
| --- | --- | --- |
| enabled | true | Admit automatic background work |
| model | "auto" | Advertised model name, or luna/nano/mini auto selection |
| debounce_seconds | 60 | Coalesce recent events |
| min_interval_seconds | 300 | Minimum interval between a thread's paid attempts |
| max_calls_per_day | 20 | Daily global attempt cap |
| max_calls_per_thread_per_day | 6 | Daily attempt cap per thread |
| max_tokens_per_day | 50000 | New-request Token admission budget |
| token_reservation | 20000 | Conservative reservation before an attempt |
| max_input_chars | 6000 | Conversation JSON limit, excluding framework instructions |
| classification_timeout_seconds | 45 | Classifier turn timeout |
| confidence_threshold | 0.8 | General title-change threshold |
| completion_threshold | 0.95 | Completion threshold; cannot be lower than the general threshold |
| timezone | "Asia/Shanghai" | IANA timezone used for daily usage accounting |
| codex_command | "auto" | Executable path, or desktop/PATH detection |
| autoUpdate | false | Metadata default; the plugin does not self-update |

Set a zero daily cap to stop new paid requests. Existing usage is preserved when settings change.

```sh
python3 scripts/semantic_status.py configure --set 'model="gpt-6-luna"'
python3 scripts/semantic_status.py configure --set 'timezone="UTC"'
python3 scripts/semantic_status.py configure --set 'enabled=true' --set 'max_calls_per_day=10'
```

## Storage and overrides

Default storage is `$CODEX_HOME/plugins/data/codex-semantic-status`, or `~/.codex/plugins/data/codex-semantic-status` when CODEX_HOME is unset. It contains config.json, a SQLite queue/ledger, and lock files. It must remain outside the repository. PLUGIN_DATA is intentionally not used, so Hooks and management commands agree on the location.

Use `--data-dir /absolute/path` for an isolated management command. Use SEMANTIC_STATUS_DATA for a Hook/worker isolation override; workers inherit it. SEMANTIC_STATUS_PYTHON selects Python for the shell wrapper. CODEX_SEMANTIC_STATUS_CODEX selects a Codex executable before automatic discovery.

Do not delete the usage ledger to reset a budget. The ledger and paused-thread state survive version changes.

## Diagnose without inference

```sh
python3 scripts/semantic_status.py doctor
python3 scripts/semantic_status.py status
python3 scripts/semantic_status.py logs --limit 20
```

Doctor checks the runtime and model list without a model turn. Status and logs do not create a database when the plugin has never run. Logs show outcome codes and error types, not raw exception text or transcripts.

| Outcome | Meaning and action |
| --- | --- |
| budget | No new call can be admitted. Check the daily caps and Token totals. |
| unchanged | Conversation evidence matches the last classified input. |
| active_or_empty | The stored turn is active or usable evidence is unavailable. A later completed turn can trigger another event. |
| uncertain | The model result failed confidence, schema, or complete-requirements checks. The existing title is retained. |
| manual_title | The title changed outside the worker; automatic updates paused. Use resume only if you want to release that guard. |
| protected_title | The title is empty or has a custom emoji prefix. |
| stale | Content changed during inference; retry is deferred under the cooldown. |
| classification_failed | Inference did not produce an accepted transport result. Check error type, model availability and provider access. |
| worker_error | Background setup or RPC reading failed. Run doctor; verify the Codex executable and runtime version. |

If no events appear, confirm the plugin is enabled and its exact Stop Hook is trusted in CLI `/hooks`. Reopen the source conversation to load new configuration. Hook definition changes can invalidate trust. Installation alone does not grant it.

If the local source was moved, `install --replace-source` explicitly relocates this plugin's marketplace entry. It leaves other marketplaces and source-conversation settings alone.

## Disable and remove

Use `configure --set 'enabled=false'` to stop new work. It keeps existing titles, usage and pause records. Disable or uninstall Semantic Status in Codex's plugin manager to remove the integration. Neither operation rewrites historical titles.
