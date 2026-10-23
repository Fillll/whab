# Random forest categorization plugin changelog

## 1.2.0 — 2026-10-23

PR: [#89](https://github.com/Fillll/whab/pull/89)

- Declare explicit host-process live categorization selection support with a budget-scoped engine factory. Preserve saved models and require unfinished training to finish or be cancelled before changing engines.
- Require WHAB 2.32+; retain reviewed-only training, integer-currency isolation and existing 80% automatic cutoff. Code upgrades remain application restarts until the stateful worker adapter is available; verify owning runtime at 100% statement/branch coverage.

## 1.1.0 — 2026-10-18

PR: [#70](https://github.com/Fillll/whab/pull/70)

- Preview below-cutoff guesses only on explicit request, marking their provenance without retraining or changing the model's saved automatic cutoff.

## 1.0.1 — 2026-10-18

PR: [#69](https://github.com/Fillll/whab/pull/69)

- Save only nonzero category weights at leaves to keep larger default-parameter forests within the 16 MiB limit without changing predictions; continue reading existing dense models. Report oversized models as actionable settings errors and leave the saved model list unchanged.

## 1.0.0 — 2026-10-18

PR: [#68](https://github.com/Fillll/whab/pull/68)

- Train local random forests exclusively on categorized, reviewed transactions, with chronological validation and testing, training-only feature fitting, editable parameters and settings-session progress/cancellation.
- Persist multiple named models as validated data-only JSON, support active model selection, and generate provisional category suggestions automatically after ingestion for manual review.
