"""Version-four replay with one explicitly reviewed local storage primary."""

import hashlib
import platform
import random
from itertools import combinations

from packaging.version import Version

from whab.plugins import PluginRegistry, resolve_plugins
from whab.plugins.contracts import CapabilityError

from .api_interface import api_worker_wheels
from .configurations import (
    _PEERS,
    _SEQUENCES,
    _dump,
    _peer,
    deterministic_negatives,
    exercise_candidate,
    reference_conformance,
    validate_manifest,
)
from .local_storage import (
    MINIMUM_STORAGE_HOST,
    assert_storage_readiness,
    storage_conformance,
    storage_declaration,
    storage_platform,
)
from .runtime_harness import NativeRuntime

_PROFILE = "native-local-storage-v1"
_FAULTS = ("invalid-selection", "rejected-wheel-hash", "kill-after-acknowledged-write")
_UNSUPPORTED = [
    "external-providers",
    "private-commit-boundary-injection",
    "distinct-version-upgrade-without-approved-artifacts",
]
_STEPS = [
    "candidate-storage-conformance",
    "read-only-native-readiness",
    "native-https-ledger",
    "approved-worker-activation",
    "acknowledged-worker-mutation",
    "required-primary-peer-transitions",
    "advertised-transfer-copy-controls",
    "public-fault",
    "exact-worker-reopen",
    "acknowledged-mutation-after-reopen",
    "acknowledged-data",
    "cleanup",
]
_RUNTIME_KEYS = {
    "profile",
    "minimum_host",
    "host",
    "requested",
    "applicability",
    "reference_role",
    "selections",
    "steps",
}


def _host(artifacts):
    matches = [
        row["version"]
        for row in artifacts
        if row["distribution"].replace("_", "-").lower() == "whab"
    ]
    if len(matches) != 1:
        raise ValueError("Storage replay requires one exact installed host artifact.")
    return matches[0]


def _applicability(host, requested):
    return (
        "not-requested"
        if not requested
        else "supported"
        if Version(host) >= Version(MINIMUM_STORAGE_HOST)
        else "not-applicable: host predates storage profile"
    )


def _valid(candidate, peers):
    try:
        resolve_plugins((candidate, *(_peer(name) for name in peers)))
    except CapabilityError:
        return False
    return True


def sample_storage_configurations(
    candidate, reference, artifacts, *, combinations_count=10, seed=42, runtime=True
):
    """Sample distinct peer sets without selecting the reference writer."""
    storage_declaration(candidate)
    if (
        reference.id != "sqlite"
        or candidate.id == reference.id
        or type(combinations_count) is not int
        or not 1 <= combinations_count <= 32
        or type(seed) is not int
        or type(runtime) is not bool
    ):
        raise ValueError(
            "Choose distinct storage/reference identities, an integer seed and one to 32 configurations."
        )
    valid = [
        peers
        for length in range(1, len(_PEERS) + 1)
        for peers in combinations(_PEERS, length)
        if _valid(candidate, peers)
    ]
    if len(valid) < combinations_count:
        raise ValueError(
            "The storage candidate cannot satisfy that many distinct reviewed peer sets."
        )
    randomizer = random.Random(seed)
    host, manifests = _host(artifacts), []
    for index, peers in enumerate(randomizer.sample(valid, combinations_count)):
        selected = [candidate.id, *peers]
        discovered, configured = selected.copy(), selected.copy()
        randomizer.shuffle(discovered)
        randomizer.shuffle(configured)
        initial = [name for name in configured if name != candidate.id]
        reduced = next(
            (
                initial[:i] + initial[i + 1 :]
                for i in range(len(initial))
                if _valid(candidate, initial[:i] + initial[i + 1 :])
            ),
            initial,
        )
        sequence = _SEQUENCES[index % len(_SEQUENCES)]
        if sequence == "replace-peer":
            reduced = next(
                (
                    [*reduced, name]
                    for name in _PEERS
                    if name not in initial and _valid(candidate, [*reduced, name])
                ),
                reduced,
            )
        if sequence == "reorder-peers" or reduced == initial:
            reduced, sequence = list(reversed(initial)), "reorder-peers"
        manifests.append(
            validate_storage_manifest(
                {
                    "formatVersion": 4,
                    "scope": "local-storage-lifecycle",
                    "seed": seed,
                    "index": index,
                    "python": platform.python_version(),
                    "candidate": candidate.id,
                    "reference": reference.id,
                    "peers": list(peers),
                    "discovery_order": discovered,
                    "configuration_order": configured,
                    "sequence": sequence,
                    "fault": _FAULTS[index % len(_FAULTS)],
                    "artifacts": artifacts,
                    "unsupported": _UNSUPPORTED.copy(),
                    "replay": f"whab-plugin replay configuration-{index:02d}.json --wheelhouse APPROVED_WHEELHOUSE",
                    "runtime": {
                        "profile": _PROFILE,
                        "minimum_host": MINIMUM_STORAGE_HOST,
                        "host": host,
                        "requested": runtime,
                        "applicability": _applicability(host, runtime),
                        "reference_role": "independent-conformance-only",
                        "selections": [initial, reduced, initial],
                        "steps": _STEPS.copy(),
                    },
                }
            )
        )
    return manifests


