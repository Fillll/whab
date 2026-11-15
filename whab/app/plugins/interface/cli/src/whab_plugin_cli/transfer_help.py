"""Offline transfer guidance, independent of execution imports."""

TRANSFER_HELP = '''Transfer proposals are suggestions, never automatic matches or reviews.
  whab "My budget" trnx transfers --since 2026-11-01 --until 2026-11-30 --format table
  whab "My budget" trnx match-transfer OUT_ID IN_ID --dry-run
  whab "My budget" trnx match-transfer OUT_ID IN_ID --agent Codex
  whab "My budget" trnx unmatch-transfer OUT_ID IN_ID --dry-run

Eligibility: two existing posted, nondeleted, uncategorized transactions in
different active accounts, equal nonzero opposite amounts, same currency AND
on/off-budget scope, within seven calendar days. A date/account filter selects an
anchor; the other leg may lie outside that selection by up to seven days. Names
and bank hints are evidence, not proof. Refunds can look like transfers: inspect
both merchants/accounts/dates/amounts. All ambiguous alternatives stay explicit.
Cross-currency, fees/splits and movements between budget scopes need manual
handling and are not paired. No bank sync or standing future rule is created.

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
remain unreviewed for ordinary transaction review. Notes, category attribution,
amounts, merchants (including whitespace), dates, accounts and identities stay
unchanged. Match sets reciprocal transferPeerId and isTransfer=true. Unmatch
removes links and restores isTransfer from the existing provider hint. Unmatch
before changing paired amounts/dates/accounts/categories or account budget scope.
Provider corrections/removals that invalidate a pair remove both links and leave
both unreviewed; provider hints remain distinct from a confirmed match.

Verify with trnx list (JSON includes transferPeerId) and history list/show. Undo
and redo use the existing fingerprint/revision-guarded history commands. Stale
state is refused. After an uncertain result, inspect the saved pair/history and
retain BOTH original IDs, action, --revision, --fingerprint and --request-id.
Exact successful retries return the original durable result after restart. Never
retry an uncertain write with fresh intent; conflicting request reuse is refused.
Unsupported backends/authorities fail before mutation. Help works offline.
'''
