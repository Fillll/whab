import base64
import copy
import json

import pytest
from whab_plugin_devkit import receipt_png
from whab_plugin_json import codec

from whab.plugins import StoreError


@pytest.fixture
def native(json_store, json_import):
    json_store.import_workspace("personal", "fictional", "fingerprint", json_import)
    json_store.save_receipt("personal", "import-transaction", receipt_png())
    json_store.save_item("item", "fictional-ciphertext", "Fictional Bank")
    return json.loads(json_store.path.read_bytes())


def _personal(value):
    return value["workspaces"]["personal"]


def _item(value):
    return _personal(value)["items"][0]


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(extra=True),
    lambda value: value.update(formatVersion=4),
    lambda value: value.update(formatVersion=True),
    lambda value: value.update(generation=True),
    lambda value: value.update(generation=-1),
    lambda value: value.update(generation=2**54),
    lambda value: value.update(operationId=True),
    lambda value: value.update(operationId=""),
    lambda value: value.update(operationId="\ud800"),
    lambda value: value.update(authorityEpoch=1),
    lambda value: value.update(workspaces={}),
    lambda value: _personal(value).update(items=None),
    lambda value: _personal(value).update(items=[{}] * 1001),
    lambda value: _personal(value)["items"].append(copy.deepcopy(_item(value))),
    lambda value: _item(value).update(institutionName=""),
    lambda value: _item(value).update(institutionName="x\x00"),
    lambda value: _item(value).update(institutionName="\ud800"),
    lambda value: _item(value).update(id="\ud800"),
    lambda value: _item(value).update(institutionName="x" * 201),
    lambda value: _item(value).update(provider="manual"),
    lambda value: _item(value).update(provider="Invalid Provider"),
    lambda value: _item(value).update(provider="x" * 65),
    lambda value: _item(value).update(token=""),
    lambda value: _item(value).update(status=1),
    lambda value: _item(value).update(status=None),
    lambda value: _item(value).update(token=False),
    lambda value: _item(value).update(lastSynced="2026-10-01"),
    lambda value: _item(value).update(lastSynced="not-a-timestamp"),
    lambda value: value["workspaces"]["demo"].update(items=[copy.deepcopy(_item(value))]),
    lambda value: _personal(value).update(associations=[{"accountId": "missing", "itemId": "item", "mask": ""}]),
    lambda value: _personal(value).update(associations=[{"accountId": "import-account", "itemId": "missing", "mask": ""}]),
    lambda value: _personal(value).update(associations=[{"accountId": "import-account", "itemId": "item", "mask": ""}]),
    lambda value: _personal(value)["receipts"].append(copy.deepcopy(_personal(value)["receipts"][0])),
    lambda value: _personal(value)["receipts"][0].update(transactionId="missing"),
    lambda value: _personal(value)["receipts"][0].update(image="a==="),
    lambda value: _personal(value)["receipts"][0].update(image="Zh=="),
    lambda value: _personal(value)["receipts"][0].update(image=base64.b64encode(b"invalid raster").decode()),
    lambda value: _personal(value)["receipts"][0].update(image=1),
    lambda value: _personal(value)["importReceipt"]["counts"].update(accounts=True),
    lambda value: _personal(value)["importReceipt"].update(pluginId="\ud800"),
    lambda value: _personal(value)["importReceipt"].update(fingerprint="\ud800"),
    lambda value: _personal(value)["ledger"]["accounts"][0].update(balance=1.5),
    lambda value: _personal(value)["ledger"]["transactions"][0].update(accountId="missing"),
])
def test_native_schema_rejects_invalid_types_private_references_and_encodings(native, mutate):
    mutate(native)
    with pytest.raises(StoreError):
        codec.decode(json.dumps(native).encode())


def test_duplicate_account_connection_references_are_rejected(native):
    _item(native)["provider"] = "fictional"
    association = {"accountId": "import-account", "itemId": "item", "mask": ""}
    _personal(native)["associations"] = [association, association]
    with pytest.raises(StoreError):
        codec.decode(json.dumps(native).encode())