def validate_storage_manifest(manifest):
    if (
        type(manifest) is not dict
        or type(manifest.get("formatVersion")) is not int
        or manifest["formatVersion"] != 4
        or manifest.get("scope") != "local-storage-lifecycle"
    ):
        raise ValueError("Unsupported storage replay manifest schema.")
    # Reuse the established bounded identity/artifact reader. The reference slot
    # here is validation only; it is never co-selected with the candidate writer.
    common = {key: value for key, value in manifest.items() if key != "runtime"}
    common.update(
        formatVersion=1,
        scope="resolver-and-public-hooks",
        fault="none",
        unsupported=["live-coordinator", "migration", "copies", "promotion"],
    )
    for key in ("discovery_order", "configuration_order"):
        if type(common.get(key)) is not list or "sqlite" in common[key]:
            raise ValueError("The independent reference cannot be a storage candidate peer.")
        common[key] = [*common[key], "sqlite"]
    validate_manifest(common)
    runtime = manifest.get("runtime")
    if type(runtime) is not dict or runtime.keys() != _RUNTIME_KEYS:
        raise ValueError("Unsupported storage profile fields.")
    if (
        runtime["profile"] != _PROFILE
        or runtime["minimum_host"] != MINIMUM_STORAGE_HOST
        or runtime["host"] != _host(manifest["artifacts"])
        or type(runtime["requested"]) is not bool
        or runtime["applicability"] != _applicability(runtime["host"], runtime["requested"])
        or runtime["reference_role"] != "independent-conformance-only"
        or runtime["steps"] != _STEPS
        or manifest["fault"] not in _FAULTS
        or manifest["unsupported"] != _UNSUPPORTED
    ):
        raise ValueError(
            "Storage profile claims differ from its supported public execution contract."
        )
    selections = runtime["selections"]
    initial = [name for name in manifest["configuration_order"] if name != manifest["candidate"]]
    if (
        type(selections) is not list
        or len(selections) != 3
        or selections[0] != initial
        or selections[-1] != initial
    ):
        raise ValueError("Record every peer transition from and back to its original selection.")
    for selected in selections:
        if (
            type(selected) is not list
            or any(type(name) is not str for name in selected)
            or len(set(selected)) != len(selected)
            or not set(selected) <= set(_PEERS)
        ):
            raise ValueError("Storage peer selections contain unknown or duplicate providers.")
    return manifest


def _check(condition):
    if not condition:
        raise AssertionError("A storage candidate violated its public native runtime contract.")


def _code(runtime, candidate):
    status = runtime.expect("GET", "/api/storage/status")
    _check(
        status["storage"] == candidate.id
        and status["code"]["mode"] == "worker"
        and status["code"]["active"] is not None
        and not status["code"]["uncertain"]
    )
    return status["code"]["active"]


def _account(runtime, label, balance):
    return runtime.expect(
        "POST",
        "/api/accounts",
        {
            "workspace": "personal",
            "name": label,
            "type": "depository",
            "balance": balance,
        },
    )["accountId"]


def _acknowledged(snapshot, accounts):
    _check(all(
        any(row["id"] == identifier and row["balance"] == balance for row in snapshot["accounts"])
        for identifier, balance in accounts
    ))


def _extensions(runtime, candidate, declaration):
    """Exercise advertised controls; absent optional contracts stay inapplicable."""
    from .runtime_profiles import _copy_saved, _migrate

    evidence = {name: declaration[name] for name in ("same_device_transfer", "copies")}
    if declaration["copies"] == "supported":
        preview = runtime.expect("POST", "/api/storage/copies/preview", {"destination": "sqlite"})
        ticket = runtime.expect(
            "POST",
            "/api/storage/copies/transition",
            {"preview_id": preview["preview_id"]},
            status=202,
        )
        _copy_saved(runtime, ticket["id"])
        promoted = runtime.expect(
            "POST", f"/api/storage/copies/{ticket['id']}/promote", {}, status=202
        )
        runtime.operation("/api/storage/copies", promoted["id"])
        _migrate(runtime, candidate.id)
    elif declaration["same_device_transfer"] == "supported":
        _migrate(runtime, "sqlite")
        _migrate(runtime, candidate.id)
    return evidence


