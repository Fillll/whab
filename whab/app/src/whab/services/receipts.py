"""Budget-scoped receipt access over an optional storage capability."""

from whab.domain.errors import StoreError
from whab.domain.receipts import receipt_media_type, validate_receipt_selection


class ReceiptService:
    def __init__(self, store, lock):
        self.store, self.lock = store, lock
        self.supported = all(callable(getattr(store, name, None)) for name in (
            "receipt_ids", "get_receipt", "save_receipt", "delete_receipt",
        ))

    def _check(self, workspace, transaction_id=None):
        if type(workspace) is not str or workspace not in {"personal", "demo"}:
            raise StoreError("Invalid workspace.")
        if not self.supported:
            raise StoreError("This storage plugin does not support receipt images.")
        if transaction_id is not None:
            lookup = getattr(self.store, "receipt_transaction_exists", None)
            if callable(lookup):
                found = lookup(workspace, transaction_id)
                if type(found) is not bool:
                    raise StoreError("Storage returned an invalid receipt transaction lookup.")
                # Let storage check workspace metadata before a malformed ID,
                # matching the legacy full-history read's error precedence.
                found = found and isinstance(transaction_id, str)
            else:
                found = any(row["id"] == transaction_id for row in self.store.transactions(workspace))
            if not found:
                raise StoreError("Transaction not found.")

    def index(self, workspace):
        with self.lock:
            if not self.supported:
                return {"supported": False, "transactionIds": []}
            self._check(workspace)
            return {"supported": True, "transactionIds": self.store.receipt_ids(workspace)}

    def index_for(self, workspace, transaction_ids):
        """Return receipt markers only for the bounded caller-selected identities."""
        with self.lock:
            if type(workspace) is not str or workspace not in {"personal", "demo"}:
                raise StoreError("Invalid workspace.")
            selected = validate_receipt_selection(transaction_ids)
            if not self.supported:
                return {"supported": False, "transactionIds": []}
            read = getattr(self.store, "receipt_ids_for", None)
            allowed = set(selected)
            if callable(read):
                result = read(workspace, selected)
                if (
                    type(result) is not list or len(result) > len(selected)
                    or any(type(value) is not str or value not in allowed for value in result)
                    or len(set(result)) != len(result)
                ):
                    raise StoreError("Storage returned an invalid receipt selection index.")
            else:
                full = self.store.receipt_ids(workspace)
                if type(full) is not list or any(type(value) is not str for value in full):
                    raise StoreError("Storage returned an invalid receipt selection index.")
                result = allowed.intersection(full)
            return {"supported": True, "transactionIds": sorted(result)}

    def get(self, workspace, transaction_id):
        with self.lock:
            self._check(workspace, transaction_id)
            raw = self.store.get_receipt(workspace, transaction_id)
            return None if raw is None else (raw, receipt_media_type(raw))

    def save(self, workspace, transaction_id, raw, media_type):
        actual = receipt_media_type(raw)
        if actual != media_type:
            raise StoreError("The receipt image does not match its file type.")
        with self.lock:
            self._check(workspace, transaction_id)
            self.store.save_receipt(workspace, transaction_id, raw)

    def delete(self, workspace, transaction_id):
        with self.lock:
            self._check(workspace, transaction_id)
            self.store.delete_receipt(workspace, transaction_id)
