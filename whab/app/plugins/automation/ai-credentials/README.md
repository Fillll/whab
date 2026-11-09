# AI credentials

Shared provider connections for [AI transaction categorization](../llm-categorization/README.md) and [AI subscription classification](../subscriptions-ai/README.md). Installed with the supported plugin set, disabled by default. Enable it with either consumer in Settings → Plugins → Installed, then restart when requested. Manual [Subscriptions](../subscriptions/README.md) and ordinary transaction category edits need no AI credentials.

Open **AI credentials → Settings** to connect OpenAI subscription (Continue with ChatGPT), OpenAI API key, Anthropic API key, or OpenRouter API key. A provider connection is shared by the enabled AI features in this budget. Each consumer chooses its own model, effort and data permission. No inference occurs when connecting. Disconnecting a shared provider affects every consumer using that connection.

Keys and OAuth tokens stay in owner-only machine files. Existing `~/.config/whab/llm-categorization/` files and `LLM_CATEGORIZATION_CREDENTIALS_DIR` remain supported without moving or replacing secrets. `AI_CREDENTIALS_DIR` takes precedence when explicitly configured. Keep the directory outside source control and portable budgets. Only opaque UUID connection references are portable; reconnect on another machine. Demo has a separate connection namespace. API usage may incur charges; ChatGPT plan usage consumes its allowance.

The existing fixed HTTPS endpoints, certificate verification, bounded responses, no redirects/proxies/retries, PKCE/OIDC checks and ephemeral loopback OAuth receiver are preserved. Every WHAB route still requires HTTPS and local Host/origin checks. OAuth's one-time native callback serves no financial data. Provider and OAuth implementations, credential storage and cleanup are owned here.

## Public connection API v1

Consumers import only `whab_plugin_ai_credentials.api` inside their plugin process. They declare a named `ai-credentials >=1.0.0,<2` dependency in `plugin.json`, a matching Python distribution dependency, and `Requirement("ai.credentials")` in their descriptor. AI credentials 1.1 requires WHAB 2.54+ and Python 3.15.x. Its manifest explicitly forwards the current and legacy credential-directory settings to its child and declared public API consumers; paths, secrets and existing references remain in place.

`Vault(context)` uses a consumer's own namespace for legacy connection references. `credential(vault, provider, connection_id="")` checks an opaque connection; `complete(vault, provider, model, data, guidance, effort="default", *, instructions, connection_id="")` performs the authenticated request with consumer-owned instructions. `PROVIDERS` and `EFFORTS` describe supported choices; `identifier` and `document` validate legacy portable records. `settings_router(context, route_id="ai-credentials", prompt_rules="")` provides the local settings transport and compatible legacy alias. Provider/OAuth/private modules are implementation details.

`POST /api/plugin-settings/ai-credentials` accepts the workspace and status/save-key/models/connect/auth-status/cancel-sign-in/disconnect actions. Status exposes connection IDs and connected flags, never secrets. The bounded `adopt` action accepts valid opaque provider references from the original categorizer, preserving newer shared connections. Web adopts existing references when opening the categorizer settings; old profiles continue working before adoption. New consumer profiles retain the shared connection ID.

Tests use fictional namespaces and mocked provider responses. Run `.venv/bin/python -m pytest plugins/automation/ai-credentials/tests` from `whab/app/`.

## Operation documentation

The packaged [operations.json](src/whab_plugin_ai_credentials/operations.json) documents this plugin's public operations, input/output schemas, fictional examples, effects, access, scope, errors and retries. Read it offline with `whab-cli describe --plugin ai-credentials`, add `--json` for structured metadata, or use operation-specific `--help` for public CLI commands. Local-only settings and SDK callbacks are explicitly labeled and are not made remotely callable by documentation. Delegated ledger operations are described by `whab-cli describe --plugin core`.
