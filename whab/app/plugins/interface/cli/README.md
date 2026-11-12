# Command line interface

## Agent workflow

Start with `whab --help`, then `whab budget list`. `whab` is the preferred
name-addressed local console; `whab api` exposes the supported advanced commands
using the same local connection. `whab-cli` also supports remote JSON profiles.
These are CLI commands: an agent does not need to construct HTTP requests, read
private runtime files or edit SQLite. Built-in AI plugins are optional.

Check the runtime home and environment before working. Production is the default;
use `--environment sandbox` for experiments. The local console uses the personal
workspace. Advanced queries support an explicit `--workspace`; never mix demo and
personal results. Discover permanent budget IDs with `budget list`; browser slugs
and account names are not advanced `--budget` identifiers. `default` means the
runtime's original default budget, not whichever budget is open in a browser.

```sh
whab --help
whab budget list
whab "My budget" --help
whab "My budget" trnx list --since 2026-08-01 --merchant "Fictional Village" --format table
whab "My budget" trnx uncategorized --month 2026-11 --format table
whab "My budget" trnx categories
whab "My budget" trnx categorize TRANSACTION_ID --category "Food / Groceries" --agent Codex --needs-review --batch UNIQUE_RUN_ID
whab "My budget" history list
```

Replace fictional names, IDs and values with observed results. Read transactions
and category definitions before deciding. Unclear payees should stay uncategorized;
uncategorized reads can include transfers, pending entries, inflows and off-budget
accounts unless filtered. Include an explicit `--agent LABEL` for agent category
changes; the CLI never assumes every caller is an agent. `--needs-review` leaves
the result visible for review. Each CLI edit commits atomically; `--batch` groups
history entries and does not turn separate invocations into one transaction.
Verify the returned budget identity and saved transactions after editing.

Amounts are integer minor units of each currency (USD 1234 is $12.34; JPY 1234 is
¥1234). Never sum unlike currencies. Liability balances use a positive amount owed.
Account `type` and `onBudget` scope are separate; use account-classification help
before changing either. Investment reports preserve missing values as null; reported
cost basis and unrealized gain do not establish realized or total returns.

For advanced writes, the revision is an opaque concurrency guard from the read
used to make the decision, not a history version number. Generate a unique request
ID for each new intent. After a stale-state conflict, read and reconsider. After
an uncertain write, inspect the saved result and retain **all** original parameters,
revision, request ID and actor/batch metadata for an exact retry. Do not automatically
retry with a fresh revision or new request ID. Request receipts have a bounded
lifetime; permanent history has a separate retention contract.

Discover capabilities before using them:

```sh
whab api command edit_transaction --help
whab api describe --offline --json
whab api describe --plugin core --operation history --help
whab api --budget BUDGET_ID query describe
whab api --budget BUDGET_ID query features
```

Help and offline discovery need no credentials, profile or running server. They
describe installed packages; plugin enablement can be unknown. `query describe`
and `query features` read the running addressed budget. `describe` without a remote
URL remains offline even when `whab api` supplies a local home. `local` and `sdk`
documentation entries describe their own access boundaries; they are not arbitrary
names accepted by `command`. Delegated `categorization.ACTION` entries use
`command categorization`, while feature operations use `feature-query` or
`feature-command`. If no documented command supports a task, report the gap.

Success uses stdout, usually a JSON envelope with `budgetId`, `revision` and `data`;
discovery and management responses have their documented shapes. Human tables may
shorten text; use JSON for decisions. A preview also exits 0 and has not committed.
Operational failures print JSON to stderr and exit 1; argument usage errors exit 2.
Keep access tokens in the named environment variable, and preserve HTTPS certificate
verification. Read the sections below for profile precedence and remote access.

## Rename categories and append memos

```sh
whab "My budget" category rename "House / Rent" --name "Main Residence"
whab "My budget" trnx note TRANSACTION_ID --append "Bicycle for Alex"
whab "My budget" category rename --help
whab "My budget" trnx note --help
```

Rename accepts an exact stable child ID, unique case-insensitive name, or
`Group / Category`; ambiguous names include candidates and never select a fuzzy
match. It changes only the existing child's name, keeping its ID, parent, currency,
target, transaction references, monthly assignments and permanent history. New
names are trimmed and duplicate siblings are refused by the shared domain rule.
Use the returned stable ID for recovery after the old name/path stops identifying it.

Memo append accepts a stable nondeleted transaction ID and nonempty text. It keeps
the existing memo exactly, adds one newline when that memo is nonempty, then adds
the supplied text verbatim. An existing trailing newline stays in place, so this
can produce consecutive newlines. Imported YNAB information, merchant whitespace,
amount/date/account/category, review state and existing category-agent attribution
remain unchanged. The final memo must fit the existing 1000-character limit;
overflow is refused without mutation. There is no content-based deduplication.

Both commands read the current revision, create one request ID, use public guarded
commands and verify the saved record. `--format json` (default) retains complete
fields; `--format table` is readable. `--agent LABEL` labels audit history only;
`--batch ID` groups related entries without combining separate writes into one
transaction. Unlabelled commands retain manual User attribution. Neither command
starts bank sync or installs a future categorization rule.

They never retry automatically. The result includes `requestId`, `expectedRevision`,
`savedRevision` and `replayed`. For an exact retry use the same stable record ID,
original name/text, `--revision`, `--request-id`, agent and batch. A successful
append request replay cannot add the text twice, including after a host restart.
Receipts are bounded; an unknown/expired request with its stale original revision
is refused safely. A new request is a new user intent and can deliberately append
the same text again. After an uncertain response inspect `trnx list`/`category list`
and `history list`; retain the complete reported intent rather than using new IDs.

Requires CLI 2.16 / WHAB 2.71; native rename support is SQLite 1.22 or JSON 1.14.
Existing public note-only edits supply memo persistence; no new note storage API
or migration is introduced. Unsupported storage reports an update requirement.

## Atomic bulk edits

CLI 2.17 with WHAB 2.72 adds true atomic native bulk operations.
Discover them through root, transaction/account group, `trnx categorize`,
`trnx edit-many` and `account classify` help, all available offline.

