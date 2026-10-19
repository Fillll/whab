"""Eligibility and stale-input checks for category suggestions, without model dependencies."""

import hashlib
import json


def candidates(transactions, accounts, categories, *, reviewed):
    accounts = {row["id"]: row for row in accounts}
    categories = {row["id"]: row for row in categories if row.get("parentId") or row.get("group")}
    result = []
    for row in transactions:
        account = accounts.get(row["accountId"])
        if not account or row.get("deleted") or row.get("pending") or row.get("isTransfer") or row.get("inferredTransfer") or row.get("splits"):
            continue
        if row.get("reviewed") is not reviewed:
            continue
        category = categories.get(row.get("categoryId"))
        if reviewed and (not category or category.get("currency", "USD") != account.get("currency", "USD")):
            continue
        if not reviewed and row.get("categoryId") is not None:
            continue
        result.append({**row, "currency": account.get("currency", "USD"),
                       "accountType": account["type"], "accountSubtype": account.get("subtype") or ""})
    return result


def fingerprint(row):
    fields = ("id", "accountId", "merchant", "amount", "date", "currency", "accountType", "accountSubtype")
    return hashlib.sha256(json.dumps([row[key] for key in fields], separators=(",", ":")).encode()).hexdigest()
