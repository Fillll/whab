import importlib.metadata
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import whab_plugin_devkit
from whab_plugin_devkit import (
    BackendAdapter,
    BackendHarness,
    Capability,
    ConformanceFailure,
    SyntheticClock,
    public_snapshot,
    receipt_png,
    run_conformance,
)

from whab.plugins import receipt_media_type

from .synthetic_adapter import SyntheticLedger


def test_synthetic_adapter_passes_required_contracts_with_explicit_optional_applicability():
    report = run_conformance(BackendAdapter(SyntheticLedger), clock=SyntheticClock("2026-10"))
    report.assert_success()
    assert len([result for result in report.results if result.status == "passed"]) == 5
    assert {result.capability for result in report.results if result.status == "not-applicable"} == {
        capability for capability in Capability if capability != Capability.LEDGER
    }


def test_advertised_broken_optional_behavior_fails_instead_of_skipping():
    report = run_conformance(BackendAdapter(SyntheticLedger, frozenset({Capability.RECEIPTS})))
    assert not report.passed
    failures = [result for result in report.results if result.status == "failed"]
    assert [(result.capability, result.detail) for result in failures] == [("receipts", "AttributeError")]
    with pytest.raises(ConformanceFailure, match="receipts/receipt-validation-and-reopen"):
        report.assert_success()


def test_legacy_sdk_context_keeps_primary_suites_and_rejects_false_replica_advertisement(monkeypatch):
    def legacy_context(home, environment, config):
        return SimpleNamespace(home=home, environment=environment, config=config)
    monkeypatch.setattr("whab_plugin_devkit.adapters.StorageContext", legacy_context)
    run_conformance(BackendAdapter(SyntheticLedger)).assert_success()
    report = run_conformance(BackendAdapter(SyntheticLedger, frozenset({Capability.NATIVE_REPLICA})))
    failures = [result for result in report.results if result.status == "failed"]
    assert [(result.capability, result.detail) for result in failures] == [("native-replica", "TypeError")]


def test_failed_optional_hook_never_exposes_exception_payload():
    class LeakingAdapter(SyntheticLedger):
        def save_receipt(self, *args):
            raise ValueError("sensitive-synthetic-error-must-not-appear")

    report = run_conformance(BackendAdapter(LeakingAdapter, frozenset({Capability.RECEIPTS})))
    assert "sensitive-synthetic-error" not in repr([asdict(result) for result in report.results])
    assert any(result.detail == "ValueError" for result in report.results)


def test_required_atomic_failure_is_observable():
    class BrokenRejection(SyntheticLedger):
        def assign(self, workspace, category_id, month, assigned):
            self.values[workspace]["categories"].clear()
            super().assign(workspace, category_id, month, assigned)

    report = run_conformance(BackendAdapter(BrokenRejection))
    assert any(result.case == "atomic-invalid-mutation" and result.status == "failed" for result in report.results)


def test_missing_financial_rejection_fails_required_suite():
    class AcceptedInvalidAssignment(SyntheticLedger):
        def assign(self, *args):
            pass

    report = run_conformance(BackendAdapter(AcceptedInvalidAssignment))
    assert any(result.case == "atomic-invalid-mutation" and result.status == "failed" for result in report.results)


def test_reopen_uses_explicit_factory_and_observes_lost_data():
    def empty_reopen(context):
        (context.home / f"synthetic-{context.environment}.json").unlink(missing_ok=True)
        return SyntheticLedger(context)

    report = run_conformance(BackendAdapter(SyntheticLedger, reopen_factory=empty_reopen))
    assert any(result.case == "durable-reopen-and-annotations" and result.status == "failed" for result in report.results)


def test_cleanup_attempts_every_backend_after_close_failure():
    created = []

    class FailingClose(SyntheticLedger):
        def close(self):
            super().close()
            raise RuntimeError("synthetic-private-close-error")

    def factory(context):
        backend = FailingClose(context) if not created else SyntheticLedger(context)
        created.append(backend)
        return backend

    with pytest.raises(RuntimeError, match="failed to close"):
        with BackendHarness(BackendAdapter(factory)) as harness:
            first, second = harness.open(), harness.open("budget-b")
            temporary_home = harness.home
    assert first.closed and second.closed
    assert harness.cleanup_attempts == 2
    assert not temporary_home.exists()


def test_harness_cleans_existing_backends_when_later_startup_fails():
    created = []

    def factory(context):
        if created:
            raise RuntimeError("synthetic startup failure")
        backend = SyntheticLedger(context)
        created.append(backend)
        return backend

    with pytest.raises(RuntimeError, match="startup failure"):
        with BackendHarness(BackendAdapter(factory)) as harness:
            harness.open()
            harness.open("budget-b")
    assert created[0].closed
    assert not harness.home.exists()


def test_factory_and_capability_validation():
    with pytest.raises(ValueError, match="callable"):
        BackendAdapter(None)
    with pytest.raises(ValueError, match="strings"):
        BackendAdapter(SyntheticLedger, frozenset({1}))
    with pytest.raises(ValueError, match="unknown"):
        run_conformance(BackendAdapter(SyntheticLedger, frozenset({"misspelled-capability"})))
    with BackendHarness(BackendAdapter(SyntheticLedger)) as harness:
        with pytest.raises(ValueError, match="temporary budget"):
            harness.open("../outside")


def test_snapshot_is_detached_and_synthetic_receipt_is_valid():
    with BackendHarness(BackendAdapter(SyntheticLedger)) as harness:
        store = harness.open()
        snapshot = public_snapshot(store)
        snapshot["accounts"].append({"id": "not-saved"})
        assert not store.accounts("personal")
    assert receipt_media_type(receipt_png()) == "image/png"


def test_kit_does_not_import_backend_implementations_or_private_state():
    source = Path(whab_plugin_devkit.__file__).parent
    assert tuple(source.glob("*.py")), "Inspect actual installed toolkit source."
    for path in source.glob("*.py"):
        content = path.read_text()
        assert "whab_plugin_sqlite" not in content
        assert "sqlite3" not in content
        assert ".state(" not in content


@pytest.mark.parametrize("capabilities", [frozenset(Capability), frozenset({Capability.NEUTRAL_TRANSFER})])
def test_installed_sqlite_public_factory_conformance(capabilities):
    try:
        distribution = importlib.metadata.distribution("whab_plugin_sqlite")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("Install the optional reference SQLite distribution to run this integration test.")
    entrypoint = next(point for point in distribution.entry_points if point.group == "whab.plugins")
    descriptor = entrypoint.load()
    capabilities = set(capabilities)
    provided = getattr(getattr(descriptor, "contract", None), "provides", ())
    if not any(capability.name == "storage.replica" and capability.version == 1 for capability in provided):
        capabilities.discard(Capability.NATIVE_REPLICA)
    run_conformance(BackendAdapter(
        descriptor.storage, frozenset(capabilities),
    )).assert_success()
