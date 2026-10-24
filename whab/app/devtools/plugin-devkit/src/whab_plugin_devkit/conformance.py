"""Small reproducible storage suites exercised through public SDK methods."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from json import dumps
from uuid import uuid4

from whab.plugins import StoreError, WorkspaceImport

from .adapters import BackendHarness, public_snapshot
from .synthetic import SyntheticClock, imported_account, imported_transaction, receipt_png


class Capability(StrEnum):
    LEDGER = "ledger"
    PROVIDER_SYNC = "provider-sync"
    RECEIPTS = "receipts"
    WORKSPACE_IMPORT = "workspace-import"
    NEXT_MONTH_FUNDING = "next-month-funding"
    MONTHLY_ROLLOVER = "monthly-rollover"
    NEUTRAL_TRANSFER = "neutral-transfer"
    NATIVE_REPLICA = "native-replica"


@dataclass(frozen=True)
class Result:
    capability: str
    case: str
    status: str
    detail: str = ""


class ConformanceFailure(AssertionError):
    """At least one required or explicitly advertised contract failed."""


@dataclass(frozen=True)
class Report:
    results: tuple[Result, ...]

    @property
    def passed(self):
        return not any(result.status == "failed" for result in self.results)

    def assert_success(self):
        failures = [result for result in self.results if result.status == "failed"]
        if failures:
            names = ", ".join(f"{result.capability}/{result.case}" for result in failures)
            raise ConformanceFailure(f"WHAB conformance failed: {names}")
        return self


def _check(condition, label):
    if not condition:
        raise AssertionError(label)


def _reject(operation):
    try:
        operation()
    except StoreError:
        return
    raise AssertionError("Invalid financial operation was accepted.")


def _seed(store, clock, *, currency="USD", amount=125, transaction_id="same-raw-id"):
    account = store.add_account("personal", {
        "name": "Fictional checking", "type": "depository", "balance": 100000,
        "currency": currency,
    })
    category = store.add_category("personal", {
        "name": "Fictional essentials", "group": "Fictional group", "target": 0,
        "currency": currency,
    })
    transaction = store.add_transaction("personal", {
        "id": transaction_id, "accountId": account, "date": clock.date,
        "merchant": "Fictional shop", "amount": amount, "categoryId": category,
    })
    return account, category, transaction


def _ledger_currency(harness, clock):
    store = harness.open()
    usd, _, _ = _seed(store, clock)
    eur, euro_category, _ = _seed(store, clock, currency="EUR", amount=7, transaction_id="eur")
    accounts = {row["id"]: row for row in store.accounts("personal")}
    _check(accounts[usd]["balance"] == 99875, "USD balance did not use integer minor units.")
    _check(accounts[eur]["balance"] == 99993, "EUR balance did not use integer minor units.")
    _check(all(type(row["balance"]) is int for row in accounts.values()), "Noninteger balance.")
    view = store.workspace("personal", clock.month)
    _check(view["summariesByCurrency"]["USD"]["spent"] == 125, "USD activity is wrong.")
    _check(view["summariesByCurrency"]["EUR"]["spent"] == 7, "EUR activity is wrong.")
    before = public_snapshot(store, month=clock.month)
    _reject(lambda: store.add_transaction("personal", {
        "accountId": usd, "date": clock.date, "merchant": "Fictional invalid purchase",
        "amount": 1, "categoryId": euro_category,
    }))
    _check(public_snapshot(store, month=clock.month) == before, "Cross-currency rejection mutated data.")


def _ledger_atomic_rejection(harness, clock):
    store = harness.open()
    _seed(store, clock)
    before = public_snapshot(store, month=clock.month)
    for value in (True, 1.5, "100"):
        _reject(lambda: store.add_account("personal", {
            "name": "Fictional invalid balance", "type": "depository", "balance": value,
        }))
        _check(public_snapshot(store, month=clock.month) == before, "Rejected balance mutated data.")
    _reject(lambda: store.assign("personal", "absent-category", clock.month, 100))
    _check(public_snapshot(store, month=clock.month) == before, "Rejected assignment mutated data.")


def _ledger_isolation(harness, clock):
    first, second = harness.open(), harness.open("budget-b")
    demo_before = public_snapshot(first, "demo", month=clock.month)
    _, _, transaction_a = _seed(first, clock)
    _, _, transaction_b = _seed(second, clock, amount=250)
    _check(transaction_a == transaction_b, "Synthetic transaction IDs did not collide.")
    _check(first.transactions("personal")[0]["amount"] == 125, "Budget A was retargeted.")
    _check(second.transactions("personal")[0]["amount"] == 250, "Budget B was retargeted.")
    _check(public_snapshot(first, "demo", month=clock.month) == demo_before, "Personal data leaked into demo.")
    harness.close(first)
    production = harness.open(environment="production")
    _check(not production.accounts("personal"), "Sandbox records leaked into production.")


def _ledger_reopen(harness, clock):
    store = harness.open()
    _, _, transaction = _seed(store, clock)
    store.edit_transaction("personal", transaction, reviewed=True, note="Fictional saved memo")
    before = public_snapshot(store, month=clock.month)
    harness.close(store)
    reopened = harness.open(reopen=True)
    _check(public_snapshot(reopened, month=clock.month) == before, "Reopen lost committed public data.")


def _detached_reads(harness, clock):
    store = harness.open()
    _seed(store, clock)
    before = public_snapshot(store)
    for method in ("accounts", "categories", "transactions"):
        rows = getattr(store, method)("personal")
        rows[0]["id"] = "changed-detached-observation"
        rows.clear()
    _check(public_snapshot(store) == before, "A public read exposed mutable saved records.")


def _provider_sync(harness, clock):
    first, second = harness.open(), harness.open("budget-b")
    category = first.add_category("personal", {"name": "Fictional", "group": "Fictional", "target": 0})
    for store in (first, second):
        store.save_item("shared-item", "synthetic-encrypted-token", "Fictional Bank")
        store.apply_sync("shared-item", {
            "added": [imported_transaction(clock)], "modified": [], "removed": [],
        }, "cursor-one", [imported_account()], expected_cursor=None)
    first.edit_transaction("personal", "pending", category, True, "Keep this fictional memo")
    posted = imported_transaction(clock, "posted", pending=False, pendingTransactionId="pending")
    first.apply_sync("shared-item", {
        "added": [posted], "modified": [], "removed": ["pending"],
    }, "cursor-two", expected_cursor="cursor-one")
    rows = {row["id"]: row for row in first.transactions("personal")}
    _check("pending" not in rows, "Pending identity remained active after posting.")
    _check(rows["posted"]["note"] == "Keep this fictional memo", "Posting lost the memo.")
    _check(rows["posted"]["reviewed"] is True, "Posting lost review status.")
    _check(rows["posted"]["categoryId"] == category, "Posting lost the explicit category.")
    _check(second.get_item("shared-item")["cursor"] == "cursor-one", "Checkpoint leaked across budgets.")
    before = public_snapshot(first)
    _reject(lambda: first.apply_sync("shared-item", {
        "added": [imported_transaction(clock, "invalid", accountId="missing")],
        "modified": [], "removed": [],
    }, "must-not-commit", expected_cursor="cursor-two"))
    _check(public_snapshot(first) == before, "Failed sync changed the ledger.")
    _check(first.get_item("shared-item")["cursor"] == "cursor-two", "Failed sync advanced its cursor.")
    _reject(lambda: first.apply_sync("shared-item", {
        "added": [], "modified": [], "removed": [],
    }, "must-not-commit", expected_cursor="stale-cursor"))
    harness.close(first)
    reopened = harness.open(reopen=True)
    _check(public_snapshot(reopened) == before, "Reopen lost sync annotations.")
    _check(reopened.get_item("shared-item")["cursor"] == "cursor-two", "Reopen lost checkpoint.")


def _receipts(harness, clock):
    store = harness.open()
    _, _, transaction = _seed(store, clock)
    image = receipt_png()
    store.save_receipt("personal", transaction, image)
    _check(store.get_receipt("personal", transaction) == image, "Receipt bytes changed.")
    _check(store.receipt_ids("personal") == [transaction], "Receipt index is wrong.")
    _reject(lambda: store.save_receipt("personal", transaction, b"invalid synthetic image"))
    _check(store.get_receipt("personal", transaction) == image, "Rejected receipt replaced saved bytes.")
    _reject(lambda: store.save_receipt("personal", "missing-transaction", image))
    harness.close(store)
    reopened = harness.open(reopen=True)
    _check(reopened.get_receipt("personal", transaction) == image, "Reopen lost receipt bytes.")
    reopened.delete_receipt("personal", transaction)
    _check(reopened.get_receipt("personal", transaction) is None, "Receipt deletion failed.")
    harness.close(reopened)
    _check(harness.open(reopen=True).get_receipt("personal", transaction) is None, "Reopen resurrected a deleted receipt.")


def _workspace_import(harness, clock):
    store = harness.open()
    account = imported_account()
    account = {key: value for key, value in account.items() if key not in {"itemId", "currency"}}
    transaction = {
        "id": "imported", "accountId": "shared-account", "date": clock.date,
        "merchant": "Fictional import", "amount": 100, "categoryId": "import-category",
        "pending": False, "reviewed": True, "source": "fictional-import", "note": "Fictional memo",
        "isTransfer": False, "inferredTransfer": False, "categoryEdited": True, "deleted": False,
    }
    data = WorkspaceImport(
        accounts=({**account, "source": "fictional-import"},),
        categories=({"id": "import-category", "name": "Fictional", "group": "Fictional", "target": 0},),
        transactions=(transaction,),
        assignments=({"month": clock.month, "categoryId": "import-category", "assigned": -25},),
        budget_start_month=clock.month,
    )
    before = public_snapshot(store, month=clock.month)
    invalid = WorkspaceImport(
        data.accounts, data.categories, ({**transaction, "accountId": "absent"},),
        data.assignments, data.budget_start_month,
    )
    fingerprint = sha256(b"fictional-public-conformance-import").hexdigest()
    _reject(lambda: store.import_workspace("personal", "fictional-import", fingerprint, invalid))
    _check(public_snapshot(store, month=clock.month) == before, "Failed import partially committed.")
    _check(store.preview_workspace_import("personal", "fictional-import", fingerprint)["canImport"], "Preview changed eligibility.")
    result = store.import_workspace("personal", "fictional-import", fingerprint, data)
    _check(result["counts"]["transactions"] == 1 and not result["alreadyImported"], "Import result is wrong.")
    store.edit_transaction("personal", "imported", note="Edited after import")
    harness.close(store)
    reopened = harness.open(reopen=True)
    retry = reopened.import_workspace("personal", "fictional-import", fingerprint, data)
    _check(retry["alreadyImported"], "Reopen lost the import retry identity.")
    _check(reopened.transactions("personal")[0]["note"] == "Edited after import", "Retry overwrote a later edit.")


def _funding(harness, clock):
    store = harness.open()
    _seed(store, clock)
    store.assign_next_month("personal", clock.month, "USD", 1000)
    before = public_snapshot(store, month=clock.month)
    _check(before["workspace"]["summary"]["assignedNextMonth"] == 1000, "Funding was not projected.")
    _reject(lambda: store.assign_next_month("personal", clock.month, "USD", -1))
    _check(public_snapshot(store, month=clock.month) == before, "Rejected funding mutated saved data.")
    harness.close(store)
    _check(public_snapshot(harness.open(reopen=True), month=clock.month) == before, "Reopen lost funding.")


def _rollover(harness, clock):
    store = harness.open()
    _seed(store, clock)
    store.set_rollover_mode("personal", clock.month, "USD", "balance")
    before = public_snapshot(store, month=clock.month)
    _check(before["workspace"]["rolloverMode"] == "balance", "Rollover was not projected.")
    _reject(lambda: store.set_rollover_mode("personal", clock.month, "USD", "invalid"))
    _check(public_snapshot(store, month=clock.month) == before, "Rejected rollover mutated saved data.")
    harness.close(store)
    _check(public_snapshot(harness.open(reopen=True), month=clock.month) == before, "Reopen lost rollover.")


def _neutral_transfer(harness, clock):
    source, destination = harness.open(), harness.open("budget-b")
    _seed(source, clock)
    exported = source.export_budget("personal")
    if Capability.PROVIDER_SYNC in harness.adapter.capabilities:
        source.save_item("shared-item", "synthetic-encrypted-token", "Fictional Bank")
        source.apply_sync("shared-item", {
            "added": [imported_transaction(clock)], "modified": [], "removed": [],
        }, "synthetic-private-cursor", [imported_account()])
        source.apply_sync("shared-item", {"added": [], "modified": [], "removed": ["pending"]}, "next-cursor")
        exported = source.export_budget("personal")
        _check(any(row["id"] == "pending" and row["deleted"] for row in exported["transactions"]), "Transfer lost deleted history.")
    encoded = dumps(exported, sort_keys=True, allow_nan=False)
    _check("synthetic-encrypted-token" not in encoded and "next-cursor" not in encoded, "Neutral transfer exposed private records.")
    destination.restore_budget("personal", exported)
    restored = destination.export_budget("personal")
    # A sanitized restore deliberately marks external account snapshots stale.
    for account in exported["accounts"]:
        if account["source"] not in {"manual", "demo"}:
            account["stale"] = True
    _check(restored == exported, "Neutral transfer changed supported financial records.")
    _reject(lambda: destination.restore_budget("personal", exported))


def _native_replica(harness, clock):
    # Deliberately duck type only public methods/returned records. Older SDKs
    # keep running their supported suites; false replica advertisements fail.
    source = harness.open("replica-primary")
    _, _, transaction = _seed(source, clock)
    if Capability.RECEIPTS in harness.adapter.capabilities:
        source.save_receipt("personal", transaction, receipt_png())
    destination = harness.open("replica-copy", initial_role="copy")
    initial = destination.commit_evidence()
    replica_id = str(uuid4())
    first = source.export_local()
    watermark = destination.apply_replica(replica_id, first, "1" * 64, initial, namespace_generation=1)
    copied = destination.export_local()
    _check((copied.financial, copied.protected, copied.receipt_bytes) == (first.financial, first.protected, first.receipt_bytes),
           "Replica lost complete local data.")
    _check(copied.source.role == "copy" and copied.source.instance_id != first.source.instance_id,
           "Replica did not retain its fenced native identity.")
    _reject(lambda: destination.add_account("personal", {"name": "Invalid copy write", "type": "depository", "balance": 1}))
    namespace = destination.apply_replica(replica_id, first, "2" * 64, destination.commit_evidence(), namespace_generation=2)
    _check(namespace.source_generation == watermark.source_generation and namespace.namespace_generation == 2,
           "Namespace-only copy save lost its independent generation.")
    _reject(lambda: destination.apply_replica(replica_id, first, "1" * 64, destination.commit_evidence(), namespace_generation=1))
    source.add_account("personal", {"name": "Acknowledged later", "type": "depository", "balance": 2})
    latest = source.export_local()
    watermark = destination.apply_replica(replica_id, latest, "2" * 64, destination.commit_evidence(), namespace_generation=2)
    _check(destination.apply_replica(replica_id, latest, "2" * 64, initial, namespace_generation=2) == watermark,
           "Exact lost-response retry did not replay its durable watermark.")
    _reject(lambda: destination.apply_replica(replica_id, first, "3" * 64, destination.commit_evidence(), namespace_generation=3))
    harness.close(destination)
    destination = harness.open("replica-copy", reopen=True)
    _check(destination.replica_watermark() == watermark and destination.commit_evidence().role == "copy",
           "Replica watermark or fence disappeared after reopen.")
    transition_id = str(uuid4())
    prepared = destination.prepare_promotion(transition_id, destination.commit_evidence(), watermark)
    _reject(lambda: destination.apply_replica(replica_id, latest, "3" * 64, prepared, namespace_generation=3))
    destination.abort_promotion(transition_id, prepared)
    prepared = destination.prepare_promotion(transition_id, destination.commit_evidence(), watermark)
    fenced = source.set_role(transition_id, source.commit_evidence(), "fenced")
    promoted = destination.set_role(transition_id, prepared, "primary", authority_epoch=str(uuid4()))
    _check(promoted.authority_epoch != watermark.source_authority_epoch, "Promotion reused its source authority epoch.")
    source.set_role(transition_id, fenced, "retired")
    _reject(lambda: source.add_account("personal", {"name": "Invalid retired write", "type": "depository", "balance": 1}))
    destination.add_account("personal", {"name": "Promoted primary write", "type": "depository", "balance": 3})
    acknowledged = public_snapshot(destination)
    harness.close(destination)
    destination = harness.open("replica-copy", reopen=True)
    _check(destination.commit_evidence().role == "primary" and public_snapshot(destination) == acknowledged,
           "Promoted primary lost an acknowledged write after reopen.")


_SUITES = {
    Capability.LEDGER: (
        ("minor-units-and-currencies", _ledger_currency),
        ("atomic-invalid-mutation", _ledger_atomic_rejection),
        ("budget-workspace-environment-isolation", _ledger_isolation),
        ("durable-reopen-and-annotations", _ledger_reopen),
        ("detached-public-reads", _detached_reads),
    ),
    Capability.PROVIDER_SYNC: (("pending-posting-and-atomic-checkpoint", _provider_sync),),
    Capability.RECEIPTS: (("receipt-validation-and-reopen", _receipts),),
    Capability.WORKSPACE_IMPORT: (("atomic-import-and-durable-retry", _workspace_import),),
    Capability.NEXT_MONTH_FUNDING: (("funding-validation-and-reopen", _funding),),
    Capability.MONTHLY_ROLLOVER: (("rollover-validation-and-reopen", _rollover),),
    Capability.NEUTRAL_TRANSFER: (("sanitized-financial-roundtrip", _neutral_transfer),),
    Capability.NATIVE_REPLICA: (("fenced-monotonic-copy-and-promotion", _native_replica),),
}


def run_conformance(adapter, *, clock=None):
    """Return every result; call ``assert_success()`` to make failures a test gate.

    An advertised suite is always executed, even when its methods are missing.
    Unadvertised optional suites are explicitly ``not-applicable``. Diagnostics
    include the exception type only, never provider text, paths or payloads.
    """
    unknown = adapter.capabilities - set(_SUITES)
    if unknown:
        raise ValueError("The adapter advertises an unknown conformance capability.")
    clock = clock or SyntheticClock()
    results = []
    for capability, cases in _SUITES.items():
        if capability != Capability.LEDGER and capability not in adapter.capabilities:
            results.append(Result(capability, "applicability", "not-applicable", "Not advertised by adapter."))
            continue
        for name, case in cases:
            try:
                with BackendHarness(adapter) as harness:
                    case(harness, clock)
                results.append(Result(capability, name, "passed"))
            except Exception as error:
                results.append(Result(capability, name, "failed", type(error).__name__))
    return Report(tuple(results))
