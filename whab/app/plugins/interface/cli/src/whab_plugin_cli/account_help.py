"""Offline manual-account help without importing account execution code."""

CREATE_HELP = '''Manual account creation (fictional budget):
  whab "My budget" account create "Épargne 旅行" --type depository --subtype savings --off-budget --currency USD --agent Codex --dry-run
  whab "My budget" account create "Épargne 旅行" --type depository --subtype savings --off-budget --currency USD --agent Codex
  whab "My budget" account show "Épargne 旅行" --format table

Creates an empty manual register with an UNKNOWN opening/current balance, no
opening transaction and no bank connection or sync. Currency, type and exactly
one of --on-budget/--off-budget are required. Savings is depository/savings;
deposit means a certificate/fixed-term deposit (for example deposit/cd).
Names are trimmed; duplicates ignoring case/outer whitespace are refused with
conflicting IDs. Quote spaces, emoji and non-Latin names. No fuzzy matching.

Use trnx create for dated debit/credit history; amounts are signed integer minor
units (USD 2500 means a 25-dollar outflow; USD -2500 means an inflow). recordedActivity
is the signed sum of all nondeleted transactions (including pending), NOT a
verified balance. It is read from the public account snapshot, independent of
account type; an older backend without this projection reports it unavailable.
The raw balance
is an internal accumulator starting at zero, not evidence of zero opening funds.
Unknown balances stay excluded from known assets, debts and cash snapshots.
Later eligible posted transactions affect monthly funding under existing rules;
creation itself adds no funding. Match explicit transfer legs with trnx match-transfer
and then classify/review under existing rules; nothing is automatically reviewed.

--dry-run validates the exact proposed account and observed names without writing,
history, checkpoint, bank connection or sync. It does not reserve a name or ID.
JSON includes the complete intent, expectedRevision and requestId; --format table
shows the same proposal readably. Remove --dry-run for normal noninteractive save.
One guarded add_account commit/save creates the account and permanent audit entry.
--agent labels audit history; --batch groups history, not separate commits.

Revision and request ID are normally automatic. For an EXACT recovery retry,
supply BOTH --revision ORIGINAL_REVISION and --request-id ORIGINAL_REQUEST_ID,
with the same name/classification/currency/agent/batch. A durable successful receipt
returns the original stable ID after a same-runtime restart without another account.
Receipts/revisions are runtime-local: restoring a portable budget into a NEW runtime
preserves account/history, but the old runtime's retry is refused as stale.
Stale revisions, conflicting request-ID reuse and duplicate names fail safely.
No automatic retry. If a response is uncertain, inspect account list/show and
history and retain the reported full original intent; never retry with a new ID
or refreshed revision. Unsupported hosts/backends fail before mutation.
'''
