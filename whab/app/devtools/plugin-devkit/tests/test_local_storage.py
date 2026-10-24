"""Controlled toolkit contracts; these doubles are not native capability proof."""

import copy
import io
import json
import sys
from dataclasses import dataclass, replace
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest
from whab_plugin_devkit import local_storage as local
from whab_plugin_devkit.runtime_harness import NativeRuntime

from whab import plugins as sdk

from .test_storage_profiles import descriptor


def test_native_declarations_reject_false_advertisement_and_keep_legacy_inapplicable():
    candidate = descriptor()
    assert local.storage_declaration(candidate)["native"] == "supported"
    assert local.storage_declaration(sdk.Plugin("legacy", "Legacy", storage=lambda _: None))[
        "native"
    ].startswith("not-applicable")
    with pytest.raises(ValueError):
        local.storage_declaration(SimpleNamespace(storage=None))
    with pytest.raises(ValueError):
        local.storage_declaration(
            replace(
                candidate,
                contract=replace(
                    candidate.contract,
                    provides=tuple(replace(row, version=2) for row in candidate.contract.provides),
                ),
            )
        )
    for changes in (
        {"authority": "remote"},
        {"lifecycle": "restart"},
        {"state_version": 2},
        {"provides": (*candidate.contract.provides, sdk.Capability("storage.worker", 2))},
    ):
        with pytest.raises((ValueError, sdk.PluginError)):
            local.storage_declaration(
                replace(candidate, contract=replace(candidate.contract, **changes))
            )
    with pytest.raises(ValueError):
        local.storage_declaration(replace(candidate, settings=lambda *_: {}))
    assert (
        local.assert_storage_readiness(sdk.Plugin("legacy", "Legacy", storage=lambda _: None))[
            "status"
        ]
        == "not-applicable"
    )


def test_absent_new_optional_hook_preserves_declaration_and_actual_sdk_gate():
    candidate = descriptor()

    class OlderPluginShape:
        def __getattr__(self, name):
            if name == "branding_factory":
                raise AttributeError(name)
            return getattr(candidate, name)

    older = OlderPluginShape()
    assert not hasattr(older, "branding_factory")
    assert local.storage_declaration(older) == local.storage_declaration(candidate)
    # This branch executes against the genuinely installed older host in the
    # minimum-wheel gate. Metadata inspection cannot certify native execution.
    if local.Version(local.metadata.version("whab")) < local.Version(local.MINIMUM_STORAGE_HOST):
        with pytest.raises(ValueError, match="actual host SDK"):
            local.assert_storage_readiness(older)
    older.branding_factory = lambda _: None
    with pytest.raises(ValueError, match="unsupported public declaration"):
        local.storage_declaration(older)


def test_platform_metadata_is_explicit_and_never_becomes_a_native_pass(monkeypatch):
    candidate = descriptor()
    message = Message()
    message["Classifier"] = "Operating System :: POSIX"
    entry = SimpleNamespace(name=candidate.id, dist=SimpleNamespace(metadata=message))
    monkeypatch.setattr(local.metadata, "entry_points", lambda **_: [entry])
    monkeypatch.setattr(local, "os", SimpleNamespace(name="nt"))
    assert local.storage_platform(candidate) == "not-applicable: package requires POSIX"
    assert local.storage_conformance(candidate)["cases"] == []
    monkeypatch.setattr(local.metadata, "version", lambda _: "2.41.0")
    assert local.assert_storage_readiness(candidate)["status"].startswith("not-applicable")
    message["Classifier"] = "Operating System :: Microsoft :: Windows"
    assert local.storage_platform(candidate) == "supported"
    monkeypatch.setattr(local.metadata, "entry_points", lambda **_: [])
    assert local.storage_platform(candidate) == "supported"


def test_conformance_requires_all_mandatory_and_only_advertised_optional_suites(monkeypatch):
    calls = []
    report = SimpleNamespace(
        assert_success=lambda: calls.append("asserted"),
        results=[SimpleNamespace(capability="ledger", case="fictional", status="passed")],
    )
    monkeypatch.setattr(
        local, "run_conformance", lambda adapter: calls.append(adapter.capabilities) or report
    )
    monkeypatch.setattr(local, "storage_platform", lambda _: "supported")
    candidate = descriptor()
    value = local.storage_conformance(candidate)
    expected = set(local.Capability) - {
        local.Capability.RECEIPTS,
        local.Capability.NEUTRAL_TRANSFER,
        local.Capability.NATIVE_REPLICA,
    }
    assert calls == [frozenset(expected), "asserted"]
    assert value["status"] == "passed" and value["cases"] == [
        {"capability": "ledger", "case": "fictional", "status": "passed"}
    ]


@dataclass
class Context:
    home: Path
    environment: str
    config: dict
    read_only: bool = False
    expected_instance_id: str | None = None


@dataclass
class Command:
    operation_id: str
    authority_epoch: str
    expected_generation: int
    workspace: str
    name: str
    payload: bytes


