"""Bounded private markers validate identities and optional backend projections."""

import pytest

from whab.domain.errors import StoreError
from whab.domain.receipts import validate_receipt_selection
from whab.services.receipts import ReceiptService


class ScopeLock:
    def __init__(self):
        self.active = False
        self.entries = 0

    def __enter__(self):
        assert not self.active
        self.active = True
        self.entries += 1

    def __exit__(self, *_):
        self.active = False


class IndexStore:
    def __init__(self, lock):
        self.lock = lock
        self.events = []
        self.images = {
            "personal": {"active", "pending", "deleted", "disconnected", "off-page"},
            "demo": {"foreign", "active"},
        }

    def record(self, operation, workspace, selected=None):
        assert self.lock.active
        self.events.append((operation, workspace, selected))

    def receipt_ids(self, workspace):
        self.record("full", workspace)
        return sorted(self.images[workspace] - {"deleted"})

    def lookup(self, workspace, selected):
        self.record("selection", workspace, selected)
        return sorted(set(selected).intersection(self.images[workspace]) - {"deleted"})

    def get_receipt(self, *_):
        pytest.fail("Marker reads must never request images.")

    save_receipt = delete_receipt = get_receipt

    def transactions(self, *_):
        pytest.fail("Marker reads must never request transaction history.")


def service_for(native=True):
    lock = ScopeLock()
    backend = IndexStore(lock)
    if native:
        backend.receipt_ids_for = backend.lookup
    return ReceiptService(backend, lock), backend, lock


class ListSubclass(list):
    def __len__(self):
        pytest.fail("Selection validation must reject custom container methods.")


class TupleSubclass(tuple):
    pass


class StringSubclass(str):
    def __len__(self):
        pytest.fail("Selection validation must reject custom string methods.")


@pytest.mark.parametrize("selected", [
    None, [], (), "active", {"active"}, iter(["active"]), ["active"] * 101,
    ListSubclass(["active"]), TupleSubclass(("active",)), [StringSubclass("active")],
    [""], ["x" * 201], ["bad\x00id"], ["\ud800"], ["\udfff"],
    [1], [True], [b"active"], [[]], [{}], ["active", "active"],
])
def test_invalid_receipt_selections_fail_before_storage_without_exposing_values(selected):
    service, backend, lock = service_for()
    with pytest.raises(StoreError):
        validate_receipt_selection(selected)
    with pytest.raises(StoreError):
        service.index_for("personal", selected)
    assert backend.events == [] and lock.entries == 1 and not lock.active


def test_selection_preserves_exact_unicode_order_and_detaches_mutable_input():
    selected = [" spaced ", "é", "e\u0301", "Straße", "STRASSE", "%", "_", "quote'",
                "\x01", "\x7f", "\u0080", "\u07ff", "\u0800", "\ud7ff", "\ue000",
                "\ufdd0", "\uffff", "\U00010000", "\U0010ffff"]
    expected = tuple(selected)
    assert validate_receipt_selection(selected) == expected
    assert validate_receipt_selection(expected) == expected
    result = validate_receipt_selection(selected)
    selected.append("later")
    assert result == expected
    boundary = [f"{index}:" + "x" * 196 for index in range(100)]
    assert validate_receipt_selection(boundary) == tuple(boundary)
    assert validate_receipt_selection(["x" * 200]) == ("x" * 200,)


@pytest.mark.parametrize("workspace", ["personal", "demo"])
def test_native_selection_is_locked_bounded_and_never_requests_full_index_or_images(workspace):
    service, backend, lock = service_for()
    selected = ["disconnected", "pending", "active", "missing", "deleted", "foreign"]
    expected = ["active", "disconnected", "pending"] if workspace == "personal" else ["active", "foreign"]
    backend.receipt_ids = lambda *_: pytest.fail("Native selection must not load the full receipt index.")
    result = service.index_for(workspace, selected)
    assert result == {"supported": True, "transactionIds": expected}
    assert backend.events == [("selection", workspace, tuple(selected))]
    assert lock.entries == 1 and not lock.active


