---
name: semantic-status
description: Inspect or change the asynchronous semantic conversation title marker, its budget, model, or paused threads. Use only when the user asks about this plugin, its usage, or its settings. Routine marking is done by the asynchronous hook, not by the main conversation.
---

# Semantic Status

Use the Python CLI shipped at `../../scripts/semantic_status.py` relative to this file.
Resolve the absolute installed plugin path before running it. Python 3.11+ is required.
This management skill is not the automatic classifier. Do not call it for ordinary task execution.

Commands:

- `status`: show effective configuration, daily inference/Token usage, paused threads and pending jobs.
- `logs --limit 20`: show recent outcomes and error types without raw exception text.
- `doctor`: check inherited Codex model/runtime, or API configuration and credential readiness; no model inference or API connectivity test.
- `provider setup`: interactive terminal wizard for custom OpenAI-compatible provider/model and hidden credential input. Run in the user's terminal; never ask for the key in chat.
- `provider add NAME --base-url URL --protocol chat|responses --model MODEL --env-key ENV_NAME`: register an API provider; use `--save-key` in a terminal or `--key-file ABSOLUTE_PATH` for file credentials.
- `provider use NAME [--model MODEL] [--backend api|codex]`: select a definition, or an existing Codex provider with explicit model.
- `provider list`: inspect definitions and local credential readiness without printing keys.
- `provider reset`: restore inherited Codex provider and auto model; preserve ledger and pauses.
- `provider remove NAME`: remove an inactive definition; retain credential files.
- `configure --set 'max_tokens_per_day=50000'`: change one or more JSON-valued settings.
- `configure --set 'enabled=false'`: disable automatic inference; retains existing titles and ledger.
- `pause THREAD_ID`: preserve the specified title and stop its automatic updates.
- `resume THREAD_ID`: release the manual-title guard. The next Stop event can classify again.
- `enqueue THREAD_ID`: schedule a background classification; uses the normal budget and debounce.
- `preview THREAD_ID`: make a budgeted classification and show the proposed title without writing it.
- `install`: install the local marketplace package through the official Codex plugin interface.

Use the user's requested scope; never batch-enroll old chats, reset usage records, or trust hooks silently.
Settings are stored separately from Codex configuration in the plugin's data directory.
Provider selection affects only background classification. Never configure an unspecified third-party endpoint,
send conversation content during setup, or print keys. Direct API uses an 8,000-Token reservation and a
512-Token output cap by default; Codex keeps its 20,000-Token reservation. Both share daily totals.
Hook trust is handled by the Codex UI. Only claim that automatic operation is enabled after trust is verified.

Explain that the shared Codex account/provider bears inference costs. Runtime framework and global
AGENTS.md input count toward usage; text truncation does not eliminate that overhead. The Token budget
is admission control, not an exact monetary limit; one in-flight request can exceed its reservation.
Low confidence leaves the existing title untouched. Semantic labels are estimates from the available
conversation evidence; they do not independently verify external code, merges, deployments, or real-world tasks.
