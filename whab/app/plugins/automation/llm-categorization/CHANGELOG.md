# LLM categorization plugin changelog

## 1.0.1 — 2026-10-26

PR: [#118](https://github.com/Fillll/whab/pull/118)

- Preserve streamed subscription text when OpenAI completes a response with an empty output list. Retain terminal-success, response-size and strict suggestion validation checks.

## 1.0.0 — 2026-10-26

PR: [#117](https://github.com/Fillll/whab/pull/117)

- Introduce OpenAI subscription sign-in, OpenAI API keys, Anthropic API keys and OpenRouter API keys, with model catalogs and budget-scoped prompt profiles. Keep credentials outside exports, require sending consent and validate suggestions before the host presents them for manual review.