```text
Atomic bulk changes (1–100 explicit, distinct targets; never split):
  whab "My budget" trnx categorize TX1 TX2 --category "Food / Groceries" --agent Codex --dry-run
  whab "My budget" trnx categorize TX1 TX2 --category "Food / Groceries" --agent Codex
  whab "My budget" trnx edit-many --file changes.json --dry-run
  whab "My budget" trnx edit-many --file changes.json --agent Codex --batch cleanup
  cat changes.json | whab "My budget" trnx edit-many --file -
  whab "My budget" account classify "🍏Example Card" "旅行カード" --type credit --on-budget --dry-run

Use a single edit for one decision, multiple IDs for the same values, or edit-many
for different annotations. One bulk operation uses ONE revision, request ID,
atomic native commit, revision advancement and synchronous durable save. --batch
still groups history entries; it does not combine separate commands into a commit.
Every item is validated first. Invalid/missing/deleted/duplicate/ambiguous targets
or disallowed currency/classification combinations reject ALL items. No fuzzy
matching, model inference, bank synchronization or future categorization rule.

Complete changes.json example (fictional; replace IDs with observed identities):
{"edits": [
  {"transactionId": "TX1", "category": "Food / Groceries", "categoryAgent": "Codex", "reviewed": false},
  {"transactionId": "TX2", "categoryId": null, "categoryAgent": null, "note": ""},
  {"transactionId": "TX3", "appendNote": "Exact user wording", "reviewed": true}
]}

File/stdin must contain exactly {"edits": [...]} (max 2 MiB). Transaction IDs
are stable IDs only. category accepts an exact ID, unique name or Group / Name;
categoryId accepts an ID or null to clear. Never supply both. Omitted fields stay
unchanged. note replaces the memo verbatim; "" clears it; null is invalid.
appendNote preserves existing text and adds one newline if nonempty; it cannot
accompany note. Final memos are limited to 1000 characters. reviewed is boolean.
categoryAgent needs a category change; null clears the label. A manual category
change clears its previous label. --agent supplies audit attribution and a default
categoryAgent for category edits; per-item categoryAgent overrides that default.
Agent labels are self-declared, not authenticated. Review/note-only edits preserve
category attribution. Financial-detail changes are outside edit-many.

--dry-run performs the same core validation and returns full proposed before/after
records and the observed revision, without mutation, history or save. A preview
does not reserve that revision: use --revision PREVIEW_REVISION to guard a later
execution. JSON is complete; --format table escapes text and shows every diff.

Normal execution is noninteractive. Revisions/request IDs are automatic and are
returned with the complete original intent. Optional --revision/--request-id are
for an observed decision or EXACT recovery. Exact successful retries return the
original receipt without another change/history/save, including after restart.
Keep identical resolved IDs, payload, original revision, request ID and actor/batch
metadata. Conflicting request-ID reuse and stale/expired receipts fail safely.
An uncertain response may have committed: inspect records/history and retain the
reported original intent. Never retry automatically with a new revision/ID.
Unsupported backends/authorities fail before mutation. SQLite/JSON are supported.
```

A single `trnx categorize ID` and single `account classify ACCOUNT` retain their
existing output and operation. `trnx edit-many` always selects the atomic batch
contract, including one item. A successful execution returns complete `data.changes`
with every public before/after record, `count`, `changed`, budget/revision identity
and the exact original intent. The commit result must equal the validated preview.
An exact replay returns the original receipt, which may describe an earlier revision
than the current ledger; `replayed` makes that distinction explicit. No-op targets
are included in results; history captures only actual financial changes. A successful
new intent still commits/saves once. Missing native support never falls back to a loop.

## Simple console

`whab` provides name-addressed local commands. **Production is the default** for
both `whab` and `whab-cli` from CLI 2.0; use `--environment sandbox` for tests, or
keep an explicit sandbox environment in a profile. Existing profiles and explicit
options retain their selections. The host SDK and developer fixtures keep their
existing sandbox defaults.

```sh
whab --help
whab budget --help
whab budget create --help
whab budget create empty-budget --path "~/budgets"
whab budget list
whab plugin --help
whab empty-budget plugin enable ynab-zip-importer
whab empty-budget plugin status
whab empty-budget insights summary
whab empty-budget trnx non-reviewed
whab empty-budget trnx uncategorized --limit 10 --format table
```

Every level has `--help`, including reports and plugin controls. `transactions`
and `trnx` are aliases; `non-reviewed` and `unreviewed` are aliases. Quotes around
`~` are supported: the CLI expands them itself. `--path` accepts a directory or
an explicit `.whab` file. Without it, creation uses the runtime-managed budget
location. Names containing spaces can be quoted. Names are resolved without
changing the default browser budget; use a permanent ID from `budget list` if
legacy names are ambiguous. New duplicate names and reserved command/workspace
names are refused. The CLI never substitutes another ledger after a failed lookup.

Set a local default profile at `~/.config/whab/default.budget` once:

```json
{"home": "/absolute/path/to/private/runtime"}
```

Home precedence is `--home`, `WHAB_HOME`, the profile's `home`, then `~/.whab`.
Use `--profile FILE` to choose another local JSON profile; its relative home/CA
paths resolve beside that profile. Environment precedence is `--environment`, an
explicit profile environment, then production. `--ca` overrides the profile CA;
otherwise the runtime's verified CA is used. Global options work before or after
subcommands. Help and offline plugin discovery do not load the profile or open a
connection. Remote profiles remain available through `whab-cli`.

Creation uses the authenticated local host, starts an empty SQLite budget without
inheriting optional plugin selections or financial data, and registers its saved
path. Default category templates remain. Existing files are never replaced. The
original host, other budgets, credentials and active browser selection stay owned
by their current runtimes. Unknown, ambiguous, busy or failed operations never
silently target another budget or automatically replay a mutation. After an
uncertain result, inspect `whab budget list` and plugin status before trying again.

`insights summary` reads the shared core's monthly projection (USD by default,
`--currency` selects another ISO currency). Money remains in integer minor units;
no unlike currencies are combined. No AI or bank sync is started. `trnx
non-reviewed` reads unreviewed, non-deleted public transactions, optionally limited
by `--month YYYY-MM`; it never changes review status.

CLI 2.4 adds `whab BUDGET trnx uncategorized` (`transactions` and British-spelled
`uncategorised` are aliases). It reads the full available history by default and
selects rows whose category ID is null, independently of review status. Deleted
rows are excluded. Pending transactions, explicit/inferred transfers, inflows,
zero amounts and off-budget accounts remain included unless explicitly filtered.

```sh
# All uncategorized transactions, counts and account names in JSON.
whab "My budget" trnx uncategorized
# Ten examples without jq; newest dates first.
whab "My budget" trnx uncategorized --limit 10 --format table
# Posted transactions excluding both explicit and inferred transfers.
whab "My budget" trnx uncategorized --posted --exclude-transfers --format table
# One calendar month.
whab "My budget" trnx uncategorized --month 2026-11
```

JSON includes `total` (all filtered matches), `returned` (rows actually shown),
`pending`, `transferFlagged`, `postedNonTransfer`, `bySource`, `byCurrency` and
the complete `data` rows with `accountName`. Pending and transfer counts can
overlap. Filters apply before counts and `--limit`; omitting the limit returns
every match. Dates descend, with IDs breaking ties without claiming an intraday
chronology. JSON retains integer currency minor units and full field text; table
output uses each currency's precision, escapes terminal controls and shortens long
text. No amounts of different currencies are summed.

Only authenticated local public queries are used. The budget name resolves once;
transaction and account-name reads must match its permanent ID and ledger revision
or fail without partial output. No categories, review flags or sync state change.
The response bound is 16 MiB; `--limit` limits output, not the initial history
read, so choose `--month` if a larger budget exceeds that bound.