@pytest.mark.parametrize("capability", ["absent", None, False, 0, "unsupported"])
def test_legacy_receipt_storage_keeps_full_read_but_returns_only_the_selection(capability):
    service, backend, lock = service_for(native=False)
    if capability != "absent":
        backend.receipt_ids_for = capability
    assert service.index_for("personal", ["missing", "pending", "active", "foreign"]) == {
        "supported": True, "transactionIds": ["active", "pending"],
    }
    assert backend.events == [("full", "personal", None)]
    assert lock.entries == 1 and not lock.active


def test_legacy_intersection_deduplicates_and_old_full_index_remains_unchanged():
    service, backend, _ = service_for(native=False)
    backend.receipt_ids = lambda _: ["outside", "active", "active", "pending"]
    assert service.index("personal") == {
        "supported": True, "transactionIds": ["outside", "active", "active", "pending"],
    }
    assert service.index_for("personal", ["pending", "active"]) == {
        "supported": True, "transactionIds": ["active", "pending"],
    }


@pytest.mark.parametrize("result", [
    None, (), {"token": "synthetic-private-marker"}, "active", [1], [True], [[]],
    ["active", "active"], ["synthetic-private-marker"], ["active"] * 101,
    [StringSubclass("active")], ListSubclass(["active"]),
])
def test_malformed_native_outputs_fail_closed_without_fallback_or_private_error_data(result):
    service, backend, lock = service_for()
    backend.receipt_ids_for = lambda *_: result
    backend.receipt_ids = lambda *_: pytest.fail("Malformed native output must not use the legacy fallback.")
    with pytest.raises(StoreError, match=r"^Storage returned an invalid receipt selection index\.$") as failure:
        service.index_for("personal", ["active", "pending"])
    assert "synthetic-private-marker" not in str(failure.value)
    assert backend.events == [] and not lock.active


def test_valid_native_output_is_sorted_detached_and_may_be_empty():
    service, backend, _ = service_for()
    returned = ["pending", "active"]
    backend.receipt_ids_for = lambda *_: returned
    result = service.index_for("personal", ["pending", "active"])
    returned.clear()
    assert result == {"supported": True, "transactionIds": ["active", "pending"]}
    assert service.index_for("personal", ["active"]) == {"supported": True, "transactionIds": []}


@pytest.mark.parametrize("result", [None, {"token": "synthetic-private-marker"}, [None], [{}]])
def test_malformed_legacy_index_cannot_escape_the_bounded_response(result):
    service, backend, _ = service_for(native=False)
    backend.receipt_ids = lambda *_: result
    with pytest.raises(StoreError, match=r"^Storage returned an invalid receipt selection index\.$"):
        service.index_for("personal", ["active"])


def test_native_errors_propagate_without_attempting_another_storage_read():
    service, backend, lock = service_for()

    def failed(*_):
        assert lock.active
        raise StoreError("Synthetic scoped read failure.")

    backend.receipt_ids_for = failed
    backend.receipt_ids = lambda *_: pytest.fail("A native error must not cause a full index read.")
    with pytest.raises(StoreError, match=r"^Synthetic scoped read failure\.$"):
        service.index_for("personal", ["active"])
    assert backend.events == [] and not lock.active


@pytest.mark.parametrize("method", ["receipt_ids", "get_receipt", "save_receipt", "delete_receipt"])
def test_new_index_validates_workspace_then_selection_before_reporting_unsupported(method):
    service, backend, lock = service_for()
    setattr(backend, method, None)
    service = ReceiptService(backend, lock)
    for workspace in ("invalid", None, [], True):
        with pytest.raises(StoreError, match=r"^Invalid workspace\.$"):
            service.index_for(workspace, [])
    with pytest.raises(StoreError, match="Receipt selection"):
        service.index_for("personal", [])
    assert service.index_for("personal", ["active"]) == {"supported": False, "transactionIds": []}
    assert service.index("invalid") == {"supported": False, "transactionIds": []}
    assert backend.events == [] and not lock.active
