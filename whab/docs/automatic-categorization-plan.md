# WHAB automatic categorization plugin plan

Implementation plan approved October 18, 2026. Numerical defaults and details described as proposed remain open to adjustment.

Build an optional Random Forest Categorization plugin that learns from the current budget's reviewed transactions and suggests categories for future imports. Suggestions remain separate from ledger categories until the user reviews them. Training runs only through an explicit interaction in plugin settings.

**1. Agreed requirements**

- Training, validation, and model testing use only transactions that are already categorized and marked reviewed. An unreviewed transaction is never a labeled example, even if it has a category or an ML suggestion.
- A prediction does not mark a transaction reviewed, assign its actual category, or change budget calculations. The transaction continues to count as uncategorized and needing review until the user acts.
- Prediction runs automatically after successful imports and bank synchronization, using the selected saved model. Review remains manual.
- Training starts only when the user clicks Train in plugin settings and stays in that view until completion or cancellation. There is no scheduled retraining or training that intentionally continues after leaving settings.
- Plugin settings expose editable model parameters, progress, results, saved models, and active-model selection.
- The transaction register displays the proposed category with an ML suggested badge. Reviewing accepts the selected category; the user can change or dismiss the suggestion first.
- Training and prediction run locally to the WHAB instance hosting the budget. Budgets, demo data, and environments remain isolated.

**2. Eligibility and data boundaries**

Create one host-enforced selector for all model-development data. Include only reviewed, categorized, nondeleted, posted transactions with a valid child category and matching account/category currency. Initially exclude transfers and unsupported split transactions. Use the confirmed category as the target label.

Apply this selector before splitting data or learning any vocabulary, feature selection, preprocessing, or statistics. Unreviewed rows are available only as inference candidates. Accepted or corrected ML suggestions become eligible for a later training run only after manual review. Records whose review status has been removed are excluded from the next run; saved models remain immutable historical snapshots until retrained or deleted.

Inference candidates must be nondeleted, posted, uncategorized, and unreviewed. Skip transfers, explicitly dismissed suggestions for unchanged input/model combinations, and unsupported records. Validate eligibility again when saving a suggestion so a concurrent user edit wins. Category and currency checks apply again at acceptance.

Treat training labels as reviewed state recorded in WHAB, including reviewed history imported from another budget. Do not infer review from the mere presence of a category.

**3. First model features**

| Feature group | Inputs | Proposed treatment |
| --- | --- | --- |
| Payee identity | Full payee text | Normalize case, whitespace, and punctuation conservatively. Reduce variable reference noise without stripping meaningful names such as 7 Eleven. Preserve the original transaction text. |
| Payee words | Individual words and adjacent word pairs | Binary presence with a bounded vocabulary learned exclusively from training records. Frequent generic words must not crowd out useful merchant names. |
| Payee fragments | Short character sequences | Evaluate sequences of 3 to 5 characters for inconsistent spelling, spacing, and abbreviations. Cap vocabulary size to bound training cost. |
| Account | Stable account ID, type, subtype | Encode as categorical features, with defined behavior for previously unseen accounts. |
| Amount | Absolute integer minor-unit amount, inflow/outflow/zero indicator | Preserve exact amounts and cents. Let trees learn amount ranges initially rather than impose arbitrary bands. |
| Calendar | Day of month, day of week, month of year, days until month-end | Derive deterministically from the retained transaction date. Do not substitute import time for purchase time. |
| Currency | Account currency | Include as context and restrict output to valid categories of the same currency. Currency compatibility is a core constraint, independent of model behavior. |

The first version excludes transaction time because the current normalized import record retains a date only. It also excludes user notes written during review, transaction IDs as predictors, category names as input features, review flags as predictors, balances, and budget availability. These are unavailable at prediction time, identify records rather than spending behavior, or risk teaching the model from the answer.

Recurrence, payee familiarity, typical amount, and historical categorization consistency are later experiments. They must improve validation results and be calculated from information available before each example; they are not required for the first release.

**4. Training and honest evaluation**

Take an immutable snapshot of eligible reviewed transactions when Train is clicked. Split chronologically by whole transaction dates, with proposed targets of 60% training, 20% validation, and 20% test. Keep the same canonical transaction out of multiple partitions. Record actual counts and date boundaries because date grouping may change the proportions.

