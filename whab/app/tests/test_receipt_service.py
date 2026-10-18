"""Scoped optional receipt lookups retain legacy isolation and validation order."""

import pytest

from tests.receipt_images import png
from whab.domain.errors import StoreError
from whab.services.receipts import ReceiptService


class ScopeLock:
    def __init__(self):
        self.active = False
        self.entries = 0

    def __enter__(self):
        assert not self.active
        self.active = True
        self.entries += 1

    def __exit__(self, *error):
        self.active = False


class LegacyReceipts:
    def __init__(self, lock):
        self.lock = lock
        self.events = []
        self.metadata = {"personal", "demo"}
        self.rows = {
            "personal": {
                "active": {"deleted": False}, "pending": {"deleted": False, "pending": True},
                "deleted": {"deleted": True}, "1": {"deleted": False},
                "disconnected": {"deleted": False, "accountActive": False},
            },
            "demo": {"foreign": {"deleted": False}},
        }
        self.images = {}

    def record(self, operation, workspace, transaction_id=None):
        assert self.lock.active
        self.events.append((operation, workspace, transaction_id))

    def require_metadata(self, workspace):
        if workspace not in self.metadata:
            raise StoreError("Workspace not found.")

    def active(self, workspace, transaction_id):
        return (isinstance(transaction_id, str) and transaction_id in self.rows[workspace]
                and not self.rows[workspace][transaction_id]["deleted"])

    def require_transaction(self, workspace, transaction_id):
        if not self.active(workspace, transaction_id):
            raise StoreError("Transaction not found.")

    def lookup(self, workspace, transaction_id):
        self.record("lookup", workspace, transaction_id)
        self.require_metadata(workspace)
        return self.active(workspace, transaction_id)

    def transactions(self, workspace):
        self.record("transactions", workspace)
        self.require_metadata(workspace)
        return [{"id": key} for key, row in self.rows[workspace].items() if not row["deleted"]]

    def receipt_ids(self, workspace):
        self.record("ids", workspace)
        return sorted(key for scope, key in self.images if scope == workspace)

    def get_receipt(self, workspace, transaction_id):
        self.record("get", workspace, transaction_id)
        self.require_transaction(workspace, transaction_id)
        return self.images.get((workspace, transaction_id))

    def save_receipt(self, workspace, transaction_id, raw):
        self.record("save", workspace, transaction_id)
        self.require_transaction(workspace, transaction_id)
        self.images[workspace, transaction_id] = raw

    def delete_receipt(self, workspace, transaction_id):
        self.record("delete", workspace, transaction_id)
        self.require_transaction(workspace, transaction_id)
        self.images.pop((workspace, transaction_id), None)


def service_with_backend(native=True):
    lock = ScopeLock()
    backend = LegacyReceipts(lock)
    if native:
        backend.receipt_transaction_exists = backend.lookup
    return ReceiptService(backend, lock), backend, lock


@pytest.mark.parametrize("identifier", ["active", "pending", "disconnected"])
def test_native_reads_writes_and_deletes_are_locked_and_do_not_request_history(identifier):
    service, backend, lock = service_with_backend()
    backend.transactions = lambda *_: pytest.fail("Native receipt lookup must not load history.")
    assert service.get("personal", identifier) is None
    image = png()
    service.save("personal", identifier, image, "image/png")
    assert service.get("personal", identifier) == (image, "image/png")
    assert service.index("personal") == {"supported": True, "transactionIds": [identifier]}
    service.delete("personal", identifier)
    assert service.get("personal", identifier) is None
    assert [event[0] for event in backend.events] == [
        "lookup", "get", "lookup", "save", "lookup", "get", "ids", "lookup", "delete", "lookup", "get",
    ]
    assert lock.entries == 6 and not lock.active


@pytest.mark.parametrize("capability", ["absent", None, False, 0, "unsupported"])
def test_absent_or_noncallable_lookup_keeps_the_legacy_storage_contract(capability):
    service, backend, lock = service_with_backend(native=False)
    if capability != "absent":
        backend.receipt_transaction_exists = capability
    service.save("personal", "active", png(), "image/png")
    assert service.get("personal", "active") == (png(), "image/png")
    service.delete("personal", "active")
    assert [event[0] for event in backend.events] == ["transactions", "save", "transactions", "get", "transactions", "delete"]
    assert lock.entries == 3 and not lock.active


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("identifier", ["missing", "deleted", "foreign", 1, True, {}, [], b"active", ""])
def test_unknown_deleted_foreign_and_malformed_ids_cannot_reach_backend_images(native, identifier):
    service, backend, _ = service_with_backend(native)
    for operation in (lambda: service.get("personal", identifier),
                      lambda: service.save("personal", identifier, png(), "image/png"),
                      lambda: service.delete("personal", identifier)):
        with pytest.raises(StoreError, match="^Transaction not found\\.$"):
            operation()
    assert all(event[0] in {"lookup", "transactions"} for event in backend.events)
    assert backend.images == {}


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("identifier", ["active", {}, [], 1])
def test_missing_workspace_metadata_precedes_malformed_or_missing_ids(native, identifier):
    service, backend, _ = service_with_backend(native)
    backend.metadata.remove("personal")
    with pytest.raises(StoreError, match="^Workspace not found\\.$"):
        service.get("personal", identifier)
    assert [event[0] for event in backend.events] == ["lookup" if native else "transactions"]


