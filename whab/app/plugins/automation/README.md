# Automation plugins

Automation plugins provide categorization suggestions and configured workflows through public WHAB capabilities.

- [Random forest categorization](random-forest-categorization/README.md) learns from reviewed transactions and proposes categories for manual review. Training runs only in an explicit plugin settings session.
- [Subscriptions](subscriptions/README.md) provides manual labels, payment matching and cost reports without AI. Its separate optional [AI companion](subscriptions-ai/README.md) supplies model-based classification through [AI credentials](ai-credentials/README.md).

The `Categorization` SDK descriptor is a budget-scoped engine factory. The host owns eligibility, ingestion triggers, suggestion persistence, and acceptance; model implementations receive selected transaction snapshots and their namespaced files. This category does not imply a general-purpose scheduler.