The existing detailed JSON vocabulary remains discoverable through:

```sh
whab api --help
whab api command edit_transaction --help
whab api describe --plugin ynab-zip-importer --help
```

## Complete transaction search

CLI 2.12 adds `whab BUDGET trnx list` (`transactions list` is an alias). Unlike
the specialized unreviewed/uncategorized reads, it includes **all** nondeleted
transactions: reviewed and unreviewed, categorized and uncategorized, pending and
posted, transfers, refunds, zero amounts, and on/off-budget or archived accounts.

```sh
whab "My budget" trnx list --help
whab "My budget" trnx list --since 2026-08-01 --merchant "Fictional Village" --format table
whab "My budget" trnx list --since 2026-08-01 --until 2026-08-31 --merchant "Fictional Mortgage"
# Repeated merchant options match ANY supplied text (OR).
whab "My budget" trnx list --since 2026-08-01 --merchant "Fictional Village" --merchant "Fictional Mortgage"
```

Date bounds are inclusive and accept real dates in strict `YYYY-MM-DD` form;
omitting either leaves that side unbounded. Merchant matching is a Unicode
case-insensitive **literal substring of the merchant only**. Spaces are preserved;
quote names with spaces/emoji/non-Latin text. There is no regex, wildcard, fuzzy,
synonym or lender inference: another mortgage lender is not Fictional Mortgage.
Omit `--merchant` to include all merchants. Optional `--month YYYY-MM` restricts
the initial public read and intersects the date bounds.

JSON returns `budgetId`, `revision`, effective `filters`, `complete: true`, `total`,
`returned` and `data`. Success means `total == returned == len(data)`; every match
is included, with no limit or hidden pagination. Rows retain all public fields,
including stable IDs, dates, merchant, account ID, amount/currency, category ID,
notes, agent annotation and pending/review status. `accountName`, `categoryName`
and `categoryGroup` come from the same budget and revision. Unavailable account
names fall back to the ID; unavailable/no category names are null, retaining any
category ID. Results sort by descending date then ID, without inventing intraday
times. Tables include full transaction IDs, category, currency and separate review
and posting status; they escape terminal controls and may shorten names. JSON
retains full text and integer currency minor units. Negative amounts are inflows
or refunds; currencies are never combined.

All queries are read-only and revision-checked. An oversized response (16 MiB per
public query), changed revision, foreign budget or transport/storage error fails
explicitly without partial output. SQLite performs coherent storage-side selection through the public bounded read
contract. Other backends retain the complete transactions/accounts/categories
query fallback; use `--month` and inspect every relevant month separately if that
fallback history exceeds the response bound. The initial CLI 2.12 workflow
composed those reads; the integrated performance contract adds native selection.

Inspect **all** results and category definitions before applying a confirmed choice:

```sh
whab "My budget" trnx categories
whab "My budget" trnx categorize TRANSACTION_ID --category "Housing / Fictional home" --agent Codex
whab "My budget" trnx list --since 2026-08-01 --merchant "Fictional Village" --merchant "Fictional Mortgage"
```

A single-ID categorization uses the existing guarded edit; multiple explicit IDs
use the atomic batch described above. Categorization preserves review
unless `--needs-review` is explicit. Re-run the identical list, compare the complete
stable ID set/count and inspect category IDs to verify saved results. Retain the
original revision/request ID/arguments after uncertain edits and inspect before
retrying; never replay with a new intent automatically. Listing never edits,
reviews or syncs, and creates no persistent future categorization rule.

## Simple category edits

CLI 2.6 adds category editing directly under `whab BUDGET trnx` (`transactions`
and British-spelled `categorise` are aliases). No JSON, advanced command syntax,
revision or request ID is needed for an ordinary edit:

```sh
whab "My budget" trnx categories
whab "My budget" trnx categorize TRANSACTION_ID --category "Food / Groceries" --agent Codex --needs-review
whab "My budget" trnx categorize TRANSACTION_ID --category CATEGORY_ID
whab "My budget" trnx categorize TRANSACTION_ID --clear
```

`--category` accepts an exact stable ID, a unique category name or `Group / Name`.
Name matching ignores case and outer whitespace. Ambiguous or missing names fail
before writing. Use the categories list to disambiguate; IDs take precedence.
Core still validates the transaction, currency and category on the addressed budget.

A single-ID invocation resolves the budget once, reads categories and their revision,
and submits one guarded category edit. Multiple explicit IDs submit one atomic bulk
operation; `--dry-run` previews its exact record changes without saving. Success returns `requestId` and
`expectedRevision` with the public result. Agents that already observed a decision
revision may pass `--revision`; `--request-id` retains an explicit intent identity.
There is no automatic write retry or revision refresh. After an uncertain result,
inspect the transaction and keep the original arguments and reported revision and
request ID; only an exact intent can be retried while its receipt remains available.
The existing 16 MiB response bound and verified local HTTPS connection apply.

Review status stays unchanged unless `--needs-review` is supplied. `--agent LABEL`
is explicit opt-in; normal manual category edits/clears remove a previous agent
marker. No bank synchronization or model categorization is started.

## Account archives and certificates of deposit

CLI 2.10 with WHAB 2.64 adds `account close` and `account reopen` for every account
type, independent of classification and budget scope. A CD is `deposit` / `cd`:

```sh
whab "My budget" account classify "Example CD" --type deposit --subtype cd --off-budget
whab "My budget" account close "Example CD"
whab "My budget" account show "Example CD"
whab "My budget" account list --closed --format table
whab "My budget" account list --active
whab "My budget" account reopen "Example CD"
whab "My budget" account close --help
```

Names/IDs use the same exact resolution as classification. List defaults to all
accounts; `--active` and `--closed` are exclusive, and combine with `--unclassified`.
JSON always includes `closed`; tables show State. Classification preserves it.
Each lifecycle command reads the account/revision, submits one guarded
`update_account`, generates one request ID and verifies the saved classification
and lifecycle. No JSON, revisions, request IDs or terminal wizard are needed.
After an uncertain result, inspect show/history and retain the complete reported
intent; do not rerun close/reopen blindly. Older storage without durable lifecycle
support is refused before mutation; SQLite 1.17+ and JSON 1.9+ support it.

