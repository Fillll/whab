"""Provider-independent records accepted by the atomic ledger importer.

Amounts are integer currency minor units. Provider adapters supply category suggestions,
while user annotations are merged only after the repository locks its ledger.
"""

from typing import NotRequired, TypedDict

from .errors import StoreError
from .money import cents, currency_code
from .validation import validate_date


class ImportedAccount(TypedDict):
    id: str
    name: str
    institution: str
    type: str
    balance: int
    currency: str
    source: str
    itemId: str
    mask: NotRequired[str]
    stale: NotRequired[bool]
    balanceUnknown: NotRequired[bool]


class ImportedTransaction(TypedDict):
    id: str
    accountId: str
    date: str
    merchant: str
    amount: int
    suggestedCategoryId: str | None
    pending: bool
    source: str
    inferredTransfer: bool
    pendingTransactionId: str | None


class SyncChanges(TypedDict):
    added: list[ImportedTransaction]
    modified: list[ImportedTransaction]
    removed: list[str]


def bank_identifier(value: str, *, limit: int = 200) -> str:
    if type(value) is not str or not value or len(value) > limit or "\x00" in value:
        raise StoreError("Invalid normalized bank identifier.")
    return value


def _text(value: str, *, empty: bool = False, limit: int = 200) -> str:
    if type(value) is not str or len(value) > limit or (not empty and not value):
        raise StoreError("Invalid normalized bank record.")
    return value


def _boolean(value: bool) -> bool:
    if type(value) is not bool:
        raise StoreError("Invalid normalized bank record.")
    return value


def validate_imported_transaction(record: ImportedTransaction) -> None:
    try:
        bank_identifier(record["id"])
        bank_identifier(record["accountId"])
        validate_date(record["date"])
        _text(record["merchant"])
        cents(record["amount"])
        _text(record["source"], limit=64)
        _boolean(record["pending"])
        _boolean(record["inferredTransfer"])
        if record["suggestedCategoryId"] is not None:
            bank_identifier(record["suggestedCategoryId"])
        if record["pendingTransactionId"] is not None:
            bank_identifier(record["pendingTransactionId"])
    except (KeyError, TypeError):
        raise StoreError("Invalid normalized bank record.") from None


def imported_account(record: ImportedAccount) -> dict:
    """Validate normalized money and retain only the public account fields."""
    try:
        currency_code(record["currency"])
        bank_identifier(record["id"])
        bank_identifier(record["itemId"], limit=4096)
        _text(record["name"])
        _text(record["institution"])
        _text(record["type"], limit=64)
        _text(record["source"], limit=64)
        _text(record.get("mask", ""), empty=True)
        _boolean(record.get("stale", False))
        _boolean(record.get("balanceUnknown", False))
    except (KeyError, TypeError):
        raise StoreError("Invalid normalized bank record.") from None
    return {
        "id": record["id"],
        "name": record["name"],
        "institution": record["institution"],
        "type": record["type"],
        "balance": cents(record["balance"]),
        "currency": record["currency"],
        "source": record["source"],
        "itemId": record["itemId"],
        "mask": record.get("mask", ""),
        "stale": record.get("stale", False),
        "balanceUnknown": record.get("balanceUnknown", False),
    }


def annotated_transaction(record: ImportedTransaction, annotations: dict) -> dict:
    """Apply the latest user edits to a normalized bank record."""
    validate_imported_transaction(record)
    category = (
        annotations.get("categoryId")
        if annotations.get("categoryEdited")
        else record["suggestedCategoryId"]
    )
    return {
        "id": record["id"],
        "accountId": record["accountId"],
        "date": record["date"],
        "merchant": record["merchant"],
        "amount": cents(record["amount"]),
        "categoryId": category,
        "pending": record["pending"],
        "reviewed": bool(annotations.get("reviewed")),
        "source": record["source"],
        "note": annotations.get("note", ""),
        "isTransfer": False
        if annotations.get("categoryEdited") and category is not None
        else record["inferredTransfer"],
        "inferredTransfer": record["inferredTransfer"],
        "categoryEdited": bool(annotations.get("categoryEdited")),
        "deleted": False,
    }
