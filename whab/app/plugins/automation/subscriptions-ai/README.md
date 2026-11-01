# AI subscription classification

An optional Automation companion for [Subscriptions](../subscriptions/README.md). Enable **Subscriptions**, **AI credentials**, and **AI subscription classification**, then restart when requested. It requires WHAB 2.52+, Subscriptions 1.3+, and AI credentials 1.x. Manual subscription labels and reports work without this package.

In Subscriptions choose **Classification → AI assisted**, connect a provider through AI credentials, and open **AI classification settings**. Choose a model/effort, review the data permission, and explicitly start the scan. Choose **Manual (no model)** to stop using inference; stop an active job before switching. No inference runs on page reads or bank imports.

This package owns provider validation, prompts, bounded model requests and background jobs. It uses only the AI credentials public API and the Subscriptions public integration API. Profiles, labels and job receipts remain in the original Subscriptions budget namespace through that API, preserving existing budgets without copying credentials or records. Existing consented profiles remain available, but old installations must explicitly enable this new companion to continue AI scans. Installing it does not activate it. Disabling it preserves labels and reports.

The existing local Subscriptions settings transport retains configure/classify/start/job/stop actions when this companion is active. Provider configuration remains local to the budget authority and is not exposed through public feature operations. Each scan processes at most 20 payment patterns per request, saves successful batches, reuses decisions, rejects stale transactions and supports stop/resume while the application stays open. Interrupted jobs never automatically retry paid inference.

AI receives bounded payees, amounts, currencies, billing days, occurrence counts and up to 24 observed months. Account IDs, full dates, categories, notes, balances, receipts and bank credentials are excluded. Scores below 0.8 remain for review; manual labels outrank model results. API usage may incur charges, and ChatGPT subscription use consumes its plan allowance. All tests use fictional data and mocked inference.

## Operation documentation

The packaged [operations.json](src/whab_plugin_subscriptions_ai/operations.json) documents this plugin's public operations, input/output schemas, fictional examples, effects, access, scope, errors and retries. Read it offline with `whab-cli describe --plugin subscriptions-ai`, add `--json` for structured metadata, or use operation-specific `--help` for public CLI commands. Local-only settings and SDK callbacks are explicitly labeled and are not made remotely callable by documentation. Delegated ledger operations are described by `whab-cli describe --plugin core`.