def test_provider_owned_unknown_status_remains_valid_opaque_state(native):
    _item(native)["status"] = "independent-provider-attention"
    restored = codec.decode(json.dumps(native).encode())
    assert restored["workspaces"]["personal"]["items"]["item"]["status"] == "independent-provider-attention"


def test_cumulative_historical_money_limit_is_explicit_even_when_transactions_cancel(native):
    ledger = _personal(native)["ledger"]
    transaction = ledger["transactions"][0]
    ledger["transactions"] = [
        {**transaction, "id": transaction["id"] if index == 0 else f"fictional-history-{index}",
         "amount": 1_000_000_000_000 * (1 if index % 2 == 0 else -1)}
        for index in range(9007)
    ]
    assert codec.decode(json.dumps(native).encode())["workspaces"]["personal"]["transactions"]
    ledger["transactions"].append({**transaction, "id": "fictional-history-9007", "amount": -1_000_000_000_000})
    with pytest.raises(StoreError, match="totals"):
        codec.decode(json.dumps(native).encode())


@pytest.mark.parametrize("field", ["item-id", "import-plugin-id", "import-fingerprint"])
def test_invalid_utf8_private_identifiers_are_rejected_before_reopen_and_raw_file_preserved(json_store, json_import, field):
    from whab_plugin_json import Store

    json_store.import_workspace("personal", "fictional", "fingerprint", json_import)
    json_store.save_item("item", "fictional-ciphertext", "Fictional")
    path = json_store.path
    json_store.close()
    document = json.loads(path.read_bytes())
    if field == "item-id":
        _item(document)["id"] = "\ud800"
    else:
        key = "pluginId" if field == "import-plugin-id" else "fingerprint"
        _personal(document)["importReceipt"][key] = "\ud800"
    damaged = json.dumps(document).encode()
    path.write_bytes(damaged)
    with pytest.raises(StoreError):
        Store(path)
    assert path.read_bytes() == damaged


@pytest.mark.parametrize("raw", [None, "{}", b"", b'{"a":1,"a":2}', b"[NaN]", b"[Infinity]", b"[-Infinity]", b"1e999", b"1.5", b"{", b"\xff"])
def test_parser_rejects_duplicates_nonfinite_values_truncation_and_nonbytes(raw):
    with pytest.raises(StoreError):
        codec.parse(raw)


def test_size_and_nesting_are_bounded_before_decoding():
    with pytest.raises(StoreError, match="size"):
        codec.parse(b"{}", limit=1)
    with pytest.raises(StoreError, match="nesting"):
        codec.parse(b"[" * 25 + b"]" * 25)
    value = {"escaped": '["\\{}] a quoted brace'}
    assert codec.parse(codec.serialize(value)) == value


@pytest.mark.parametrize("value", [object(), float("nan"), "\ud800"])
def test_unserializable_candidates_cannot_be_written(value):
    with pytest.raises(StoreError):
        codec.serialize(value)


def test_serialized_size_and_receipt_expansion_are_bounded(json_store, monkeypatch):
    with pytest.raises(StoreError, match="size"):
        codec.serialize({"fictional": "text"}, limit=1)
    account = json_store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 100})
    transaction = json_store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Fictional", "amount": 1,
    })
    before = json_store.path.read_bytes()
    monkeypatch.setattr(codec, "MAX_PORTABLE_BYTES", 10)
    with pytest.raises(StoreError, match="size"):
        json_store.save_receipt("personal", transaction, receipt_png())
    assert json_store.path.read_bytes() == before


@pytest.mark.parametrize("files", [None, {}, {"wrong.json": b"{}"}])
def test_portable_payload_requires_exact_filename(files):
    with pytest.raises(StoreError):
        codec.read_files(files)


@pytest.mark.parametrize("change", [
    {"formatVersion": 2}, {"formatVersion": True}, {"items": [{}]}, {"associations": [{}]},
])
def test_portable_payload_rejects_runtime_records_and_incompatible_version(json_store, change):
    value = json.loads(json_store.export_budget_files()["budget.json"])
    value.update(change)
    with pytest.raises(StoreError):
        codec.read_files({"budget.json": json.dumps(value).encode()})