@pytest.mark.parametrize("native", [False, True])
def test_none_id_retains_backend_error_and_str_subclasses_keep_legacy_identity(native):
    class Identifier(str):
        pass

    service, backend, _ = service_with_backend(native)
    assert service.get("personal", Identifier("active")) is None
    backend.events.clear()
    with pytest.raises(StoreError, match="^Transaction not found\\.$"):
        service.get("personal", None)
    assert backend.events == [("get", "personal", None)]


@pytest.mark.parametrize("result", [None, 0, 1, "true", "false", [], {}, {"token": "Synthetic private marker"}])
@pytest.mark.parametrize("operation", ["get", "save", "delete"])
def test_malformed_optional_lookup_results_fail_closed_without_sensitive_error_details(result, operation):
    service, backend, lock = service_with_backend()

    def lookup(workspace, transaction_id):
        backend.record("lookup", workspace, transaction_id)
        return result

    backend.receipt_transaction_exists = lookup
    backend.transactions = lambda *_: pytest.fail("Invalid native responses must not fall back to history.")
    with pytest.raises(StoreError, match="^Storage returned an invalid receipt transaction lookup\\.$") as failure:
        if operation == "save":
            service.save("personal", "active", png(), "image/png")
        else:
            getattr(service, operation)("personal", "active")
    assert "Synthetic private marker" not in str(failure.value)
    assert backend.events == [("lookup", "personal", "active")]
    assert backend.images == {} and not lock.active


def test_true_lookup_cannot_authorize_a_non_string_identifier():
    service, backend, _ = service_with_backend()
    backend.receipt_transaction_exists = lambda *_: True
    for identifier in (1, True, {}, []):
        with pytest.raises(StoreError, match="^Transaction not found\\.$"):
            service.get("personal", identifier)
    assert backend.events == []


def test_optional_backend_errors_propagate_without_falling_back():
    service, backend, lock = service_with_backend()

    def failed_lookup(*_):
        assert lock.active
        raise StoreError("Synthetic lookup failure.")

    backend.receipt_transaction_exists = failed_lookup
    backend.transactions = lambda *_: pytest.fail("Storage failure must not trigger legacy reads.")
    with pytest.raises(StoreError, match="^Synthetic lookup failure\\.$"):
        service.get("personal", "active")
    assert backend.events == [] and not lock.active


def test_workspace_supported_and_media_validation_keep_their_existing_order():
    service, backend, lock = service_with_backend()
    backend.save_receipt = None
    service = ReceiptService(backend, lock)
    for workspace in ("invalid", None, {}):
        with pytest.raises(StoreError, match="^Invalid workspace\\.$"):
            service.get(workspace, "active")
    with pytest.raises(StoreError, match="does not support receipt images"):
        service.get("personal", "active")
    assert service.index("invalid") == {"supported": False, "transactionIds": []}
    before = lock.entries
    with pytest.raises(StoreError, match="valid PNG, JPEG or WebP"):
        service.save("invalid", "missing", b"not an image", "image/png")
    with pytest.raises(StoreError, match="does not match its file type"):
        service.save("invalid", "missing", png(), "image/jpeg")
    assert lock.entries == before and backend.events == []


@pytest.mark.parametrize("operation", ["get", "save", "delete"])
def test_backend_active_transaction_checks_still_handle_deletion_after_service_lookup(operation):
    service, backend, lock = service_with_backend()
    backend.images["personal", "active"] = png()

    def deleted_after_lookup(workspace, transaction_id):
        found = backend.lookup(workspace, transaction_id)
        backend.rows[workspace][transaction_id]["deleted"] = True
        return found

    backend.receipt_transaction_exists = deleted_after_lookup
    with pytest.raises(StoreError, match="^Transaction not found\\.$"):
        if operation == "save":
            service.save("personal", "active", png(2, 1), "image/png")
        else:
            getattr(service, operation)("personal", "active")
    assert [event[0] for event in backend.events] == ["lookup", operation]
    assert backend.images == {("personal", "active"): png()} and not lock.active
