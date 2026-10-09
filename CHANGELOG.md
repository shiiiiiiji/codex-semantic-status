# Changelog

## 1.2.0 — 2026-10-09

- Add custom provider/model commands and a terminal setup wizard with hidden key input.
- Support direct OpenAI-compatible Chat Completions and Responses, avoiding Codex framework input overhead.
- Add environment-variable and private key-file references without storing inline credentials in configuration.
- Select existing or classifier-only Codex Responses providers without changing source-thread or global settings.
- Add API-specific reservations and output caps while preserving shared daily usage and pause records.
- Bound inference with a child-process deadline; refuse redirects, retries, tools and model fallback.
- Add local HTTP integration coverage for request limits, usage, malformed outputs, errors, redirects and timeout.

## 1.1.0 — 2026-10-08

- Prepare the public GitHub distribution with English and Chinese documentation, MIT licensing and contributor guidance.
- Add offline macOS/Linux CI, version-tag release packaging and issue templates.
- Skip app-server startup when the budget cannot admit another request.
- Make unused status/log queries read-only and configuration writes atomic.
- Support advertised nano/mini models when a luna model is unavailable; preserve explicit-model fail-closed behavior.
- Add bounded diagnostic events containing result codes and error types.
- Add an explicit local-source relocation option and preserve existing budget storage.
- Keep personal paths, transcript identifiers and installation trust records out of source and archives.
- Preserve original-requirement truncation guards across consecutive cached classifications.

## 1.0.1 — 2026-10-08

- Add semantic status classification with silent asynchronous Stop hooks.
- Add durable queue, Token/call admission budgets, cooldown and stale-result protection.
- Preserve manual titles, uncertain results and complete user requirements within the input budget.
- Account for observed Token usage even on inference failure.
- Prefer the desktop-bundled Codex runtime and compatible plugin manifest.
