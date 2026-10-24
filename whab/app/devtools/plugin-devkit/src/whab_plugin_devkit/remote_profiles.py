"""Exact-artifact remote-authority replay, separate from local migration/copies."""

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
from .remote_client import (
    MINIMUM_REMOTE_HOST,
    RemoteClientRuntime,
    assert_lost_remote_result,
    assert_remote_client,
    remote_declaration,
)

_FAULTS = ("invalid-selection", "rejected-wheel-hash", "kill-after-acknowledged-write", "dropped-public-result")
_UNSUPPORTED = ["local-migration", "local-copies", "automatic-unknown-reconciliation", "server-restart-retry-proof", "distinct-version-upgrade-without-approved-artifacts"]
_STEPS = ["resolve-remote-peers", "reference-conformance", "candidate-worker-readiness", "native-https-financial-services",
          "same-binding-pause-enable", "peer-transitions", "public-fault", "exact-worker-reopen", "cleanup"]
_RUNTIME_KEYS = {"profile", "minimum_host", "host", "requested", "applicability", "reference_role", "selections", "steps"}


def _host(artifacts):
    matches = [row["version"] for row in artifacts if row["distribution"].replace("_", "-").lower() == "whab"]
    if len(matches) != 1:
        raise ValueError("Remote replay requires one exact installed host artifact.")
    return matches[0]


def _applicability(host, requested):
    return ("not-requested" if not requested else "supported" if Version(host) >= Version(MINIMUM_REMOTE_HOST)
            else "not-applicable: host predates remote profile")


def _valid(candidate, names):
    try:
        resolve_plugins((candidate, *(_peer(name) for name in names)))
    except CapabilityError:
        return False
    return True


def sample_remote_configurations(candidate, reference, artifacts, *, combinations_count=10, seed=42, runtime=True):
    remote_declaration(candidate)
    if (reference.id != "sqlite" or candidate.id == reference.id or type(combinations_count) is not int
            or not 1 <= combinations_count <= 32 or type(seed) is not int or type(runtime) is not bool):
        raise ValueError("Choose distinct remote/reference identities, an integer seed and one to 32 configurations.")
    randomizer = random.Random(seed)
    valid = [peers for length in range(1, len(_PEERS) + 1) for peers in combinations(_PEERS, length) if _valid(candidate, peers)]
    if len(valid) < combinations_count:
        raise ValueError("The remote candidate cannot satisfy that many distinct reviewed peer configurations.")
    host = _host(artifacts)
    manifests = []
    for index, peers in enumerate(randomizer.sample(valid, combinations_count)):
        selected = [candidate.id, *peers]
        discovered, configured = selected.copy(), selected.copy()
        randomizer.shuffle(discovered)
        randomizer.shuffle(configured)
        initial = [name for name in configured if name != candidate.id]
        reduced = next((initial[:index] + initial[index + 1:] for index in range(len(initial))
                        if _valid(candidate, initial[:index] + initial[index + 1:])), initial)
        sequence = _SEQUENCES[index % len(_SEQUENCES)]
        if sequence == "replace-peer":
            reduced = next(([*reduced, name] for name in _PEERS if name not in initial and _valid(candidate, [*reduced, name])), reduced)
        if sequence == "reorder-peers" or reduced == initial:
            reduced, sequence = list(reversed(initial)), "reorder-peers"
        manifest = {"formatVersion": 3, "scope": "remote-client-lifecycle", "seed": seed, "index": index,
                    "python": platform.python_version(), "candidate": candidate.id, "reference": reference.id,
                    "peers": list(peers), "discovery_order": discovered, "configuration_order": configured,
                    "sequence": sequence, "fault": _FAULTS[index % len(_FAULTS)], "artifacts": artifacts,
                    "unsupported": _UNSUPPORTED.copy(),
                    "replay": f"whab-plugin replay configuration-{index:02d}.json --wheelhouse APPROVED_WHEELHOUSE",
                    "runtime": {"profile": "native-remote-client-v1", "minimum_host": MINIMUM_REMOTE_HOST,
                                "host": host, "requested": runtime, "applicability": _applicability(host, runtime),
                                "reference_role": "fictional-api-authority-backend",
                                "selections": [initial, reduced, initial], "steps": _STEPS.copy()}}
        manifests.append(validate_remote_manifest(manifest))
    return manifests


def validate_remote_manifest(manifest):
    if (type(manifest) is not dict or type(manifest.get("formatVersion")) is not int
            or manifest["formatVersion"] != 3 or manifest.get("scope") != "remote-client-lifecycle"):
        raise ValueError("Unsupported remote replay manifest schema.")
    # Reuse the established bounded identity/artifact validator. Its temporary
    # reference slot is schema validation only; actual remote resolution below
    # never co-selects the server's SQLite writer with the remote client writer.
    common = {key: value for key, value in manifest.items() if key != "runtime"}
    common.update(formatVersion=1, scope="resolver-and-public-hooks", fault="none",
                  unsupported=["live-coordinator", "migration", "copies", "promotion"])
    for key in ("discovery_order", "configuration_order"):
        if type(common.get(key)) is not list or "sqlite" in common[key]:
            raise ValueError("The local reference cannot be selected as a remote client peer.")
        common[key] = [*common[key], "sqlite"]
    validate_manifest(common)
    runtime = manifest.get("runtime")
    if type(runtime) is not dict or runtime.keys() != _RUNTIME_KEYS:
        raise ValueError("Unsupported remote profile fields.")
    if (runtime["profile"] != "native-remote-client-v1" or runtime["minimum_host"] != MINIMUM_REMOTE_HOST
            or runtime["host"] != _host(manifest["artifacts"]) or type(runtime["requested"]) is not bool
            or runtime["applicability"] != _applicability(runtime["host"], runtime["requested"])
            or runtime["reference_role"] != "fictional-api-authority-backend" or runtime["steps"] != _STEPS
            or manifest["fault"] not in _FAULTS or manifest["unsupported"] != _UNSUPPORTED):
        raise ValueError("Remote applicability, authority, fault or execution claims differ from the supported profile.")
    selections = runtime["selections"]
    initial = [name for name in manifest["configuration_order"] if name != manifest["candidate"]]
    if type(selections) is not list or len(selections) != 3 or selections[0] != initial or selections[-1] != initial:
        raise ValueError("Record every remote peer transition from and back to its original selection.")
    for selected in selections:
        if (type(selected) is not list or any(type(name) is not str for name in selected)
                or len(set(selected)) != len(selected) or not set(selected) <= set(_PEERS)):
            raise ValueError("Remote peer selections contain unknown or duplicate providers.")
    return manifest