def controlled_readiness(monkeypatch, fault=None):
    """A versioned SDK/adapter double tests kit decisions on every kit host."""
    records, writers = {}, []
    monkeypatch.setattr(local, "StorageContext", Context)
    monkeypatch.setattr(sdk, "StorageOperation", Command, raising=False)
    monkeypatch.setattr(local.metadata, "version", lambda _: "2.41.0")
    monkeypatch.setattr(local, "storage_platform", lambda _: "supported")
    monkeypatch.setitem(
        sys.modules,
        "whab.plugins.storage_transfer",
        SimpleNamespace(
            canonical_storage_json=lambda value: json.dumps(value, sort_keys=True).encode()
        ),
    )

    class Backend:
        def __init__(self, context):
            self.context, self.closed = context, False
            if context.home not in records:
                if context.read_only and fault not in {
                    "missing-accepted",
                    "missing-created-then-rejected",
                }:
                    raise ValueError("fictional missing native state")
                context.home.mkdir(parents=True, exist_ok=True)
                if context.read_only and fault == "missing-created-then-rejected":
                    raise ValueError("fictional creation before refusal")
                records[context.home] = {"accounts": [], "generation": 0, "receipt": None}
                (context.home / "native").write_bytes(b"0")
            self.values = records[context.home]
            self.stopped = not writers and context.read_only
            if not context.read_only:
                writers.append(self)

        def accounts(self, workspace):
            return copy.deepcopy(self.values["accounts"])

        def categories(self, workspace):
            return []

        def transactions(self, workspace):
            return []

        def commit_evidence(self):
            generation = self.values["generation"]
            if self.context.read_only and (
                fault == "reader-mismatch" or fault == "stopped-mismatch" and self.stopped
            ):
                generation += 1
            return SimpleNamespace(
                instance_id="fictional-instance",
                authority_epoch="fictional-epoch",
                generation=generation,
            )

        def execute_operation(self, operation):
            if self.context.read_only:
                if fault == "reader-mutates":
                    return None
                raise ValueError("fictional read-only refusal")
            if self.values["receipt"] is not None:
                return SimpleNamespace(
                    replayed=fault != "bad-replay", receipt=self.values["receipt"]
                )
            self.values["generation"] += 1
            self.values["accounts"] = [{"id": "wallet", "balance": 125}]
            self.values["receipt"] = SimpleNamespace(operation_id=operation.operation_id)
            (self.context.home / "native").write_bytes(b"1")
            return SimpleNamespace(replayed=False, receipt=self.values["receipt"])

        def close(self):
            if fault == "close-failed":
                raise ValueError("fictional retained owner")
            if not self.closed:
                self.closed = True
                if self in writers:
                    writers.remove(self)
                if self.context.read_only and fault == "reader-extra-file":
                    (self.context.home / "unexpected").write_bytes(b"unexpected")
                if self.stopped and fault == "stopped-file-changed":
                    (self.context.home / "native").write_bytes(b"changed")

    return replace(descriptor(), storage=Backend)


def test_controlled_public_readiness_checks_read_only_reopen_and_exact_native_retry(monkeypatch):
    assert local.assert_storage_readiness(controlled_readiness(monkeypatch)) == {
        "status": "passed",
        "read_only": "unchanged",
        "reopen": "preserved",
        "retry": "exact-receipt",
    }


@pytest.mark.parametrize(
    "fault",
    [
        "missing-accepted",
        "missing-created-then-rejected",
        "reader-mismatch",
        "reader-mutates",
        "reader-extra-file",
        "stopped-mismatch",
        "stopped-file-changed",
        "bad-replay",
        "close-failed",
    ],
)
def test_controlled_readiness_never_converts_broken_behavior_into_a_pass(monkeypatch, fault):
    with pytest.raises((AssertionError, RuntimeError)):
        local.assert_storage_readiness(controlled_readiness(monkeypatch, fault))


def test_advertised_worker_on_a_genuine_older_sdk_fails_instead_of_skipping(monkeypatch):
    monkeypatch.setattr(local.metadata, "version", lambda _: "2.22.0")
    with pytest.raises(ValueError, match="actual host SDK"):
        local.assert_storage_readiness(descriptor())


def test_native_runtime_accepts_only_one_explicit_reviewed_storage_primary():
    candidate = descriptor()
    runtime = NativeRuntime(candidate.id, (), storage_candidate=candidate)
    try:
        assert runtime.environment()["WHAB_STORAGE"] == candidate.id
    finally:
        runtime.__exit__(None, None, None)
    for storage, selected, declared in (
        (candidate.id, (), None),
        ("different", (), candidate),
        (candidate.id, (candidate.id,), candidate),
    ):
        with pytest.raises(ValueError):
            NativeRuntime(storage, selected, storage_candidate=declared)


def test_native_fixture_reader_bounds_files_depth_size_and_types(tmp_path, monkeypatch):
    (tmp_path / "valid").write_bytes(b"safe")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/file").write_bytes(b"bounded nested bytes")
    assert local._files(tmp_path)
    (tmp_path / "linked").symlink_to(tmp_path / "valid")
    with pytest.raises(AssertionError):
        local._files(tmp_path)
    (tmp_path / "linked").unlink()
    large = tmp_path / "too-large"
    with large.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(AssertionError):
        local._files(tmp_path)
    large.unlink()
    for index in range(129):
        (tmp_path / f"count-{index}").touch()
    with pytest.raises(AssertionError):
        local._files(tmp_path)
    for path in tmp_path.glob("count-*"):
        path.unlink()
    original = Path.open
    monkeypatch.setattr(
        Path,
        "open",
        lambda path, *args, **kwargs: (
            io.BytesIO(b"") if path.name == "valid" else original(path, *args, **kwargs)
        ),
    )
    with pytest.raises(AssertionError):
        local._files(tmp_path)
    monkeypatch.setattr(Path, "open", original)
    entry = SimpleNamespace(
        path=str(tmp_path / "special"),
        is_symlink=lambda: False,
        is_dir=lambda **_: False,
        is_file=lambda **_: False,
    )

    class Entries:
        def __enter__(self):
            return iter([entry])

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(local, "os", SimpleNamespace(scandir=lambda _: Entries()))
    with pytest.raises(AssertionError):
        local._files(tmp_path)
