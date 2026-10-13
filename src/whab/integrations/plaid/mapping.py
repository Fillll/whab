"""Pure Plaid field normalization and category/transfer suggestions."""

from whab.domain.errors import StoreError
from whab.domain.imports import ImportedAccount, ImportedTransaction, SyncChanges
from whab.domain.money import dollars_to_cents
from whab.domain.validation import validate_date


def transfer_category(transaction: dict) -> bool:
    category = transaction.get("personal_finance_category") or {}
    return (
        category.get("primary") in {"TRANSFER_IN", "TRANSFER_OUT"}
        or category.get("detailed") == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
    )


def suggest_category(transaction: dict) -> str | None:
    category = transaction.get("personal_finance_category") or {}
    primary, detailed = category.get("primary", ""), category.get("detailed", "")
    if transfer_category(transaction) or primary == "INCOME":
        return None
    for keyword, result in [
        ("GROCERIES", "groceries"),
        ("FOOD_AND_DRINK", "dining"),
        ("RENT", "rent"),
        ("UTILITIES", "utilities"),
        ("TRANSPORTATION", "transport"),
        ("MEDICAL", "health"),
        ("ENTERTAINMENT", "fun"),
        ("TRAVEL", "travel"),
        ("GENERAL_MERCHANDISE", "shopping"),
    ]:
        if keyword in primary or keyword in detailed:
            return result
    return None


def normalize_account(bank_account: dict, item_id: str, institution_name: str) -> ImportedAccount:
    balances = bank_account.get("balances") or {}
    if balances.get("iso_currency_code") not in {None, "USD"} or balances.get(
        "unofficial_currency_code"
    ):
        raise StoreError("Only USD bank accounts are supported.")
    value = balances.get("current")
    if value is None and bank_account.get("type") == "depository":
        value = balances.get("available")
    unknown = value is None
    return {
        "id": bank_account["account_id"],
        "name": (bank_account.get("name") or "Bank account")[:200],
        "institution": institution_name,
        "type": bank_account.get("type", "depository"),
        "balance": 0 if unknown else dollars_to_cents(value),
        "currency": "USD",
        "source": "plaid",
        "itemId": item_id,
        "mask": bank_account.get("mask") or "",
        "stale": unknown,
        "balanceUnknown": unknown,
    }


def normalize_transaction(imported: dict) -> ImportedTransaction:
    validate_date(imported["date"])
    inferred = transfer_category(imported)
    return {
        "id": imported["transaction_id"],
        "accountId": imported["account_id"],
        "date": imported["date"],
        "merchant": (imported.get("merchant_name") or imported.get("name") or "Bank transaction")[
            :200
        ],
        "amount": dollars_to_cents(imported["amount"]),
        "suggestedCategoryId": suggest_category(imported),
        "pending": bool(imported.get("pending")),
        "source": "plaid",
        "inferredTransfer": inferred,
        "pendingTransactionId": imported.get("pending_transaction_id"),
    }


def normalize_changes(changes: dict) -> SyncChanges:
    return {
        "added": [normalize_transaction(transaction) for transaction in changes.get("added", [])],
        "modified": [
            normalize_transaction(transaction) for transaction in changes.get("modified", [])
        ],
        "removed": [transaction["transaction_id"] for transaction in changes.get("removed", [])],
    }
