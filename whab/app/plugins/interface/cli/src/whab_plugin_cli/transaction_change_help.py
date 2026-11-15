"""Offline discovery for general transaction edits, creation and atomic splits."""

CHANGE_HELP = '''General transaction changes (fictional examples):
  whab "My budget" trnx edit TX1 --amount -80000 --agent Codex --dry-run
  whab "My budget" trnx edit TX1 --payee "Fictional employer" --date 2025-04-12 --note "Exact replacement memo" --agent Codex
  whab "My budget" trnx create --account "Fictional checking" --date 2025-04-12 --amount -20000 --currency USD --payee "Fictional lender" --category "Finance / Transfers" --agent Codex --dry-run
  whab "My budget" trnx edit-many --file split.json --agent Codex --dry-run
  cat split.json | whab "My budget" trnx edit-many --file - --agent Codex --revision PREVIEW_REVISION --request-id PREVIEW_REQUEST_ID

Amounts are SIGNED INTEGER MINOR UNITS: positive = outflow, negative = inflow;
USD -20000 means a USD 200 inflow. Zero, decimal amounts and currency/account
mismatches are refused. Dates are YYYY-MM-DD, from 1900-01-01 through 2200-12-31.
Accounts/categories accept stable IDs or unique exact names (account names ignore
case and outer whitespace); categories also accept Group / Category. No fuzzy match.

Complete split.json (fictional; TX1 is an existing USD 1,000 inflow in the same
account, split into USD 800 and USD 200; substitute observed IDs/names):
{"preserveBalances": true,
 "edits": [{"transactionId": "TX1", "amount": -80000}],
 "creates": [{"account": "Fictional checking", "date": "2025-04-12",
              "merchant": "Fictional lender", "amount": -20000, "currency": "USD",
              "category": "Finance / Transfers", "note": "Preserved fictional provenance"}]}

This edits the existing record and creates an ordinary manual transaction in ONE
native commit, revision advancement, durable save and reversible audit entry.
1–100 total edits plus creates; never split. Invalid, missing, deleted, duplicate,
ambiguous or disallowed items reject the ENTIRE batch. Matched-transfer protections
apply. No automatic transfer pairing, reconciliation, bank sync or future rule.

edit preserves every omitted field, including exact existing merchant whitespace,
notes and imported/provider identity. Financial corrections retain source evidence
in original/audit metadata and leave affected transactions unreviewed. New records
are manual and unreviewed. Category assignment alone does not review a transaction.
Imported account balances remain provider/import snapshots. Normal imported
corrections retain those snapshots; ordinary manual creates update account balances.
For a split, use preserveBalances:true or --preserve-balances: the entire plan must
have exactly zero net signed amount change PER ACCOUNT/CURRENCY, and its saved
account balance snapshots stay byte-for-byte unchanged. A nonconserving plan is
refused; the preview includes balanceChanges even when before and after are equal.
Future provider refreshes retain corrected fields and original source evidence;
manual creations are separate records, not duplicate bank imports or transfer peers.

create requires account, date, amount, currency and payee; category and note are
optional. --payee and --merchant are aliases; --note and --memo are aliases.
--clear-category explicitly clears an edit's category. In JSON, account resolves a
name/ID; accountId accepts an ID, and category resolves a name/path/ID; categoryId
accepts an ID or null to clear. Never provide both forms. Omitted edit fields stay
unchanged; note replaces exactly, "" clears and null is invalid. appendNote adds
one newline to a nonempty existing memo; it cannot accompany note. Memos have a
1000-character limit. --agent records audit attribution and defaults categoryAgent
only on explicit category choices. --batch groups history; it does not merge saves.

--dry-run is read-only and returns every before/after record, proposed creation ID,
observed revision and complete intent. Use BOTH returned --revision and --request-id
to commit that exact preview. Creation IDs are derived from request ID and position;
an optional creationId UUID in JSON fixes identity explicitly. Preserve array order.
Exact retry after restart uses the same resolved payload, revision, request ID and
agent/batch metadata; it returns the original receipt without duplicate creation,
history or save. Conflicting request reuse and stale revisions fail safely. Never
replay an uncertain result as a new intent. No automatic retries are performed.

JSON is complete; --format table shows every changed field and full new records.
Success returns the saved records and stable createdIds. Existing annotation-only
edit-many/categorize/note syntax and output remain compatible. SQLite and JSON
support atomic changes; unsupported storage/remote authorities fail before mutation.
Normal execution is noninteractive. Inspect trnx list and history list after saving;
use guarded history preview/apply undo or redo for the entire atomic change.
'''
