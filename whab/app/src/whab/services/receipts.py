"""Budget-scoped receipt access over an optional storage capability."""

from whab.domain.errors import StoreError
from whab.domain.receipts import receipt_media_type


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
        if transaction_id is not None and not any(
            row["id"] == transaction_id for row in self.store.transactions(workspace)
        ):
            raise StoreError("Transaction not found.")

    def index(self, workspace):
        with self.lock:
            if not self.supported:
                return {"supported": False, "transactionIds": []}
            self._check(workspace)
            return {"supported": True, "transactionIds": self.store.receipt_ids(workspace)}

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
