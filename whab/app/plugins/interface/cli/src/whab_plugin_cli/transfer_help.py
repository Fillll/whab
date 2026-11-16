"""Offline transfer guidance, independent of execution imports."""

BULK_TRANSFER_HELP = '''Atomic transfer matching (1–100 explicit pairs):
  whab "My budget" trnx match-transfers --file pairs.json --dry-run
  whab "My budget" trnx match-transfers --file pairs.json --reviewed --dry-run --agent Codex
  whab "My budget" trnx match-transfers --file pairs.json --reviewed --agent Codex
  printf '%s\\n' '[{"outId":"out-1","inId":"in-1"}]' | whab "My budget" trnx match-transfers --file - --dry-run

Complete pairs.json example (UTF-8 JSON array, maximum 2 MiB):
[
  {"outId": "out-1", "inId": "in-1"},
  {"outId": "zero-a", "inId": "zero-b"}
]
Here zero-a/zero-b are two already-existing posted zero entries confirmed by the
user; names do not establish their amounts. Inspect the exact preview.
Use stable IDs from trnx list/transfers. For nonzero pairs, outId is the positive
minor-unit outflow and inId its negative inflow. For two explicitly confirmed
zero-value entries, these are simply the first/second IDs; no direction is inferred.
Each ID may appear exactly once in the whole file.
No fuzzy matching or inferred counterpart. Every pair must pass core validation;
one invalid, missing, repeated, already paired or otherwise disallowed leg rejects
ALL pairs. Oversized batches are refused, never split. Empty batches are refused.
Pairing requires equal opposite amounts (or two explicitly confirmed zero-value
entries) in different same-currency accounts, posted/nondeleted legs within seven
days, and compatible classification. Zero and nonzero pairs may share one batch.
Closed historical accounts and different current on/off-budget scopes may pair
without reopening/reclassifying. Dated transferFunding accounts for signed
eligible budget movement; transfers never become income/spending. Same-month
cash pairs net to zero; cross-month legs affect their posting months. Unknown/
stale cash creates no funding; credit debt changes cannot invent overpayment cash.
Automatic proposals exclude zero-value entries. Explicit matching may pair two
existing zeros, without inventing a counterpart, direction or value. An unpaired
zero hint stays unreviewed until paired or independently classified. Bank hints
and merchant names are not proof; inspect the transfer audit.

This is ONE native atomic commit, revision advancement, durable checkpoint and
coherent reversible before/after audit entry for the complete batch, not repeated
single-pair edits. --batch groups history only. --agent labels the audit actor,
without changing existing category-agent attribution. Omitted --reviewed leaves
both legs unreviewed; --reviewed explicitly reviews every validated complete pair
inside that same commit. No financial fields, notes or merchant whitespace are
changed; no bank sync or persistent future matching rule is created.

--dry-run is read-only and reports exact before/after, revision and fingerprint.
Inspect every pair, then bind the chosen preview with --revision REV and
--fingerprint HASH. Omitting them previews current state internally. Execution
stays noninteractive. A stale revision or changed fingerprint rejects all changes.
JSON is complete; --format table shows every saved before/after record. Verify
trnx list and history list/show. Guarded undo/redo refuses invalid legacy Transfer
labels or invalid reviewed state; undo to safely unclassified, unpaired records remains allowed.

After an uncertain response, inspect saved pairs/history and retain the ENTIRE
reported intent: exact ordered file, --reviewed choice, original --revision,
--fingerprint, --request-id, --agent and --batch. Repeat that exact command only;
successful retries return the original durable receipt after restart, without a
second mutation/history/save. Never retry with fresh intent. Conflicting reuse
is refused. Unsupported authoritative backends fail before mutation. Legacy paired-
transfer portable formats require WHAB 2.81 or newer. Current SQLite journal saves
require WHAB 2.82 with SQLite 1.30 or newer; older readers refuse them. Help works
offline. Use match-transfer for one pair with its existing result shape, or
match-transfers for a batch and optional review in the same commit.
'''


