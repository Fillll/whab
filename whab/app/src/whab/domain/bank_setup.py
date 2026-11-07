"""Explicit account destinations and import cutoffs over detached ledger state."""

from copy import deepcopy

from .accounts import is_liability
from .errors import StoreError
from .imports import bank_identifier, imported_account, validate_imported_transaction
from .validation import validate_date


def validate_sync_settings(value):
    if type(value) is not dict or len(value) > 500:
        raise StoreError("Invalid bank account sync settings.")
    targets = set()
    for source, row in value.items():
        bank_identifier(source)
        if type(row) is not dict or row.keys() != {"accountId", "since"}:
            raise StoreError("Invalid bank account sync settings.")
        bank_identifier(row["accountId"])
        if row["accountId"] in targets:
            raise StoreError("Choose a different WHAB account for each bank account.")
        targets.add(row["accountId"])
        if row["since"] is not None:
            validate_date(row["since"])
    return deepcopy(value)


def configure_sync(state, item_id, accounts, selection):
    item = state["items"].get(item_id)
    if not item or not item.get("token"):
        raise StoreError("Connection not found.")
    selected = validate_sync_settings(selection)
    available = {row["id"]: imported_account(row) for row in accounts}
    if not selected or selected.keys() - available.keys():
        raise StoreError("Select at least one account from this bank connection.")
    previous = item.get("syncSettings")
    retired = []
    for source, link in selected.items():
        bank = available[source]
        if bank["itemId"] != item_id or bank["source"] != item.get("provider", "plaid"):
            raise StoreError("Bank account belongs to another connection.")
        existing_source = state["accounts"].get(source)
        if existing_source is not None and existing_source.get("itemId") != item_id:
            raise StoreError("Bank account belongs to another connection.")
        target_id = link["accountId"]
        old_id = previous.get(source, {}).get("accountId", source) if previous is not None else source
        if old_id != source and target_id != old_id:
            raise StoreError("This bank account is already linked. Its saved destination cannot be changed.")
        target = state["accounts"].get(target_id)
        if target is None and target_id != source:
            raise StoreError("Destination account not found.")
        if target is not None:
            if target.get("currency", "USD") != bank["currency"]:
                raise StoreError("Bank and destination account currencies must match.")
            if target["type"] != "unclassified" and is_liability(target) != is_liability(bank):
                raise StoreError("Bank and destination account types must both be assets or both be debts.")
            if target.get("itemId") not in (None, item_id):
                raise StoreError("Destination account is linked to another bank connection.")
            if target_id != source and target_id in available:
                raise StoreError("Destination account is already linked to another bank account.")
            if any(other != source and row["accountId"] == target_id
                   for other, row in (previous or {}).items()):
                raise StoreError("Destination account is already linked to another bank account.")
        if target_id != source:
            for transaction in state["transactions"].values():
                if transaction["accountId"] == source:
                    transaction["accountId"] = target_id
                if transaction.get("original", {}).get("accountId") == source:
                    transaction["original"]["accountId"] = target_id
            if source in state["accounts"]:
                del state["accounts"][source]
                retired.append(source)
        state["accounts"][target_id] = {
            **bank, **({key: target[key] for key in ("name", "type", "onBudget", "subtype", "bankWebsite")
                       if key in target and (key == "name" or target["type"] != "unclassified")} if target else {}),
            "id": target_id, "nameOverridden": target is not None,
        }
    # Re-read the original cursor on the next sync, including when the cutoff is
    # moved earlier. Provider transaction identities make replay idempotent.
    item.update(syncSettings=selected, cursor=None)
    return {"configured": True, "retiredAccounts": retired}


def select_sync(state, item_id, changes, accounts):
    """Map provider identities and filter only new rows before the chosen date."""
    item = state['items'][item_id]
    settings = item.get('syncSettings')
    if settings is None:
        return changes, accounts
    mapped = []
    for record in accounts:
        account = imported_account(record)
        if account['itemId'] != item_id or account['source'] != item.get('provider', 'plaid'):
            raise StoreError('Bank account belongs to another connection.')
        if account['id'] in settings:
            mapped.append({**account, 'id': settings[account['id']]['accountId']})
    selected = {'added': [], 'modified': [], 'removed': changes['removed']}
    for kind in ('added', 'modified'):
        for record in changes[kind]:
            validate_imported_transaction(record)
            if record['source'] != item.get('provider', 'plaid'):
                raise StoreError('Bank transaction belongs to another connection.')
            link = settings.get(record['accountId'])
            if link is None:
                continue
            known = record['id'] in state['transactions'] or record.get('pendingTransactionId') in state['transactions']
            if link['since'] is not None and record['date'] < link['since'] and not known:
                continue
            selected[kind].append({**record, 'accountId': link['accountId']})
    return selected, mapped
