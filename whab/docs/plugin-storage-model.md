# Pure storage model helpers

WHAB 2.41 adds the public `whab.plugins.storage_model` module for independent
storage implementations. It exposes the existing financial core through the SDK;
adapters can share those rules without importing a bundled backend or host runtime.
Projects importing this module must require `whab>=2.41,<3`.

The module performs no persistence, provider requests, credential decryption or
runtime selection. Its dictionaries are mutable working state, not a native file
format or a public ledger response. The adapter owns identities, validation at its
boundary, locks, detached candidates, storage versions and atomic publication.

## Available helpers

| Helpers | Purpose |
| --- | --- |
| `commands.add_account`, `update_account`, `add_transaction`, `edit_transaction` | Apply account, money, reference and annotation rules to working state |
| `commands.assign`, `assign_next_month`, `set_rollover_mode` | Apply allocation and rollover rules with an explicit `as_of_month` |
| `initial_state`, `require_pristine` | Construct an empty personal or fictional demo state; check whether restoration may replace it |
| `calculate_workspace`, `calculate_workspace_periods` | Project one coherent working state into ordinary monthly views |
| `create_category`, `public_categories`, `transaction_rows` | Construct two-level categories and produce public category/transaction records |
| `merge_sync` | Merge normalized provider records, pending matches, annotations, receipts and a checkpoint together in a candidate |
| `portable_snapshot`, `restore_snapshot` | Validate and construct sanitized neutral financial records; restoration marks external accounts stale by default |
| `validate_workspace_import` | Validate a public `WorkspaceImport` and return detached records |
| `bank_identifier`, `validate_label`, `validate_month`, `current_month` | Shared bounded identifiers, labels and calendar handling |

`StoreError`, `UNSET`, normalized import records, receipt validation and storage
protocol records remain available from `whab.plugins`. Existing versioned native
evidence/command/transfer helpers live in `whab.plugins.storage_transfer` and
`whab.plugins.storage_replica`.

## An adapter's transaction boundary

Construct a complete independent candidate while holding the adapter's mutation
lock or native transaction. Supply stable record identities and explicit time to
the commands, validate the full candidate, then commit it together with its native
generation and operation receipt. Publish the candidate only after the durable
outcome is established. Discard the entire candidate on a helper failure: helpers
do not provide rollback, and a later validation error may follow earlier changes
within a batch.

```python
from copy import deepcopy
from whab.plugins.storage_model import commands

candidate = deepcopy(current_state)  # inside the adapter's mutation boundary
commands.assign(candidate, "groceries", "2026-10", 1000,
                as_of_month="2026-10")
# Validate and durably commit the complete candidate; then publish it.
```

Amounts are integer currency minor units. The shared rules preserve unlike
currencies, account classifications, reviewed annotations, pending-to-posted
matches, funding and rollover. An adapter must keep personal/demo and separate
budget/environment states independent. `merge_sync` receives normalized records
and an already encrypted provider token in working state; it does not encrypt,
decrypt, contact a provider or create a connection.

Working state is not automatically a complete transfer. Neutral snapshots omit
encrypted provider records and private account associations. Same-device native
transfer uses the complete versioned financial/protected codecs and evidence in
the [storage authority contract](storage-authority.md); receipt and provider state
must move with that validated snapshot. Opaque settings, models and files owned by
other plugins stay in host-managed namespaces, outside this module.

These helpers do not activate a plugin or certify its persistence. An independent
adapter must implement and test every advertised [storage contract](plugins.md#storage-backends),
including primary-role fencing, durable retry receipts and read-only readiness
when it advertises the [native storage worker](storage-workers.md) capability.

Native worker code replacement is independently usable without advertising
`transfer.local` or `storage.replica`. It still requires native authority and
command evidence, read-only readiness, and drainable bank background work.
Migration, copies and promotion separately require their transfer contracts;
passing code readiness does not enable those operations.
