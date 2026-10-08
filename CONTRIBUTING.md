# Contributing

The runtime uses Python 3.11+ and its standard library. No Node or Python package installation is required.

```sh
python3 -B -m unittest discover -s tests -v
python3 -B scripts/package_release.py
```

Tests must run without Codex, account credentials or cloud inference. Use synthetic conversations and protocol fixtures at the external RPC boundary. Test outcomes such as duplicate spending, stale writes, lost task requirements, and accidentally resumed source threads.

Keep original-thread operations limited to read methods and `thread/name/set`. Classification belongs to a separate ephemeral thread with tools and integrations disabled. Hooks must remain silent, asynchronous and short. Record result codes and error types rather than exception text or conversation content in diagnostic events.

Change public settings carefully. New runtime tables must preserve existing budgets and paused-thread decisions. A worker restart must never refund unknown usage or bypass the cooldown. Input truncation must preserve recent user requirements; incomplete requirements must prevent `completed`.

For a release, update `package.json`, `.codex-plugin/plugin.json` and `.claude-plugin/plugin.json` together, add a changelog entry, then tag the tested commit with the matching `v` version. The release workflow tests and packages that tag. Changes to Hook definitions require another user trust review.

This is a Codex plugin. It relies on experimental app-server APIs and currently supports macOS and Linux. Report runtime incompatibilities with the exact Codex version and redacted outcome codes.
