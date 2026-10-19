# Automation plugins

Automation plugins provide categorization suggestions and configured workflows through public WHAB capabilities.

- [Random forest categorization](random-forest-categorization/README.md) learns from reviewed transactions and proposes categories for manual review. Training runs only in an explicit plugin settings session.

The `Categorization` SDK descriptor is a budget-scoped engine factory. The host owns eligibility, ingestion triggers, suggestion persistence, and acceptance; model implementations receive selected transaction snapshots and their namespaced files. This category does not imply a general-purpose scheduler.