def replay_storage_configuration(
    manifest, candidate, reference, *, wheelhouse=None, native=NativeRuntime
):
    manifest = validate_storage_manifest(manifest)
    declaration = storage_declaration(candidate)
    if (candidate.id, reference.id) != (manifest["candidate"], manifest["reference"]):
        raise ValueError(
            "Installed storage/reference descriptors differ from the recorded identities."
        )
    available = {row.id: row for row in (candidate, *(_peer(name) for name in _PEERS))}
    selected = tuple(available[name] for name in manifest["configuration_order"])
    resolved = resolve_plugins(selected)
    _check(resolved == resolve_plugins(tuple(reversed(selected))))
    registry = PluginRegistry(resolved)
    peers = {name: exercise_candidate(available[name], registry) for name in manifest["peers"]}
    conformance, readiness = storage_conformance(candidate), assert_storage_readiness(candidate)
    result = {
        "configuration": hashlib.sha256(_dump(manifest).encode()).hexdigest(),
        "candidate_hooks": ["storage"],
        "peer_hooks": peers,
        "resolved": [row.id for row in resolved],
        "conformance": conformance,
        "readiness": readiness,
        "reference_conformance": reference_conformance(reference),
        "deterministic_negatives": deterministic_negatives(),
    }
    profile = manifest["runtime"]
    applicability = storage_platform(candidate)
    if profile["applicability"] != "supported" or applicability != "supported":
        return {
            **result,
            "runtime": {
                "status": profile["applicability"]
                if applicability == "supported"
                else applicability,
                "profile": _PROFILE,
            },
        }
    if declaration["native"] != "supported":
        return {**result, "runtime": {"status": declaration["native"], "profile": _PROFILE}}
    wheels = api_worker_wheels(candidate, manifest["artifacts"], wheelhouse)
    with native(candidate.id, profile["selections"][0], storage_candidate=candidate) as runtime:
        _check(runtime.expect("GET", "/api/health")["version"] == profile["host"])
        accounts = [(_account(runtime, "Fictional pre-worker wallet", 125), 125)]
        runtime.expect(
            "POST",
            "/api/accounts",
            {
                "workspace": "personal",
                "name": "Invalid minor units",
                "type": "depository",
                "balance": True,
            },
            status=400,
        )
        ticket = runtime.expect(
            "POST",
            "/api/plugins/management/upgrade",
            {"pluginId": candidate.id, "wheels": wheels},
            status=202,
        )
        runtime.operation("/api/plugins/runtime", ticket["id"])
        active = _code(runtime, candidate)
        accounts.append((_account(runtime, "Fictional worker wallet", 250), 250))
        for selection in profile["selections"][1:]:
            expected = resolve_plugins((candidate, *(available[name] for name in selection)))
            ticket = runtime.expect(
                "POST", "/api/plugins/management/transition", {"plugins": selection}, status=202
            )
            runtime.operation("/api/plugins/runtime", ticket["id"])
            _check(
                {row["id"] for row in runtime.expect("GET", "/api/plugins")["plugins"]}
                == {row.id for row in expected}
            )
            _check(_code(runtime, candidate) == active)
        extensions = _extensions(runtime, candidate, declaration)
        if manifest["fault"] == "invalid-selection":
            runtime.expect(
                "POST",
                "/api/plugins/management/transition",
                {"plugins": ["synthetic-not-installed"]},
                status=409,
            )
        if manifest["fault"] == "rejected-wheel-hash":
            from .remote_profiles import _failed_operation

            ticket = runtime.expect(
                "POST",
                "/api/plugins/management/upgrade",
                {
                    "pluginId": candidate.id,
                    "wheels": [{**row, "sha256": "0" * 64} for row in wheels],
                },
                status=202,
            )
            _failed_operation(runtime, ticket["id"])
            _check(_code(runtime, candidate) == active)
        before = runtime.expect("GET", "/api/workspace?workspace=personal&month=2026-10")
        _acknowledged(before, accounts)
        runtime.stop(kill=manifest["fault"] == "kill-after-acknowledged-write")
        runtime.start()
        _check(runtime.expect("GET", "/api/workspace?workspace=personal&month=2026-10") == before)
        _check(_code(runtime, candidate) == active)
        accounts.append((_account(runtime, "Fictional reopened worker wallet", 375), 375))
        after = runtime.expect("GET", "/api/workspace?workspace=personal&month=2026-10")
        _acknowledged(after, accounts)
        runtime.stop()
        runtime.start()
        _check(runtime.expect("GET", "/api/workspace?workspace=personal&month=2026-10") == after)
        _check(_code(runtime, candidate) == active)
        runtime.stop()
    _check(not runtime.home.exists() and runtime.process is None)
    return {
        **result,
        "runtime": {
            "status": "passed",
            "profile": _PROFILE,
            "primary": candidate.id,
            "selections": profile["selections"],
            "steps": profile["steps"],
            "fault": manifest["fault"],
            "worker_selection_sha256": hashlib.sha256(_dump(active).encode()).hexdigest(),
            "acknowledged_writes": len(accounts),
            "extensions": extensions,
            "cleanup_complete": True,
            "cleanup_count": runtime.cleanups,
        },
    }