TRANSFER_AUDIT_HELP = '''A confirmed Transfer has exactly two reciprocal, valid legs. A provider hint,
a merchant name or an isolated Transfer category does not prove a pair. A zero
amount imported hint has no inferred direction and is excluded from proposals.
Two explicitly confirmed existing posted zero entries can be paired. An unpaired
zero hint stays unreviewed until paired or classified on independent evidence.

Audit first (read-only):
  whab "My budget" trnx transfer-audit --format table
  whab "My budget" trnx transfer-audit --since 2026-01-01 --until 2026-12-31
  whab "My budget" trnx transfer-audit --offset NEXT --limit 100
The inclusive dates select audited transactions. Inspect total/next and keep the
same revision across pages; next=null means complete. Default limit 100; no silent
truncation. JSON exposes stable IDs, exact saved records and issue reasons.

Repair only explicitly inspected legacy issues:
  whab "My budget" trnx repair-transfers TX1 TX2 --dry-run --agent Codex
  whab "My budget" trnx repair-transfers TX1 TX2 --revision REV --fingerprint HASH --agent Codex
Repair clears invalid reviewed flags, orphan Transfer classification and invalid
links, as shown by the exact core before/after preview. Include every affected
malformed counterpart explicitly. It preserves import hints, identities, amounts,
dates, merchants (including whitespace), notes, account balances and history.
It neither manufactures a missing leg nor guesses a category or counterpart.
Explicit non-Transfer categories are retained; imported hint evidence cannot
override their financial treatment after repair.
After repair, unpaired hints (including zero values) stay unreviewed. Inspect trnx list and
history list/show; use match-transfers only for confirmed complete pairs.
An already unreviewed hint can appear as unchanged in the exact repair receipt.
Audit history is permanent; guarded undo/redo refuses to restore invalid legacy
Transfer labels or invalid reviewed state, including an invalid pre-repair state.

1–200 explicit transaction IDs (up to 100 malformed pairs); all validation precedes one native atomic commit,
revision advancement, durable save and reversible audit entry. One invalid,
missing, duplicate or incomplete repair selection rejects the entire operation.
No automatic upgrade/read cleanup, bank sync or future matching rule. --batch
groups history only; --agent records the audit actor. Normal use is noninteractive.

--dry-run returns exact changes, revision and fingerprint without mutation/save.
Bind a chosen preview with --revision and --fingerprint; otherwise the CLI previews
current state internally. Stale state or changed fingerprints fail safely. After
an uncertain result, inspect saved records/history and keep the complete original
IDs, revision, fingerprint, request ID and actor/batch metadata. Exact successful
retries return the durable original receipt, including after restart, without
duplicated changes/history/saves. Never retry with a new intent. Unsupported
authorities fail before mutation. JSON is complete; tables show every before/after.
All help works offline.
'''


TRANSFER_HELP = '''Transfer proposals are suggestions, never automatic matches or reviews.
  whab "My budget" trnx transfers --since 2026-11-01 --until 2026-11-30 --format table
  whab "My budget" trnx match-transfer OUT_ID IN_ID --dry-run
  whab "My budget" trnx match-transfer OUT_ID IN_ID --agent Codex
  whab "My budget" trnx unmatch-transfer OUT_ID IN_ID --dry-run

Explicit matching: two existing posted, nondeleted transactions with no category
or a Transfer category, in different accounts, equal opposite amounts (including
two explicitly confirmed zero-value entries), same currency, within seven days. Historical closed accounts and differing
current on/off-budget scopes are supported without reopening/reclassifying them.
A date/account filter selects an
anchor; the other leg may lie outside that selection by up to seven days. Names
and bank hints are evidence, not proof. Refunds can look like transfers: inspect
both merchants/accounts/dates/amounts. All ambiguous alternatives stay explicit.
Cross-currency and unequal fee/split movements cannot be paired. Signed eligible
budget movement uses explicit dated transferFunding, not income or spending.
Same-month cash pairs net to zero; cross-month legs affect their posting months.
Stale/unavailable cash cannot create funding. Credit debt uses its
actual change without inventing cash for overpayment. No bank sync or standing
future rule is created. Proposals exclude zero-value entries; explicit matching
can pair two existing zeros without inventing a counterpart, direction or value.

List is read-only. JSON includes complete stable IDs, both public transactions,
account names, evidence, ambiguity, exact total and next offset. Default limit
100; use --offset NEXT with identical filters and require the same revision until
next=null. Over 1,000 candidates fails explicitly: narrow dates/accounts; nothing
is silently truncated. Amounts are integer minor units; currencies stay separate.

Match/unmatch is an explicit confirmation. --dry-run returns exact before/after,
revision and fingerprint without a commit. To bind a reviewed preview, copy its
--revision REV and --fingerprint HASH into the confirmation command. Without
these flags the CLI reads the current exact plan and generates revision/request
machinery internally. Execution is noninteractive, one atomic native commit,
durable checkpoint and reversible audit entry. --agent labels the audit actor;
--batch only groups history. Neither command sets reviewed=true: both records
remain unreviewed for ordinary transaction review. Notes, amounts, merchants
(including whitespace), dates, accounts and identities stay unchanged. Match
preserves category attribution and sets reciprocal transferPeerId/isTransfer=true.
Unmatch removes links, clears any now-orphan Transfer category and its attribution,
and restores isTransfer from the existing provider hint. Unmatch before changing
paired amounts/dates/accounts or choosing Expense, Income or Opening Balance.
A valid pair may assign or clear a Transfer category without unmatching. Current
budget scope or closed status does not destroy a valid historical pair.
Provider corrections/removals that invalidate a pair remove both links and leave
both unreviewed; provider hints remain distinct from a confirmed match.

Verify with trnx list (JSON includes transferPeerId) and history list/show. Undo
and redo use fingerprint/revision-guarded history commands and refuse to restore
invalid legacy Transfer labels or invalid reviewed state. Stale
state is refused. After an uncertain result, inspect the saved pair/history and
retain BOTH original IDs, action, --revision, --fingerprint and --request-id.
Exact successful retries return the original durable result after restart. Never
retry an uncertain write with fresh intent; conflicting request reuse is refused.
Unsupported backends/authorities fail before mutation. Help works offline.
''' + '\n\n' + BULK_TRANSFER_HELP + '\n\n' + TRANSFER_AUDIT_HELP