Fit vocabularies, encoders, feature selection, and the forest using the training partition only. Use validation results to compare settings and suggestion thresholds. Evaluate the resulting candidate once on the untouched test partition. Test records must not influence feature fitting, parameter selection, or threshold selection. Retain this evaluated model for activation in the first version, without subsequently fitting it on the test records.

Show overall classification accuracy, macro F1, per-category sample counts and results, and suggestion precision and coverage at the selected score cutoff. Precision means the fraction of offered suggestions that are correct; coverage means the fraction of eligible evaluated transactions that receive a suggestion. Include wrong predictions and abstentions rather than silently excluding difficult categories. Identify categories absent from training and report when metrics are unavailable or based on very few examples.

Block training if there are fewer than two training categories or chronological partitions cannot be formed. Explain the missing data requirement without falling back to unreviewed transactions. Treat very small samples as limited evidence. Do not call a raw forest score a calibrated probability of correctness.

Parameter comparisons use the validation partition. Keep the test partition fixed within a comparison session; repeated human tuning against displayed test results must not be presented as an independent final test.

**5. Settings and saved models**

Proposed starting controls:

| Control | Initial value |
| --- | --- |
| Training history | All eligible reviewed history, with optional date limits |
| Number of trees | 300 |
| Maximum tree depth | 20 |
| Minimum examples per leaf | 2 |
| Features considered at a split | Square root of available features |
| Class weighting | None initially; expose balanced weighting under Advanced |
| Reproducibility seed | Fixed and recorded |
| Suggestion score cutoff | 0.80 provisionally; show its measured validation precision and coverage |
| Text processing | Word presence, word pairs, and bounded character fragments |

These values are initial choices to evaluate, not claims of optimal accuracy. Bound expensive values, validate settings before starting, and expose vocabulary limits and feature-group toggles under Advanced.

Settings show eligible sample and category counts before training. Each completed model has a stable ID, editable display name, training timestamp, data counts/date ranges, settings, feature-schema and dependency versions, and evaluation results. Provide Use model and Delete actions, and clearly identify the active model. A failed or canceled run leaves the previous active model intact. Switching models affects subsequent predictions and does not rewrite categories or existing suggestions.

Save preprocessing together with the forest so inference uses exactly the training vocabulary and feature order. Store models as validated, bounded data-only artifacts in the plugin's budget namespace; do not use executable pickle/joblib payloads in portable budgets. Verify prediction parity across save/load. Preserve saved data when the plugin is disabled or unavailable and explain incompatible model versions before requesting retraining.

**6. Training interaction and progress**

The settings view shows Preparing data, Building features, Training trees, Evaluating, and Saving, followed by results. Report actual progress where measurable, such as completed trees; show activity for phases without reliable percentages instead of inventing progress or completion times.

Training is tied to the initiating settings-view session. Prevent navigation within the app until the user finishes or cancels. Provide Cancel; leaving through browser navigation, closing the view, losing the session, or timing out cancels the run and removes incomplete artifacts. A short session lease handles disconnections, and publishing a completed model requires a still-valid session. Bound cancellation checks so work stops promptly. Do not resume an abandoned run automatically.

Keep the UI responsive during this interaction. An internal worker may perform computation, but it must not become a detached training job that continues after the settings session ends. Allow one training run per budget and cap CPU/memory use. Saving a complete model is atomic.

**7. Automatic suggestions and manual acceptance**

After an ingestion operation commits, predict for its eligible new or changed transactions using the active model. Prediction failure must not undo a successful import or corrupt its cursor. Retries must not duplicate suggestions. With no compatible active model, imports still succeed and the plugin reports that training or model selection is needed.

Persist suggestion category, plugin ID, model ID, score, timestamp, and a fingerprint of relevant transaction inputs separately from the actual category. Preserve provenance without overloading the transaction's ingestion source. Revalidate changed payee, amount, account, date, currency, category availability, and pending-to-posted transitions so stale suggestions cannot be accepted.

The register shows the suggested category with an ML suggested badge while the saved category remains empty. On a suggested transaction, choosing a different category changes the selection awaiting review. Review commits that selection and reviewed=true atomically, then removes the pending badge while retaining provenance. Dismiss leaves the transaction uncategorized and unreviewed. Explicitly clearing or dismissing must not immediately regenerate the same suggestion.

Bulk review accepts only the suggestions actually presented for those selected rows, with the same stale-data and currency checks as single-row review. A newer unseen suggestion must not be silently accepted. Review without a suggestion preserves the existing review behavior. Disabling the plugin stops new predictions but does not erase suggestions, provenance, models, or accepted categories.