Closed accounts are archives: no new manual entries, incoming transaction moves
or bank updates. Historical records, annotations, currency, imported balance
uncertainty, bank mappings and schedule choices remain. Closing neither zeroes nor
verifies a balance, settles debt nor changes budget scope/historical financial
treatment. Known balances still follow that scope in totals/funding; off-budget
CDs never fund envelopes. Other open accounts/shared attachments keep working.
Reopening resumes the saved bank cursor/history choices on the next normal sync,
with stable-ID replay rather than duplicated provider rows; neither command
initiates sync. See [the full lifecycle contract](../../../../docs/architecture.md#account-lifecycle-and-cds).

## Agent category labels

CLI 2.5 with WHAB 2.61 adds an explicit `--agent LABEL` option to
`command edit_transaction`, available through both `whab api` and `whab-cli`.
Normal CLI category changes do not claim an agent. Read the transactions and
categories first, then use their observed revision and a unique request ID:

```sh
whab api --budget BUDGET_ID query transactions
whab api --budget BUDGET_ID query categories
whab api --budget BUDGET_ID command edit_transaction --agent Codex --parameters '{"transactionId":"PAYMENT_ID","categoryId":"CATEGORY_ID","reviewed":false}' --revision OBSERVED_REVISION --request-id UNIQUE_INTENT_ID
# Equivalent detailed interface:
whab-cli --budget BUDGET_ID command edit_transaction --agent Codex --parameters '{"transactionId":"PAYMENT_ID","categoryId":"CATEGORY_ID"}' --revision OBSERVED_REVISION --request-id UNIQUE_INTENT_ID
```

`--agent` requires `categoryId` in the same edit. It saves the optional public
`categoryAgent` field atomically with that category. The same field can be
provided directly in JSON; conflicting labels are rejected. Labels contain
1–80 printable characters without surrounding whitespace. They are self-declared
provenance, not authenticated identities. The transaction's import `source`
(Plaid, YNAB or manual) remains separate.

The Web register shows **Agent · Codex** beside the category. Review, memo and
financial-detail-only edits retain the label; a category change without an agent
label clears it, including selecting the same category manually. Existing review
status is preserved unless explicitly set; include `"reviewed":false` to put an
already-reviewed transaction back in the review queue. Refreshes, posted matches,
reopening and portable saves retain the marker. Older transactions stay unlabelled.
A storage adapter must explicitly advertise durable support; an unsupported
adapter rejects the labelled write instead of silently discarding the marker.

## Simple account classification

CLI 2.9 adds `account list`, `account show` and `account classify` under the
existing name/ID-addressed budget. Every level has detailed offline `--help`.
These commands are noninteractive and use the existing public `accounts` query
and guarded `update_account` operation. No shared API was added.

```sh
whab "My budget" account --help
whab "My budget" account list --unclassified --format table
whab "My budget" account show "🍏Example Card"
whab "My budget" account classify "🍏Example Card" --type credit --subtype credit_card --on-budget
whab "My budget" account classify ACCOUNT_ID --type investment --off-budget
whab "My budget" account classify --help
```

Account IDs take precedence. Names match exactly after ignoring case and outer
whitespace; spaces, emoji and non-Latin characters are supported. Missing or
ambiguous names fail with up to 20 candidate names/IDs; use `account list` and an
exact ID to disambiguate. No fuzzy match is ever selected. Budget resolution uses
the existing catalog name/ID mechanism; it never changes the browser selection.

JSON is the default. List returns the public `budgetId`, `revision` and `data`
array; show/classify return one public account in `data`. Results also include the
addressed `budget` argument and an `unclassified` boolean (type equals `other`).
`--unclassified` filters only by that type, independently of balance verification.
`--format table` shows names, IDs, types, subtypes, scope and unclassified status;
long text is shortened and terminal controls escaped. JSON keeps full public fields.

`--type` uses the existing public update contract: `depository`, `credit`,
`investment`, `retirement`, `property`, `loan` or `other`. `credit` means a card or
line of credit; `other` means unclassified. `--subtype` accepts the domain's
subtype for that type (see classify help). Omitted subtype stays unchanged when
the type stays the same, and clears when the type changes.

`--on-budget` and `--off-budget` are mutually exclusive. **Omitting scope preserves
the existing account's scope**, including an imported off-budget account changed
to credit. Only depository and credit may be on budget. An incompatible preserved
scope is rejected; pass `--off-budget` explicitly when moving an on-budget account
to a tracking type. The public SDK's domain validator rejects invalid combinations
before writing; the host validates them again. Results clearly show saved scope.

Each classification reads the account and revision, generates one unique request
ID, then submits exactly one guarded write. Users supply no JSON, revision or
request ID. A second public account read verifies the saved result and revision
before success is reported. A stale write fails; a failed/mismatched verification
reports an unconfirmed result, which may already have been saved. There are no
automatic write retries or revision refreshes. After an uncertain response, inspect
`account show` and `history list`; **do not rerun classify as a retry**, because that
would create a new intent. Keep the original budget ID and complete intent printed
in the error for reconciliation or an exact advanced retry while its receipt is
available. Advanced operation help documents that contract:

```sh
whab api query accounts --help
whab api command update_account --help
whab api describe --offline --plugin cli --operation account.classify --json
```

Classification preserves account identity, transactions, notes, categories,
reviews, earlier history and bank mappings. It adds the normal classification
history entry and changes financial treatment across the account's full history.
Imported balance estimates remain unverified; no bank sync is requested.

For a conversational review, list unclassified accounts, show one account, ask the
user to confirm its type/subtype/scope, classify only that confirmed choice, verify
with show, then continue. Separate invocations are separate decisions; already
classified accounts are excluded by `--unclassified` and are never bulk modified.
Develop and test with fictional isolated Sandbox budgets, never family data.

## Plugin controls

CLI is an installed management tool, available to all local budgets without
per-budget activation. Plugin status and documentation identify it as a management
tool with `enabled: null`; enabling or disabling it for a budget is rejected.
Primary storage is required: attempts to disable it fail with a nonzero exit code
and a clear explanation, without changing the selection. Use storage settings to
change the primary backend. Web is optional; enabling/disabling it uses the durable
visual-interface transition and preserves ledger access and saved data. An
already-applied optional choice succeeds without starting another transition.

## Local operation

A fresh runtime needs only the host, CLI and storage packages. No AI account, Web
UI, remote API plugin or manually configured bearer token is required. Use a JSON
`default.budget` profile:

```json
{
  "home": "/absolute/path/to/private/runtime",
  "budget": "default",
  "environment": "sandbox"
}
```

```sh
whab-cli default.budget query accounts
whab-cli default.budget plugins list
whab-cli default.budget plugins enable web
whab-cli default.budget plugins disable web
whab-cli default.budget plugins enable plaid
whab-cli default.budget sync plaid
whab-cli default.budget jobs status
whab-cli default.budget jobs enable
whab-cli default.budget jobs run
whab-cli default.budget jobs disable
```

Relative `home` and `ca` paths resolve against the profile directory. Choose
`home` for local operation or `url` for a remote connection. `--home DIRECTORY`
and `--environment sandbox|production` provide the same explicit configuration.
For a saved portable budget, use its permanent UUID from the runtime catalog as
`budget`. Production requires an explicit environment and existing authorized
provider configuration; plugin enablement never creates a bank connection.

The CLI acquires a private authenticated HTTPS lease on the existing host or
starts a temporary one. Startup is serialized per runtime home/environment;
commands retain their original revision/request ID and are never replayed after
transport failure. The host exits shortly after the last lease ends, unless an
enabled visual/API interface or an explicit server launch (`python -m whab`, optionally `--serve`) needs it.
Closing browser tabs does not disable Web. Stateful engines and active operations
retain their owners; pure rendering/import/report/artwork hooks and installed
bank provider clients release their child after each operation. Existing budget
selections and encrypted connections remain intact.

`plugins status` and successful plugin changes return `host_url`, including the
chosen temporary port, so enabling Web also tells you where to open it. Browser
access still requires explicitly trusting that runtime's CA; CLI trust does not
change OS/browser trust.

Plaid declares a 3,600-second incremental sync interval. `jobs enable` installs a
private macOS LaunchAgent that runs due jobs hourly, including without Web UI.
The running host also checks due work; saved due times prevent duplicate runs and
coalesce missed intervals after sleep. `jobs run` runs only due jobs; `sync plaid`
is an explicit immediate sync. Job status records the last attempt/success and a
sanitized failure code. `jobs disable` removes OS wakeups; a running host still
honors enabled providers' intervals. Other operating systems can schedule
`python -m whab --home DIRECTORY --environment sandbox --run-jobs` with their OS
scheduler; automatic registration currently supports macOS. No public webhook
receiver is needed for polling.

Plugin enable/disable applies supported changes live. If preflight reports that a
plugin requires restart, the host validates and saves the requested selection
without replacing the running plugins; the response has `restart_required: true`.
Restart WHAB to apply it. Busy, invalid and failed live transitions remain errors
and never trigger a fallback write.

Local management is personal-workspace only. Commands return JSON and errors are
sanitized. Plugin changes use existing live transitions, including browser edit
draining for visual changes; unsupported live changes report their limitation.
Neither the CLI nor the scheduler reads or writes a database directly.

## File imports

CLI 2.1 adds preview and commit commands for enabled file importers. Start with
`whab BUDGET import --help` and `whab BUDGET import ynab-zip-importer preview --help`.
For the complete ordered YNAB migration, including classification, optional shared
Plaid setup and six-hour updates, use
`whab BUDGET import ynab-zip-importer --help` or
`whab api describe --plugin ynab-zip-importer --operation import.workflow --help`.
Importer/preview/commit help renders the same importer-owned packaged workflow;
other importers may supply their own `local import.workflow` topic. The topic is
documentation, not an executable operation; SDK parser callbacks stay SDK-only.
See the [owning guide](../../ingestion/ynab/README.md#ordered-cli-migration-guide).
For a new empty budget:

```sh
whab budget create empty-budget
whab empty-budget plugin enable ynab-zip-importer
whab empty-budget import ynab-zip-importer preview '~/Downloads/export.zip' --currency USD
whab empty-budget import ynab-zip-importer commit '~/Downloads/export.zip' --currency USD --fingerprint SHA256_FROM_PREVIEW
```

Review the preview's counts, warnings, `canImport`, `reason` and SHA-256
`fingerprint` before committing. Currency confirmation is required; the bundled
YNAB parser accepts USD. ZIP files must be nonempty and at most 10 MiB. Quoted
`~` expands. Use the fingerprint returned by the reviewed preview verbatim.

Commit checks that fingerprint before connecting, obtains a fresh preview and
echoes its parser identity and fingerprint with the identical archive bytes in
the same host lease. A parser change between that preview and commit is refused.
This works even if a temporary headless host exited after the earlier preview.
Names resolve once to a permanent budget identity; other budgets are unchanged.
The CLI uses the existing host import routes and never writes a database directly.

This imports into the addressed pristine personal budget, replacing only untouched
seed categories. Nonempty budgets are refused; financial data is never merged or
overwritten. Preview saves no financial records. Commit atomically saves the
import and its receipt. Neither step creates a second budget or switches the
browser's selected budget. The importer must already be enabled in the target.

No command is automatically retried. After an uncertain commit, preview the same
file in the same budget and inspect `alreadyImported`; an exact same-file commit
retry returns the receipt without duplicating records or overwriting later edits.
A different subsequent archive is refused. See the [YNAB migration limits](../../ingestion/ynab/README.md#migration-limits):
imported accounts need classification and exported balances remain unverified
estimates.

Import validation failures retain the host's bounded authored explanation for
HTTP 400, 409 and 413. Invalid JSON, unexpected response shapes, server crashes
and transport errors remain sanitized. No failed request is automatically replayed.

## Remote operation

The `cli` Interface plugin installs `whab-cli`, a noninteractive JSON terminal interface for people and external agents. It needs no Web interface or AI plugins. Run it against a budget explicitly enabled with the [server interface](../server/README.md). The terminal executable is available after installing this package; enabling its optional server-side registration adds package information only. `python -m whab --serve` explicitly starts a persistent headless service.

Supply an HTTPS origin, stable budget ID, and access token through an environment variable. Use `--ca` for an explicitly selected CA certificate; certificate verification is always enabled. The default token variable is `WHAB_CLI_TOKEN`; `--token-env` selects another. This is ordinary server access, separate from AI credentials.

Save repeated connection options as a UTF-8 JSON object in a file named, for example, `default.budget`:

```json
{
  "url": "https://localhost:5173",
  "budget": "default",
  "ca": "/absolute/path/ca.crt"
}
```

Pass that file before options and the command:

```sh
whab-cli default.budget --help
whab-cli default.budget command edit_transaction --help
whab-cli default.budget describe --offline --json
whab-cli default.budget query transactions
whab-cli default.budget --budget ANOTHER_BUDGET query accounts
```

A remote profile accepts `url`, `budget`, `ca`, `workspace` and `token_env`; a local profile also supports `home` and `environment`. Values are all nonempty strings with the same values as their command-line options. For example, `"workspace": "demo"` selects the demo workspace and `"token_env": "MY_WHAB_TOKEN"` selects the environment variable containing the access token. Unknown or duplicate keys, invalid JSON and incorrect value types are rejected before a request. Explicit command-line options override file defaults. Relative CA paths use the config file's directory; `~` expands to the user's home for the config and CA paths. Files are limited to 64 KiB and are read as data: shell commands and environment substitutions are never executed. Keep the file with private runtime configuration, outside Git. Store tokens in the named environment variable. Configured help and offline discovery need no server or token.

Existing flag-based connection files remain readable for compatibility. New files and help examples use JSON; the `.budget` filename extension is unchanged.

The equivalent explicit connection options remain supported:

```sh
whab-cli --url https://localhost:5173 --budget BUDGET_ID --ca /absolute/path/ca.crt query transactions
whab-cli --url https://localhost:5173 --budget BUDGET_ID --ca /absolute/path/ca.crt query categories
whab-cli --url https://localhost:5173 --budget BUDGET_ID --ca /absolute/path/ca.crt query features
whab-cli --url https://localhost:5173 --budget BUDGET_ID --ca /absolute/path/ca.crt feature-query subscriptions report --parameters '{"month":"2026-11","currency":"USD"}'
```

All responses are JSON envelopes containing `budgetId`, `revision` and `data`. Queries accept `--workspace demo` on the root command (personal is the default). Use `query transactions --month YYYY-MM` to limit the read. `query features` lists the enabled feature query and command names. `feature-query PLUGIN ACTION --parameters JSON` calls their public reads.

Writes require the exact revision used to make the decision and a caller-generated unique request ID:

```sh
whab-cli --url https://localhost:5173 --budget BUDGET_ID command edit_transaction --parameters '{"transactionId":"PAYMENT_ID","categoryId":"CATEGORY_ID"}' --revision OBSERVED_REVISION --request-id UNIQUE_INTENT_ID
whab-cli --url https://localhost:5173 --budget BUDGET_ID feature-command subscriptions set_label --parameters '{"transactionId":"PAYMENT_ID","fingerprint":"OBSERVED_FINGERPRINT","isSubscription":true,"cadence":"monthly"}' --revision OBSERVED_REVISION --request-id ANOTHER_UNIQUE_INTENT_ID
```

Subscription labels apply to matching payments in the same account; see [Subscriptions](../../automation/subscriptions/README.md). Setting a transaction category does not mark it reviewed unless `reviewed: true` is explicitly included. `command OPERATION --parameters JSON` supports the documented public ledger command vocabulary. `feature-command` supports the commands returned by discovery. Use `--parameters -` to read up to 1 MiB from standard input.

The CLI makes one request, never retries a write or refreshes its revision automatically, rejects redirects/proxies and validates response budget identity. Success prints JSON to stdout and exits 0; failure prints a sanitized JSON error to stderr and exits 1. After an uncertain write, retain the original target, parameters, revision and request ID. Inspect the budget and retry only that exact intent; a restart or stale revision requires reconciliation rather than a new write. Request receipts have the host's documented lifetime; the CLI does not claim durable receipt storage.

Install only the host, this plugin, and needed storage/server/feature packages for a model-free environment. The `bundled` extra also installs optional AI packages but does not enable them. Python 3.15.x and WHAB 2.62+ are required.

## Operation documentation

Top-level help lists each plugin ID with its packaged purpose and documented operations.
The YNAB importer uses the CLI identifier **`ynab-zip-importer`** and display name
**YNAB ZIP importer**. For example:

```sh
whab-cli --home /absolute/runtime --budget BUDGET_ID plugins enable ynab-zip-importer
whab-cli describe --plugin ynab-zip-importer --help
```

Help, JSON discovery and plugin listings use this explicit CLI identifier. The CLI
translates it to the existing host identifier `ynab`, preserving saved budgets and
compatibility with existing hosts. The old spelling is still accepted in scripts.
A conflicting installed plugin with the reserved CLI identifier is rejected before
submitting an operation.

Help and packaged documentation work without a server, token, AI account or source checkout:

```sh
whab-cli --help
whab-cli command edit_transaction --help
whab-cli feature-command subscriptions set_label --help
whab-cli describe --plugin subscriptions
whab-cli describe --plugin ai-credentials --kind local --operation save-key --help
whab-cli describe --json
```

Operation help lists input fields, required values, defaults/choices, output fields, examples, scope, access, side effects, errors and retry rules. `describe --json` returns complete JSON Schemas; filter with `--plugin core`, `--kind command` or `--operation edit_transaction`. Offline listings identify installed plugins and explicitly leave their enablement unknown.

To inspect the running authority instead, supply the usual connection arguments:

```sh
whab-cli --url https://localhost:5173 --budget BUDGET_ID --ca /absolute/path/ca.crt describe --json
```

Authenticated online discovery adds current plugin enablement and the running HTTP OpenAPI paths, request parameters/bodies and response schemas. Human-readable `describe` lists HTTP methods and paths. Access rules still apply: local settings and SDK callbacks are described with their actual boundaries. Discovering a local credential action does not make it remotely executable. Add `--offline` to use installed package metadata even with a configured URL. `query describe` returns the same online metadata in the normal revision envelope; `describe` prints only the catalog.

The packaged [operations.json](src/whab_plugin_cli/operations.json) documents this plugin's public operations, input/output schemas, fictional examples, effects, access, scope, errors and retries. Read it offline with `whab-cli describe --plugin cli`, add `--json` for structured metadata, or use operation-specific `--help` for public CLI commands. Local-only settings and SDK callbacks are explicitly labeled and are not made remotely callable by documentation. Delegated ledger operations are described by `whab-cli describe --plugin core`.


## Bank account setup and update policies

CLI 2.3 with WHAB 2.60 also supports `bank investments PROVIDER --item ID`
through both `whab BUDGET` and `whab-cli PROFILE`. Optional `--bank-account`
selects a provider account ID from preview/the report, not a WHAB destination ID.
`--since YYYY-MM-DD` and `--until YYYY-MM-DD` filter inclusive activity history;
defaults are the last two years through today. Positions remain current snapshots.
JSON includes each position's quantity, price/date, value, reported cost basis,
unrealized gain and percentage, and earliest recorded addition when supported.
Money is integer currency minor units, quantities/prices/percentages are decimal
strings, and unknown dates/basis/realized returns are null. Currency totals stay
separate and describe basis coverage. This reads an already active/consented
Investments product without importing trades or changing transaction sync.
`describe --plugin plaid --json` documents `bank.investments` and its limits.

CLI 2.2 with WHAB 2.59 supports `whab BUDGET bank` and `whab-cli PROFILE bank`. Use `connections PROVIDER` to read connection IDs and saved destinations, `preview PROVIDER --item ID` to review bank accounts without importing, and `configure PROVIDER --item ID --accounts JSON` to save account destinations and explicit history choices. The JSON array contains `bankAccountId`, `accountId` (existing same-currency account or explicit null for a new account), and `since` (inclusive YYYY-MM-DD or explicit null for all available history). No history start is inferred. Omitted accounts are not imported. Configuration and synchronization are separate operations: run `bank sync PROVIDER [--item ID]` after reviewing/saving.

Use `bank schedule plaid --every-hours 1`, `--every-hours 3`, `--every-hours 6`, or `--manual`. Omit those flags to read the saved policy and due status. Manual mode disables periodic and webhook-triggered imports while retaining explicit sync. `bank background plaid --enable` / `--disable` controls macOS device/environment wakeups, which check the per-budget policies. Existing `jobs enable`, `jobs disable`, `jobs run` and `sync plaid` remain supported. An hourly cron entry invoking `python -m whab --home /absolute/runtime --environment production --run-jobs` provides the same due-job filtering on other platforms. Bank controls require the local personal budget and preserve HTTPS/authentication; no remote credential or built-in AI is required. See [Plaid setup and limitations](../../ingestion/plaid/README.md#account-destinations-and-transaction-history).

## Financial history

See the [budget history workflow](../../../../docs/usage.md#budget-history).
This version requires WHAB 2.62+. History is saved atomically with financial
changes and retained across reopen and exports. Earlier changes are not backfilled.

```sh
whab "My budget" history --help
whab "My budget" history list --limit 50
whab "My budget" history list --before NEXT_CURSOR
whab "My budget" history show ENTRY_OR_BATCH_ID
whab "My budget" history export baseline
whab "My budget" history snapshot --agent Codex
whab "My budget" history undo ENTRY_OR_BATCH_ID
whab "My budget" history undo ENTRY_OR_BATCH_ID --apply REVIEWED_FINGERPRINT --agent Codex
```

List page sizes are 1–100 (default 50); pass the exact preceding `data.next` cursor
to `--before` until it is null. `show` accepts an entry or batch ID. `export`
returns the historical financial state in JSON `data.snapshot`; it is not a `.whab`
backup and excludes credentials, provider cursors and attachment bytes. `baseline`
is accepted by export and restore. `snapshot` writes a checkpoint immediately.

Undo, redo and restore preview by default. Inspect `data.changes`, then repeat the
same action and target with `--apply` set to the exact `data.fingerprint`. Stale
previews, conflicting later edits and provider/import/attachment barriers fail
without partially applying the action. Later financial dependencies can block
reversal; annotation-only reversal can preserve disjoint later edits. External bank
operations are never replayed. Restore reverses to a financial snapshot as a new
recorded event, rather than deleting history.

After an uncertain apply, inspect history before retrying. Use the reported
`expectedRevision`, `requestId` and `fingerprint` as `--revision`, `--request-id`
and `--apply`, retaining the same action, target and agent label. This also applies
to retrying a snapshot. The advanced equivalent accepts query parameters:

```sh
whab api --budget BUDGET_ID query history --parameters '{"limit":10}'
whab api --budget BUDGET_ID query history_preview --parameters '{"action":"undo","target":"ENTRY_OR_BATCH_ID"}'
```

`--parameters` accepts a JSON object or `-` for stdin, at most 1 MiB. Do not combine
it with `--month`; put an operation's supported fields in the JSON object.

## Shared bank connections and provider account profiles

CLI 2.8 with WHAB 2.63 adds `bank shared PROVIDER` (device connections, safe account
metadata, owning/profile IDs and all budget attachments), `bank attach PROVIDER
--item ID` and `bank detach PROVIDER --item ID`. Attachment makes no provider
request, creates no Item and imports no transactions. Configure destinations and
explicit dates separately, then sync. Detach retains this budget's financial
history and cursor; other attached budgets remain connected. `bank connections`
continues to list only this budget's attachments.

`bank profiles PROVIDER` lists account profile names. `bank profile-save PROVIDER
--profile NAME --credentials-file PRIVATE_JSON_FILE` stores a named provider
account encrypted with the host key. Use `-` for stdin; credentials never belong
in command arguments. Different Plaid accounts use different profiles. Existing
connections retain their original profile; enabling a provider attaches nothing.
See [Plaid profiles and complete examples](../../ingestion/plaid/README.md#shared-connections-and-several-plaid-accounts).

`bank disconnect PROVIDER --item ID --affected-budgets '["budget-a","budget-b"]'`
revokes the Item for every attached budget. Review `bank shared` first and supply
the exact sorted affected IDs. This differs from detaching one budget. Missing or
unavailable budgets and changed attachment sets refuse revocation. The same
commands work through `whab-cli PROFILE bank ...`; `describe --plugin plaid
--operation bank.attach` and operation-specific help document inputs, effects and
recovery. Profiles/attachments are device-local and environment-isolated; bank
consent and reauthentication remain interactive Web operations.

All bank actions support provider-specific offline help, for example:

```sh
whab BUDGET bank attach plaid --help
whab BUDGET bank profile-save plaid --help
whab-cli bank disconnect plaid --help
whab api describe --plugin plaid --help
whab api describe --plugin plaid --operation bank.configure --help
```

Selected-action help includes Plaid's packaged inputs/outputs, runnable commands
for both entry points, permissions, scope, examples, side effects and recovery.
No runtime profile, credentials, enabled plugin or provider request is needed.

## Category management

CLI 2.11+ offers ordinary name-addressed category commands with WHAB 2.65+:

```sh
whab "My budget" category list --format table
whab "My budget" category group create "Projects"
whab "My budget" category move "Finance / Example studio" --parent "Projects"
whab "My budget" category create "Example venture" --parent "Projects"
whab "My budget" category list --parent "Projects" --format table
whab "My budget" category move --help
```

Names match exactly ignoring case and outer whitespace; stable IDs take precedence.
Children also accept `Group / Name`; spaces, emoji and non-Latin names work when
quoted. Missing/ambiguous names show candidates; no fuzzy choice is made. New
parents are real groups and can be empty. Children require an existing `--parent`;
`--currency` defaults to USD and `--target` to zero integer minor units. Duplicate
groups/siblings and invalid/deeper hierarchies are refused. Groups never receive
transactions or assignments. A move changes the parent only and retains the old
group even when empty, plus child ID, target, currency, references and every month's
assignments/history. Existing ledger order is preserved; there is no manual reorder
operation or requested-order guarantee.

Each mutation reads a revision, generates one unique request ID and invokes the
guarded public operation, then verifies the saved hierarchy and revision.
No bank sync is started. Calls are separate atomic commits. After an uncertain
response, inspect `category list` and `history list`; retain the complete reported
original intent and never automatically rerun with a new request identity. JSON is
the default; `--format table` is for readable inspection. Unsupported storage fails
explicitly for group creation/moves; older adapters may still read derived parents.
Detailed offline help for every command renders one maintained workflow from
packaged `category.workflow` documentation; the topic itself is not executable.

CLI 2.13 with WHAB 2.67 adds safe deletion through the same exact names and IDs:

```sh
whab "My budget" category delete --help
whab "My budget" category group delete --help
whab "My budget" category list --parent "Fictional maintenance" --format table
whab "My budget" category delete "Fictional maintenance / Example home"
whab "My budget" category list --parent "Fictional maintenance"
whab "My budget" category group delete "Fictional maintenance"
whab "My budget" category list --format table
whab "My budget" history list
```

Confirm the unused child and its parent before deleting. Child deletion refuses
**every current transaction reference**, including reviewed, pending and deleted
records, and **every nonzero monthly assignment row**. Zero-only assignment rows
are removed atomically with the unused child and retained exactly in audit history.
Groups with
children cannot be deleted. There is no cascade, force flag or automatic financial
reassignment. Deleting an unused child removes its target metadata and retains the
empty parent; delete that parent in a separate command. All unrelated identities,
transactions, notes, review flags, assignments, financial fields, bank mappings
and prior audit history remain intact. No bank synchronization starts.

The CLI obtains the revision and one request ID internally, invokes a public
guarded operation and verifies the complete same-revision hierarchy against the
expected removal. JSON reports the removed record with `deleted: true`, budget,
stable ID and original intent identifiers. Tables label it deleted and verified
absent. Successful deletes are audited atomically; history list/show can inspect
the entry, and history undo/redo use the existing preview/fingerprint guards.
An undo may be refused after incompatible later changes; inspect its preview.
Missing/ambiguous/wrong-kind names never select a fuzzy target. Stale writes fail;
uncertain responses or failed verification must not be blindly replayed. Inspect
category list/history and retain the entire original intent as reported.

Zero-only row removal requires WHAB 2.69+, CLI 2.15+, SQLite 1.21+, JSON 1.13+ or another backend explicitly implementing
the additive `CategoryDeletion` contract with atomic audit history and receipts.
Older adapters fail safely before mutation. Detailed help remains offline.

### Inspect category deletion blockers

```sh
whab "My budget" category show "Fictional maintenance / Example home" --format table
whab "My budget" category show CHILD_ID
```

This read reports the exact child and revision, live and deleted transaction
reference counts, all monthly assignment months, zero-valued assignment months,
and nonzeroCount/nonzeroMonths. An otherwise unused child with only zero rows has
`deletable: true`; its zero rows will be removed by the atomic deletion.
JSON is complete; the table retains the full blocker report. It excludes amounts
and unrelated transaction details. Names/IDs resolve exactly as for deletion.
The read never writes, syncs, appends history or grants permission to replay a write.

An authored HTTP 400 refusal or HTTP 409 revision/intent conflict is definitive;
CLI reports its reason and retains the original intent. A timeout, HTTP 5xx,
lost response or failed verification remains uncertain. Inspect the category and
history; do not replay with a new request ID. A child needs no transaction or
nonzero assignment references; only a parent group requires no children. Native authored
refusals preserve their reasons through the host/storage process boundary.
Unexpected failures remain sanitized and uncertain mutations remain fenced.

Moving transactions does not remove assignment history. Deletion removes zero-only
rows with the child; audit history retains every exact row for guarded undo/redo.
Any nonzero row or transaction refuses the whole operation without removing zero
rows. Undo restores identity, target, parent relationship and every removed row;
redo revalidates later conflicts. Monthly monetary results and rollover semantics
are preserved. There is no nonzero funding erasure or category merge/archive. Both `category show
--help` and deletion help work offline from packaged operation documentation.

## Command performance

Command parsing loads the selected command subtree and reads packaged operation
resources offline. Validation results are cached privately by document content,
plugin identity, validator implementation and installed JSON Schema library metadata. A
changed resource is validated again. Read-only cache locations retain validation.
Parser construction defers unused distribution-version reads; explicit discovery
still reports installed plugin versions. Python 3.15 lazy imports defer SDK and
transport modules until they are used.

Current local hosts expose a coherent filtered transaction read. `trnx list`
selects complete date/merchant matches in storage and returns names with the same
revision. `trnx uncategorized --limit` computes full-match counts while selecting
only the requested sample. Older hosts/backends retain the public-query path.
Unicode literal matching, completeness, default filters and output fields follow
the command contracts above.

Run `.venv/bin/python scripts/benchmark_cli.py --runs 100` from the application
checkout to measure fresh processes with fictional data. It includes process
startup, verified HTTPS, JSON output and synchronous saves, then reopens the saved
budget. Cold host startup, full-history output and ZIP import have separate rows.

The benchmark defaults to 100,000 fictional transactions, 100 repetitions,
100 accounts and 57 categories. It creates its disposable portable fixture using
storage/container APIs and verifies the complete ledger through monthly reads.
The full-history refusal is measured separately when output exceeds 16 MiB.
The ZIP timing uses a separate 100-row import fixture. Latency goals are p95
below 100 ms for help and 250 ms for selective reads and durable individual edits.
The report provides measured values for comparison with those goals.

For size comparisons on macOS/Linux, run
`.venv/bin/python scripts/benchmark_cli_matrix.py --runs 100`. It interleaves
1,000-, 14,550- and 100,000-row budgets on the same host in a reproducible random
order. Every size returns 300 monthly rows, 13 merchant matches and ten
uncategorized samples. It records fresh-process wall time, client CPU time and
system load, with separate help-only samples before and after the workflow.
Activation and initial merchant-index construction are reported separately.
Each size receives distinct real edits, stale-intent checks and exact retries;
the saved files and audit entries are verified after restart and by independent
restoration into empty stores. `--sizes` accepts a comma-separated list up to the
existing row bound; fixture images must still fit the existing 50 MiB limit.
The 250 ms p95 goal concerns warm bounded reads and edits on measured hardware.
Complete unbounded output, cold activation and provider latency require separate
measurements; their work grows with output, stored state or external services.


## Atomic bulk performance evidence

Reproduce the synthetic fresh-process benchmark from the application directory:

```sh
.venv/bin/python scripts/benchmark_atomic_bulk.py --storage sqlite --transactions 100000 --runs 100 --separate-runs 3 --output /tmp/whab-bulk-sqlite.json
.venv/bin/python scripts/benchmark_atomic_bulk.py --storage json --transactions 1000 --runs 100 --separate-runs 3 --output /tmp/whab-bulk-json.json
```

The primary commands are multi-ID `trnx categorize`, mixed `trnx edit-many` and
multi-account `account classify`, each at sizes 1, 10, 50 and 100. `--atomic` selects
the bulk contract for the one-target same-value cases. Each command/size launches
100 fresh CLI processes; equivalent complete separate-command workflows are measured
three times. Their p95 is reported with that small baseline sample count, not as a
100-run percentile estimate. Mixed single-record workflows include their revision
read and guarded advanced edit. Every intent changes real fictional records.

Measurements include startup, verified local HTTPS, output and the synchronous
portable save. A small checkpoint-counter write and a public native evidence query
after each successful checkpoint are included as diagnostic overhead. Provider
latency is excluded: no provider calls or household runtime is used. Cold host/fixture
activation and each first bulk mutation are reported separately from warm p50/p95.
Fresh identical fixtures after at most 2,000 bulk changed records, plus at most
300 baseline edits and one cold batch, bound audit growth;
no history is pruned and no storage limit is raised. Each segment restores its actual
last saved container into an empty backend and checks identities, saved decisions
and complete retained history counts. Native generations and checkpoint counters
must advance once per batch versus once per target for separate commands.

The JSON comparison intentionally uses a smaller populated budget within its
full-file persistence costs and limits; it is not a same-size backend speed comparison.
The script emits raw per-process timings, total batch p50/p95, per-record costs,
workflow savings, commit/checkpoint counts, cold timings and independent restoration
evidence. The final measured tables, target misses and validation evidence are in
[PR #188](https://github.com/Fillll/whab/pull/188). The 250 ms p95 is a measured target,
not a promise for arbitrary hardware, maximum memo sizes, cold work or full-file JSON.