def _check(condition):
    if not condition:
        raise AssertionError("A remote runtime profile violated its public contract.")


def _failed_operation(runtime, identifier):
    def observe():
        rows = runtime.expect("GET", "/api/plugins/runtime")["operations"]
        row = next((row for row in rows if row["id"] == identifier), None)
        if row and row["state"] == "complete":
            raise AssertionError("The deliberately invalid approved hash was accepted.")
        return row if row and row["state"] == "failed" else None
    return runtime.wait(observe, timeout=runtime.transition_timeout)


def replay_remote_configuration(manifest, candidate, reference, *, wheelhouse=None, native=RemoteClientRuntime):
    manifest = validate_remote_manifest(manifest)
    remote_declaration(candidate)
    if (candidate.id, reference.id) != (manifest["candidate"], manifest["reference"]):
        raise ValueError("Installed remote/reference descriptors differ from the recorded identities.")
    available = {plugin.id: plugin for plugin in (candidate, *(_peer(name) for name in _PEERS))}
    discovered = {name: available[name] for name in manifest["discovery_order"]}
    selected = tuple(discovered[name] for name in manifest["configuration_order"])
    baseline = resolve_plugins(selected)
    _check(baseline == resolve_plugins(tuple(reversed(selected))))
    registry = PluginRegistry(baseline)
    hooks = exercise_candidate(candidate, registry)
    peers = {name: exercise_candidate(available[name], registry) for name in manifest["peers"]}
    conformance = reference_conformance(reference)
    result = {"configuration": hashlib.sha256(_dump(manifest).encode()).hexdigest(), "candidate_hooks": hooks,
              "peer_hooks": peers, "resolved": [row.id for row in baseline], "conformance": conformance,
              "deterministic_negatives": deterministic_negatives()}
    profile = manifest["runtime"]
    if profile["applicability"] != "supported":
        return {**result, "runtime": {"status": profile["applicability"], "profile": profile["profile"]}}
    wheels = api_worker_wheels(candidate, manifest["artifacts"], wheelhouse)
    with native(candidate, profile["selections"][0]) as runtime:
        _check(runtime.expect("GET", "/api/health")["version"] == profile["host"])
        ticket = runtime.expect("POST", "/api/plugins/management/upgrade", {"pluginId": candidate.id, "wheels": wheels}, status=202)
        runtime.operation("/api/plugins/runtime", ticket["id"])
        worker = runtime.expect("GET", "/api/storage/remote/status")["worker"]
        _check(worker is not None)
        evidence = assert_remote_client(runtime, candidate)
        for selection in profile["selections"][1:]:
            resolved = resolve_plugins((candidate, *(available[name] for name in selection)))
            ticket = runtime.expect("POST", "/api/plugins/management/transition", {"plugins": selection}, status=202)
            runtime.operation("/api/plugins/runtime", ticket["id"])
            _check({row["id"] for row in runtime.expect("GET", "/api/plugins")["plugins"]} == {row.id for row in resolved})
            _check(runtime.expect("GET", "/api/storage/remote/status")["worker"] == worker)
        fault = manifest["fault"]
        if fault == "invalid-selection":
            runtime.expect("POST", "/api/plugins/management/transition", {"plugins": ["synthetic-not-installed"]}, status=409)
        if fault == "rejected-wheel-hash":
            invalid = [{**row, "sha256": "0" * 64} for row in wheels]
            ticket = runtime.expect("POST", "/api/plugins/management/upgrade", {"pluginId": candidate.id, "wheels": invalid}, status=202)
            _failed_operation(runtime, ticket["id"])
            _check(runtime.expect("GET", "/api/storage/remote/status")["worker"] == worker)
        if fault == "dropped-public-result":
            evidence.update(assert_lost_remote_result(runtime))
        before = runtime.expect("GET", "/api/workspace?workspace=personal")
        runtime.stop(kill=fault == "kill-after-acknowledged-write")
        runtime.start()
        _check(runtime.expect("GET", "/api/workspace?workspace=personal") == before)
        _check(runtime.expect("GET", "/api/storage/remote/status")["worker"] == worker)
        runtime.stop()
    cleanups = runtime.cleanups + runtime.server.cleanups
    _check(not runtime.home.exists() and not runtime.server.home.exists() and runtime.process is None and runtime.server.process is None)
    return {**result, "runtime": {"status": "passed", "profile": profile["profile"], "steps": profile["steps"],
                                 "selections": profile["selections"], "fault": manifest["fault"], "worker": worker,
                                 "evidence": evidence, "cleanup_complete": True, "cleanup_count": cleanups}}
