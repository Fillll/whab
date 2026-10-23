"""YNAB export adapter using the public, credential-free file import capability."""

from pathlib import Path

from whab.plugins import FileImport, Plugin, PluginContract, StoreError, WorkspaceImport

from .parser import parse_dump


def workspace_import(raw: bytes) -> WorkspaceImport:
    dump = parse_dump(raw)
    transactions = []
    for row in dump.transactions:
        # WHAB has no independent flag/reconciliation fields. Keep source status
        # alongside the original memo rather than silently discarding it.
        note = row.memo
        metadata = f"YNAB: {row.cleared}"
        if row.flag:
            metadata += f"; flag {row.flag}"
        if row.is_split:
            metadata += "; split row"
        note = f"{note}\n[{metadata}]" if note else f"[{metadata}]"
        if len(row.payee) > 200:
            note += f"\nOriginal YNAB payee: {row.payee}"
        if len(note) > 1000:
            raise StoreError("An exported memo with its YNAB status exceeds WHAB's 1,000 character limit.")
        transactions.append({
            "id": row.id,
            "accountId": row.account_id,
            "date": row.date,
            "merchant": row.payee[:200] or "(No payee)",
            "amount": row.amount_cents,
            "categoryId": row.category_id,
            # YNAB uncleared means unverified, not a bank authorization hold.
            "pending": False,
            "reviewed": row.cleared in {"cleared", "reconciled"},
            "source": "ynab",
            "note": note,
            "isTransfer": row.is_transfer,
            "inferredTransfer": row.is_transfer,
            "categoryEdited": True,
            "deleted": False,
        })
    months = [row.month for row in dump.assignments]
    months.extend(row.date[:7] for row in dump.transactions)
    if not months:
        raise StoreError("YNAB export contains no dated budget assignments or transactions.")
    return WorkspaceImport(
        accounts=tuple({
            "id": row.id, "name": row.name, "institution": "YNAB export",
            "type": "other", "balance": row.balance_cents, "source": "ynab",
            "onBudget": False, "subtype": None,
            "stale": True, "balanceUnknown": True,
        } for row in dump.accounts),
        categories=tuple({
            "id": row.id, "name": row.name, "group": row.group, "target": 0,
        } for row in dump.categories),
        transactions=tuple(transactions),
        assignments=tuple({
            "month": row.month, "categoryId": row.category_id, "assigned": row.assigned_cents,
        } for row in dump.assignments),
        budget_start_month=min(months),
        warnings=(*dump.warnings,
            "Accounts have no exported type, budget scope or verified balance snapshot. They are imported as unclassified off-budget accounts with unavailable balances and provide no budget funding.",
            "Monthly assignments are preserved. WHAB recalculates activity and availability using its own rules; YNAB credit-card payment envelopes and rollover totals may differ.",
            "YNAB flags and clearing status are retained in transaction notes. Uncleared rows are imported as unreviewed, posted transactions.",
            "Payee labels longer than 200 characters are shortened for display and preserved in full in transaction notes.",
            "Each import creates a separate budget. It cannot reconcile overlapping bank feeds or merge an existing ledger.",
        ),
    )


_ROOT = Path(__file__).parent
plugin = Plugin(
    id="ynab", display_name="Import from YNAB ZIP file",
    file_import=FileImport(
        parse=workspace_import, templates=_ROOT / "templates", static=_ROOT / "static",
    ),
    contract=PluginContract(lifecycle="live"),
)