Proposed convenience action: Suggest for existing transactions applies the active model to the existing eligible backlog. It never trains or marks records reviewed.

**8. Existing import behavior and ownership**

The current [import normalization](../app/src/whab/domain/imports.py) can place a provider's suggested category into the actual category field. Proposed integration: when this plugin is enabled, retain provider hints separately for newly imported transactions lacking an explicit category instead of allowing those hints to preempt ML eligibility or bypass review. Preserve explicit categories from imported budget history and all existing user categories. Leave existing provider behavior unchanged in budgets where this plugin is disabled. Provider hints are not training labels unless a user has reviewed the actual category.

Create `plugins/automation/random-forest-categorization/` as an independent optional distribution named `whab_plugin_random_forest_categorization`. It owns feature extraction, model training, prediction, serialization, settings definitions, documentation, and its tests. Confirm the numerical dependencies install on supported Python 3.14 environments before committing to their versions.

Extend the public SDK with budget-scoped immutable categorization inputs and structured outputs, parameterized settings controls, and session-bound progress/cancellation. The existing plain settings actions do not supply these capabilities. WHAB owns eligibility, suggestion persistence, acceptance commands, import triggers, revision checks, and lifecycle. The Web UI plugin owns settings presentation and the register badge. Storage plugins own migrations and atomic persistence. Core must not import the concrete ML plugin or its dependencies.

Extend the API interface plugin and remote storage client through public commands/queries so review and suggestion behavior do not depend on SQLite internals. Execute categorization once at the budget authority rather than separately on each remote viewer. If a remote host lacks the required capability, report that limitation explicitly. Keep existing HTTPS, origin, and budget-access safeguards.

**9. Implementation sequence after review**

1. Prepare reviewable settings and transaction-register previews, including progress, validation results, model selection, and the suggestion badge.
2. Add core suggestion records, eligibility rules, atomic acceptance, and storage migrations, with contract tests proving budget totals do not change on prediction.
3. Add the public categorization and settings-session contracts, plus local and remote command/query support.
4. Implement the standalone plugin, feature pipeline, reviewed-only chronological evaluation, artifact validation, and model lifecycle.
5. Add settings controls and progress/cancellation behavior, then register rendering, correction, dismissal, and single/bulk review.
6. Connect automatic prediction to ingestion completion, resolve provider-hint precedence, and verify retries and pending-to-posted transitions.
7. Run end-to-end demo checks and required repository validation; update documentation, owned versions, and PR-linked changelogs.

**10. Acceptance and delivery checks**

- Prove unreviewed categorized rows and unreviewed ML suggestions never enter training, vocabulary fitting, validation, or test metrics. Software tests may use such rows as negative fixtures to prove exclusion.
- Prove training, validation, and test records are disjoint and chronological; held-out records cannot change learned preprocessing or selected parameters.
- Prove prediction changes neither actual category, reviewed state, nor budget totals. Prove review saves category and review status in one atomic operation.
- Exercise insufficient history, unfamiliar payees/accounts, rare and deleted categories, multiple currencies, abstention, cancellation, navigation, disconnected settings views, concurrent edits, repeat imports, and failed inference.
- Verify model persistence and predictions survive restart and portable export/import; disabled or missing plugins preserve data; malformed or incompatible models fail safely.
- Test local and remote flows through public contracts. Check demo UI behavior, progress, badge visibility, keyboard interaction, and selected-row bulk acceptance.
- From `whab/app/`, run `.venv/bin/python -m pytest`, `.venv/bin/python -m ruff check src scripts tests plugins conftest.py`, `.venv/bin/python scripts/check_plugin_coverage.py`, `.venv/bin/python scripts/check_distribution.py`, and `.venv/bin/python scripts/check_changelog_prs.py`, plus relevant browser/JavaScript checks. Every affected plugin must retain 100% Python statement and branch coverage.
- Use a feature branch and draft PR. Update each materially changed owner's version and changelog using the real originating PR URL, and pass CI. Final approval and merge remain with the maintainer. Tags and releases require a separate explicit release request.

Technical references: [RandomForestClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestClassifier.html), [text feature extraction](https://scikit-learn.org/stable/modules/feature_extraction.html#text-feature-extraction), [data leakage](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage), and [model persistence](https://scikit-learn.org/stable/model_persistence.html).
