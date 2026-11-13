"""Offline discovery for atomic bulk workflows."""

BULK_HELP = '''Reviewed requires a category. Categorizing alone never reviews; --clear also clears review.\nUse System / Transfer or System / Income for explicit system classifications,\nwhich never receive envelope assignments. Ordinary review commands:\n  whab "My budget" trnx review TX1 TX2 --agent Codex --dry-run\n  whab "My budget" trnx unreview TX1\n\nAtomic bulk changes (1–100 explicit, distinct targets; never split):
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
Agent labels are self-declared, not authenticated: 1–80 printable characters,
without outer whitespace. --batch accepts 1–200 printable characters without
outer whitespace. Stable IDs and recovery revision/request IDs are 1–200 characters;
copy them exactly. Text must be valid UTF-8 without NUL. Review/note-only edits preserve
category attribution. Financial-detail changes are outside edit-many.

--dry-run performs the same core validation and returns full proposed before/after
records and the observed revision, without mutation, history or save. A preview
does not reserve that revision: use --revision PREVIEW_REVISION to guard a later
execution. JSON is complete; --format table escapes text and shows every diff.

--atomic selects the bulk operation/result even for one target; default single-record
syntax and output stay compatible. Normal execution is noninteractive. Revisions/request IDs are automatic and are
returned with the complete original intent. Optional --revision/--request-id are
for an observed decision or EXACT recovery. Exact successful retries return the
original receipt without another change/history/save, including after restart.
Keep identical resolved IDs, payload, original revision, request ID and actor/batch
metadata. Conflicting request-ID reuse and stale/expired receipts fail safely.
An uncertain response may have committed: inspect records/history and retain the
reported original intent. Never retry automatically with a new revision/ID.
Unsupported backends/authorities fail before mutation. SQLite/JSON are supported.
'''

