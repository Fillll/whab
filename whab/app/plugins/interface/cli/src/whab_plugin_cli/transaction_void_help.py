"""Offline guidance for explicit reversible transaction corrections."""

VOID_HELP = '''Reversible transaction corrections (1–100 explicit stable IDs):
  whab "My budget" trnx void TX1 TX2 --dry-run --format table
  whab "My budget" trnx void TX1 TX2 --revision REV --fingerprint HASH --agent Codex
  whab "My budget" trnx unvoid TX1 TX2 --dry-run
  whab "My budget" trnx unvoid TX1 TX2 --agent Codex --batch correction

Use void only for user-confirmed erroneous records. Discover live IDs with trnx
list; transaction names/payees are not identities. Preserve the exact IDs from
the receipt/history for unvoid. Void is reversible exclusion, not physical
deletion, a zero-amount edit, or a fabricated balancing transaction. Unvoid
restores explicitly voided records; it does not revive provider-only deletions.

Every selected record is validated before ONE native atomic commit, revision
advancement, synchronous durable save and permanent exact before/after audit
entry. Empty, oversized, duplicate, missing or disallowed selections reject ALL
changes. Batches are never split. Include both reciprocal transfer legs
explicitly; no counterpart, amount or link is invented. Imported, manual and
zero-value records use the same explicit workflow.

--dry-run returns the exact core before/after records and balanceChanges, plus
the observed revision and fingerprint, without mutation/history/save. Inspect
both records and balances before deciding. Bind that preview with --revision
and --fingerprint; otherwise the CLI previews current state internally. Normal
execution is noninteractive. Stale revisions and changed fingerprints refuse
the complete operation. JSON is complete; tables show every proposed/saved
record and account balance change in integer currency minor units.

Imported corrections preserve provider/imported account balance snapshots;
manual-entry corrections reverse/reapply their account balance effect. Inspect
the explicit balanceChanges rather than assuming voiding changes a bank balance.
Each balance change names its accountType: assets reverse an outflow by adding
its amount; liabilities reverse the amount owed. Unvoid applies the opposite.
Unknown/null snapshots stay unknown when no balance adjustment is needed; a
nonzero manual adjustment requires a known balance and otherwise rejects all.
Monthly assignments remain exact, even if excluding false income reveals a
funding deficit; this command does not silently reallocate envelopes.
No bank sync, guessed reconciliation or future rule is created. Import identity,
source evidence, notes, merchant whitespace and financial fields are retained.
Explicit tombstones prevent refresh/reimport from silently resurrecting a void.
Both void and unvoid leave affected transactions unreviewed. Existing reciprocal
pair links and categories remain intact; both legs must be restored together.
Unvoid sets voided=false as an explicit restoration marker, so a later posted
refresh cannot inherit an old pending tombstone. It refuses a later provider-
deletion marker rather than overriding evidence.

Verify the saved receipt and history list/show. Voided records are excluded
from ordinary trnx list; use unvoid IDS --dry-run to inspect the retained rows.
Unvoid and guarded history undo/redo retain permanent history and must satisfy
current financial/transfer rules. --agent labels the audit actor; --batch groups
history entries and never combines separate commands into one commit.

After an uncertain response, inspect receipt/history and retain the COMPLETE
original intent: ordered IDs, action, --revision, --fingerprint, --request-id,
--agent and --batch. Exact successful retries return the original durable receipt
after restart without duplicate changes/history/saves. Never retry automatically
or replace an uncertain intent with a fresh revision/request ID. Conflicting
request-ID reuse is refused. Unsupported backends fail before mutation.
An HTTP error after submission does not prove that nothing was saved; keep the
original intent and inspect saved state even when the server reports 400 or 409.
Requires CLI 2.24 / WHAB 2.82 and native SQLite 1.30 or JSON 1.22. Current SQLite
journal saves require WHAB 2.83 with SQLite 1.31 or newer; older readers refuse
them. Help is offline.
'''
