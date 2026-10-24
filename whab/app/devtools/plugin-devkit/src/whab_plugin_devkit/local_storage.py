"""Public-only local-storage candidate checks for the next independent toolkit.

No new SDK-only type is imported at module load, preserving old-host use of the
toolkit's other families. A false native-worker declaration is a failure, never a
skip. Candidate code is executed only after the caller verifies its approved
installed artifact closure, following the existing toolkit CLI contract.
"""

import hashlib
import os
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory

from packaging.version import Version

from whab.plugins import StorageContext, contract_for

from .adapters import BackendAdapter, public_snapshot
from .conformance import Capability, run_conformance

MINIMUM_STORAGE_HOST = "2.37.0"
_NATIVE = {"storage.authority", "storage.commands", "storage.worker"}
_KNOWN = {
    "ledger.read",
    "ledger.mutate",
    "provider.state",
    "provider.sync",
    "receipts",
    "transfer.neutral",
    "transfer.prepare",
    "transfer.local",
    "storage.authority",
    "storage.commands",
    "storage.replica",
    "storage.worker",
}
_OPTIONAL = {
    "receipts": Capability.RECEIPTS,
    "transfer.neutral": Capability.NEUTRAL_TRANSFER,
    "storage.commands": Capability.DURABLE_COMMANDS,
    "storage.replica": Capability.NATIVE_REPLICA,
}


def storage_platform(plugin):
    """Honor an installed package's explicit POSIX-only platform declaration."""
    entries = [
        entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == plugin.id
    ]
    classifiers = entries[0].dist.metadata.get_all("Classifier", []) if len(entries) == 1 else []
    platforms = {name for name in classifiers if name.startswith("Operating System :: ")}
    posix_only = bool(platforms) and all(
        name.startswith("Operating System :: POSIX") or name == "Operating System :: MacOS"
        for name in platforms
    )
    return (
        "not-applicable: package requires POSIX"
        if posix_only and os.name != "posix"
        else "supported"
    )


def storage_declaration(plugin):
    """Describe exact supported public declarations without opening a backend."""
    if not callable(getattr(plugin, "storage", None)):
        raise ValueError("Choose an explicitly declared storage candidate.")
    contract = contract_for(plugin)
    capabilities = {row.name: row for row in contract.provides}
    if any(row.version != 1 for name, row in capabilities.items() if name in _KNOWN):
        raise ValueError("The storage profile requires a supported advertised contract version.")
    advertised = "storage.worker" in capabilities
    supported = (
        plugin.contract is not None
        and contract.authority == "local"
        and contract.lifecycle == "live"
        and _NATIVE <= capabilities.keys() <= _KNOWN
        and all(row.version == 1 for row in capabilities.values())
        and contract.state_version == 1
        and not contract.state_upgrades
        and all(
            getattr(plugin, name) is None
            for name in (
                "bind",
                "migrate_state",
                "settings",
                "budget_connection",
                "categorization",
                "bank",
                "bank_ui",
                "ui",
                "interface",
                "insights",
                "export",
                "branding_factory",
                "bank_logos",
                "merchant_logos",
                "file_import",
            )
        )
    )
    if advertised and not supported:
        raise ValueError(
            "The advertised native storage worker has an unsupported public declaration."
        )
    return {
        "plugin": plugin.id,
        "capabilities": sorted(capabilities),
        "native": "supported"
        if supported
        else "not-applicable: no compatible native storage worker declaration",
        "same_device_transfer": "supported"
        if "transfer.local" in capabilities
        else "not-applicable: transfer.local is not advertised",
        "copies": "supported"
        if "storage.replica" in capabilities
        else "not-applicable: storage.replica is not advertised",
    }


def storage_conformance(plugin):
    """Exercise mandatory ledger/provider and every advertised optional suite."""
    declaration = storage_declaration(plugin)
    platform = storage_platform(plugin)
    if platform != "supported":
        return {"declaration": declaration, "status": platform, "cases": []}
    suites = set(Capability) - set(_OPTIONAL.values())
    suites.update(suite for name, suite in _OPTIONAL.items() if name in declaration["capabilities"])
    report = run_conformance(BackendAdapter(plugin.storage, frozenset(suites)))
    report.assert_success()
    return {
        "declaration": declaration,
        "status": "passed",
        "cases": [
            {"capability": result.capability, "case": result.case, "status": result.status}
            for result in report.results
        ],
    }


