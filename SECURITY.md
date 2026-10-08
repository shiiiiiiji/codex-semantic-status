# Security and privacy

The plugin reads local Codex conversation records through app-server and sends a bounded text selection to the user's configured Codex model provider. It reuses the existing Codex authentication; it does not implement another login flow or upload transcripts to the repository owner.

Local runtime storage contains thread IDs, last managed titles, short status summaries, budget records and result codes. It lives outside this repository in the Codex data directory. Diagnostic events contain error types rather than raw exception messages. Common credential patterns are redacted before classification; this is a best-effort filter, not a guarantee that all sensitive information is removed.

Do not include real conversations, access tokens, authentication files or unredacted status output in public issues. To report a security problem, use GitHub's private vulnerability reporting when enabled. If it is unavailable, open a public issue requesting a private contact without including sensitive details.

Review the installed Stop Hook with `/hooks` before trusting it. Installing this package does not automatically grant Hook trust. Disabling the plugin preserves existing titles and usage records. Do not delete the usage ledger to work around a budget limit.

Title writes are checked against the latest content and title, but the title API has no atomic compare-and-set operation. A very narrow manual-rename race remains possible. Semantic completion labels reflect conversation evidence and cannot verify external systems independently.