def _files(home):
    """Bound temporary native observations before allocating or reading payloads."""
    home, result, pending = Path(home), {}, [Path(home)]
    count = total = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                count += 1
                if count > 128 or len(path.relative_to(home).parts) > 8 or entry.is_symlink():
                    raise AssertionError(
                        "A fictional storage fixture exceeded its native-file boundary."
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    size = entry.stat(follow_symlinks=False).st_size
                    total += size
                    if size > 16 * 1024 * 1024 or total > 64 * 1024 * 1024:
                        raise AssertionError(
                            "A fictional storage fixture exceeded its native-file boundary."
                        )
                    with path.open("rb") as stream:
                        raw = stream.read(16 * 1024 * 1024 + 1)
                    if len(raw) != size:
                        raise AssertionError(
                            "A native fixture changed while its reader was quiescent."
                        )
                    result[path.relative_to(home).as_posix()] = hashlib.sha256(raw).hexdigest()
                else:
                    raise AssertionError(
                        "A native fixture contains an unsupported filesystem entry."
                    )
    return result


def assert_storage_readiness(plugin):
    """Prove public RO readiness and reopened durable operation identity.

    This is direct adapter conformance. Real worker activation, request leases and
    HTTPS management belong to the separate versioned native storage profile.
    """
    declaration = storage_declaration(plugin)
    if declaration["native"] != "supported":
        return {"status": "not-applicable", "reason": declaration["native"]}
    # Advertisement on an old host must not turn a broken capability into a skip.
    if Version(metadata.version("whab")) < Version(MINIMUM_STORAGE_HOST):
        raise ValueError("The advertised storage worker requires a compatible actual host SDK.")
    platform = storage_platform(plugin)
    if platform != "supported":
        return {"status": platform}
    from whab.plugins import StorageOperation
    from whab.plugins.storage_transfer import canonical_storage_json

    with TemporaryDirectory(prefix="whab-storage-readiness-") as temporary:
        root = Path(temporary).resolve()
        home = root / "native"
        home.mkdir()
        opened = []

        def open_context(context):
            backend = plugin.storage(context)
            opened.append(backend)
            return backend

        def close(backend):
            backend.close()
            opened.remove(backend)

        try:
            missing = root / "absent"
            unexpected = None
            try:
                unexpected = open_context(StorageContext(missing, "sandbox", {}, read_only=True))
            except Exception:
                pass
            if unexpected is not None or missing.exists():
                raise AssertionError(
                    "Read-only readiness created or accepted a missing native ledger."
                )
            context = StorageContext(home, "sandbox", {})
            backend = open_context(context)
            before = backend.commit_evidence()
            operation = StorageOperation(
                "fictional-readiness-request",
                before.authority_epoch,
                before.generation,
                "personal",
                "add_account",
                canonical_storage_json(
                    {
                        "name": "Fictional storage readiness",
                        "type": "depository",
                        "balance": 125,
                    }
                ),
            )
            accepted = backend.execute_operation(operation)
            snapshot, after = public_snapshot(backend), backend.commit_evidence()
            files = _files(home)
            reader = open_context(
                replace(context, read_only=True, expected_instance_id=after.instance_id)
            )
            if public_snapshot(reader) != snapshot or reader.commit_evidence() != after:
                raise AssertionError(
                    "Read-only readiness disagreed with the acknowledged native snapshot."
                )
            try:
                reader.execute_operation(
                    StorageOperation(
                        "fictional-ro-write",
                        after.authority_epoch,
                        after.generation,
                        "personal",
                        operation.name,
                        operation.payload,
                    )
                )
            except Exception:
                pass
            else:
                raise AssertionError("Read-only readiness accepted a financial write.")
            close(reader)
            # An active native reader may update engine-owned synchronization
            # marks (for example SQLite's existing shared memory). Financial
            # evidence and file membership must stay unchanged. Once stopped,
            # readiness must leave every native byte unchanged as checked below.
            if _files(home).keys() != files.keys() or backend.commit_evidence() != after:
                raise AssertionError("Read-only readiness changed native files or commit evidence.")
            close(backend)
            stopped = _files(home)
            reader = open_context(
                replace(context, read_only=True, expected_instance_id=after.instance_id)
            )
            if reader.commit_evidence() != after or public_snapshot(reader) != snapshot:
                raise AssertionError(
                    "Stopped read-only readiness changed the acknowledged native snapshot."
                )
            close(reader)
            if _files(home) != stopped:
                raise AssertionError("Stopped read-only readiness changed native files.")
            reopened = open_context(replace(context, expected_instance_id=after.instance_id))
            replay = reopened.execute_operation(operation)
            if (
                not replay.replayed
                or replay.receipt != accepted.receipt
                or public_snapshot(reopened) != snapshot
                or reopened.commit_evidence() != after
            ):
                raise AssertionError(
                    "Reopen did not preserve the exact native result and retry identity."
                )
            close(reopened)
            return {
                "status": "passed",
                "read_only": "unchanged",
                "reopen": "preserved",
                "retry": "exact-receipt",
            }
        finally:
            failed = False
            for backend in reversed(opened):
                try:
                    backend.close()
                except Exception:
                    failed = True
            if failed:
                raise RuntimeError(
                    "A storage readiness backend did not release its owned resources."
                ) from None
