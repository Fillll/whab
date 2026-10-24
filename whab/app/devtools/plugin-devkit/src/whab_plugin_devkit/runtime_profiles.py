"""Versioned replay of real lifecycle, migration and copy management operations."""

import hashlib

from packaging.version import Version

from whab.plugins import PluginRegistry, resolve_plugins
from whab.plugins.contracts import CapabilityError

from .configurations import (
    _PEERS,
    _dump,
    _peer,
    exercise_candidate,
    replay_configuration,
    sample_configurations,
    validate_manifest,
)
from .runtime_harness import NativeRuntime

MINIMUM_RUNTIME_HOST = "2.32.0"
_FAULTS = ("invalid-selection", "consumed-preview", "copy-source-changed", "kill-after-acknowledged-write")
_RUNTIME_KEYS = {"profile", "minimum_host", "host", "applicability", "candidate_applicability", "primary", "destination", "sequence", "selections", "candidate_selection", "fault", "steps"}
_STEPS = ["candidate-public-hooks", "live-selections", "attach-copy", "acknowledged-write", "copy-catch-up", "promote-copy", "migrate-back", "restart", "verify-data", "cleanup"]


def _candidate_support(plugin):
    # The initial profile must not start providers, interfaces or arbitrary
    # context binders as a side effect of testing a separate public hook.
    return ("not-applicable: unsupported candidate hooks" if any(getattr(plugin, name) is not None for name in (
        "bank", "bank_ui", "storage", "ui", "interface", "bind", "budget_connection", "migrate_state",
    )) else "supported")


def _host(artifacts):
    matches = [row["version"] for row in artifacts if row["distribution"].replace("_", "-").lower() == "whab"]
    if len(matches) != 1:
        raise ValueError("Runtime replay requires one exact host artifact.")
    return matches[0]


def _base(manifest):
    return {**{key: value for key, value in manifest.items() if key != "runtime"},
            "formatVersion": 1, "scope": "resolver-and-public-hooks",
            "unsupported": ["live-coordinator", "migration", "copies", "promotion"]}


def sample_runtime_configurations(candidate, reference, artifacts, *, combinations_count=10, seed=42):
    manifests = sample_configurations(candidate, reference, artifacts, combinations_count=combinations_count, seed=seed)
    host = _host(artifacts)
    for manifest in manifests:
        initial = [name for name in manifest["configuration_order"] if name != reference.id]
        available = {plugin.id: plugin for plugin in (reference, *(_peer(name) for name in _PEERS), candidate)}

        def valid(selection):
            try:
                resolve_plugins(tuple(available[name] for name in [reference.id, *selection]))
            except CapabilityError:
                return False
            return True
        reduced = next((initial[:index] + initial[index + 1:] for index, name in enumerate(initial)
                        if name != candidate.id and valid(initial[:index] + initial[index + 1:])), list(reversed(initial)))
        sequence = manifest["sequence"]
        middle = reduced
        if sequence == "replace-peer":
            middle = next(([*reduced, name] for name in _PEERS if name not in initial and valid([*reduced, name])), reduced)
            if middle == reduced:
                sequence = "disable-enable-peer"
        if sequence == "reorder-peers":
            middle = list(reversed(initial))
        selections = [initial, middle, initial]
        live_candidate = (_candidate_support(candidate) == "supported"
                          and candidate.contract is not None and candidate.contract.lifecycle == "live")
        candidate_reduced = [name for name in initial if name != candidate.id]
        candidate_selection = "not-applicable: candidate profile constraints"
        if live_candidate and valid(candidate_reduced):
            selections.extend([candidate_reduced, initial])
            candidate_selection = "disable-enable"
        primary = "sqlite" if manifest["index"] % 2 == 0 else "json"
        manifest.update(formatVersion=2, scope="runtime-lifecycle-storage",
                        unsupported=["worker-code-upgrade", "private-durable-boundary-injection", "provider-network"],
                        runtime={"profile": "native-local-v1", "minimum_host": MINIMUM_RUNTIME_HOST, "host": host,
                                 "applicability": "supported" if Version(host) >= Version(MINIMUM_RUNTIME_HOST) else "not-applicable: host predates runtime profile",
                                 "candidate_applicability": _candidate_support(candidate),
                                 "primary": primary, "destination": "json" if primary == "sqlite" else "sqlite",
                                 "sequence": sequence, "selections": selections, "candidate_selection": candidate_selection,
                                 "fault": _FAULTS[manifest["index"] % len(_FAULTS)], "steps": _STEPS.copy()})
    return manifests


def validate_runtime_manifest(manifest):
    if type(manifest) is not dict or manifest.get("formatVersion") != 2 or type(manifest["formatVersion"]) is not int or manifest.get("scope") != "runtime-lifecycle-storage":
        raise ValueError("Unsupported runtime manifest schema.")
    validate_manifest(_base(manifest))
    runtime = manifest.get("runtime")
    if type(runtime) is not dict or set(runtime) != _RUNTIME_KEYS:
        raise ValueError("Unsupported runtime profile fields.")
    if runtime["profile"] != "native-local-v1" or runtime["minimum_host"] != MINIMUM_RUNTIME_HOST or runtime["host"] != _host(manifest["artifacts"]):
        raise ValueError("Runtime profile and recorded host artifact differ.")
    expected = "supported" if Version(runtime["host"]) >= Version(MINIMUM_RUNTIME_HOST) else "not-applicable: host predates runtime profile"
    if runtime["applicability"] != expected or runtime["steps"] != _STEPS or runtime["fault"] not in _FAULTS:
        raise ValueError("Runtime profile makes an unsupported applicability or execution claim.")
    if runtime["candidate_applicability"] not in {"supported", "not-applicable: unsupported candidate hooks"}:
        raise ValueError("Record the candidate's actual supported runtime family.")
    if runtime["primary"] not in {"sqlite", "json"} or runtime["destination"] != ("json" if runtime["primary"] == "sqlite" else "sqlite"):
        raise ValueError("Use opposite reviewed local primary and destination backends.")
    if manifest["unsupported"] != ["worker-code-upgrade", "private-durable-boundary-injection", "provider-network"]:
        raise ValueError("Runtime replay cannot claim unsupported extensions.")
    selections = runtime["selections"]
    allowed = {manifest["candidate"], *_PEERS}
    if type(selections) is not list or not 3 <= len(selections) <= 5:
        raise ValueError("Record every bounded runtime selection explicitly.")
    for selected in selections:
        if type(selected) is not list or any(type(name) is not str for name in selected) or len(set(selected)) != len(selected) or not set(selected) <= allowed:
            raise ValueError("A runtime sequence contains unknown, duplicate or executable providers.")
    initial = [name for name in manifest["configuration_order"] if name != manifest["reference"]]
    if selections[0] != initial or selections[-1] != initial or runtime["candidate_selection"] not in {"disable-enable", "not-applicable: candidate profile constraints"}:
        raise ValueError("Runtime transitions must begin and finish with the recorded candidate selection.")
    if runtime["sequence"] not in {"disable-enable-peer", "replace-peer", "reorder-peers"}:
        raise ValueError("Use one supported explicit live selection sequence.")
    omitted = any(manifest["candidate"] not in selected for selected in selections)
    if omitted != (runtime["candidate_selection"] == "disable-enable"):
        raise ValueError("Candidate applicability must match its actual selection sequence.")
    return manifest


def _check(condition):
    if not condition:
        raise AssertionError("A runtime profile violated its public contract.")


def _selection(runtime, selection):
    ticket = runtime.expect("POST", "/api/plugins/management/transition", {"plugins": selection}, status=202)
    runtime.operation("/api/plugins/runtime", ticket["id"])
    actual = runtime.expect("GET", "/api/plugins")["plugins"]
    _check({row["id"] for row in actual} - {"sqlite", "json"} == set(selection))


def _copy_saved(runtime, identifier):
    def observe():
        status = runtime.expect("GET", "/api/storage/copies")
        row = next(row for row in status["copies"] if row["id"] == identifier)
        if row["state"] in {"failed", "unavailable", "reattach_required"}:
            raise AssertionError("The required copy did not synchronize its acknowledged primary.")
        return row if row["state"] == "saved" else None
    return runtime.wait(observe)


def _migrate(runtime, destination):
    preview = runtime.expect("POST", "/api/storage/preview", {"destination": destination})
    ticket = runtime.expect("POST", "/api/storage/transition", {"preview_id": preview["preview_id"]}, status=202)
    runtime.operation("/api/storage/status", ticket["id"])
    _check(runtime.expect("GET", "/api/storage/status")["storage"] == destination)
    return preview["preview_id"]


def replay_runtime_configuration(manifest, candidate, reference, *, native=NativeRuntime):
    """Run installed SDK hooks plus actual public HTTPS runtime operations."""
    manifest = validate_runtime_manifest(manifest)
    baseline = replay_configuration(_base(manifest), candidate, reference)
    profile = manifest["runtime"]
    if profile["candidate_applicability"] != _candidate_support(candidate):
        raise ValueError("The installed candidate does not match its runtime applicability declaration.")
    if profile["applicability"] != "supported":
        return {**baseline, "runtime": {"status": "not-applicable", "reason": profile["applicability"]}}
    if profile["candidate_applicability"] != "supported":
        return {**baseline, "runtime": {"status": "not-applicable", "reason": profile["candidate_applicability"]}}
    # Re-resolve every recorded stage before starting the child. No manifest may
    # exploit the reusable harness to select arbitrary installed community code.
    available = {row.id: row for row in (reference, *(_peer(name) for name in _PEERS), candidate)}
    for selection in profile["selections"]:
        resolve_plugins(tuple(available[name] for name in [reference.id, *selection]))
    with native(profile["primary"], profile["selections"][0]) as runtime:
        _check(runtime.expect("GET", "/api/health")["version"] == profile["host"])
        registry = PluginRegistry(tuple(available[name] for name in [reference.id, *profile["selections"][0]]))
        candidate_hooks = exercise_candidate(candidate, registry)
        if profile["fault"] == "invalid-selection":
            runtime.expect("POST", "/api/plugins/management/transition", {"plugins": [*profile["selections"][0], "synthetic-not-installed"]}, status=409)
        for selection in profile["selections"][1:]:
            _selection(runtime, selection)
        preview = runtime.expect("POST", "/api/storage/copies/preview", {"destination": profile["destination"]})
        attached = runtime.expect("POST", "/api/storage/copies/transition", {"preview_id": preview["preview_id"]}, status=202)
        copy_id = attached["id"]
        account = runtime.expect("POST", "/api/accounts", {"workspace": "personal", "name": "Fictional runtime wallet", "type": "depository", "balance": 125})["accountId"]
        _copy_saved(runtime, copy_id)
        promoted = runtime.expect("POST", f"/api/storage/copies/{copy_id}/promote", {}, status=202)
        runtime.operation("/api/storage/copies", promoted["id"])
        _check(runtime.expect("GET", "/api/storage/status")["storage"] == profile["destination"])
        consumed = _migrate(runtime, profile["primary"])
        if profile["fault"] == "consumed-preview":
            runtime.expect("POST", "/api/storage/transition", {"preview_id": consumed}, status=400)
        if profile["fault"] == "copy-source-changed":
            second = runtime.expect("POST", "/api/storage/copies/preview", {"destination": profile["destination"]})
            second_id = runtime.expect("POST", "/api/storage/copies/transition", {"preview_id": second["preview_id"]}, status=202)["id"]
            _copy_saved(runtime, second_id)
            _migrate(runtime, profile["destination"])
            row = next(row for row in runtime.expect("GET", "/api/storage/copies")["copies"] if row["id"] == second_id)
            _check(row["state"] == "reattach_required")
        before = runtime.expect("GET", "/api/workspace?workspace=personal&month=2026-10")
        _check(any(row["id"] == account and row["balance"] == 125 for row in before["accounts"]))
        runtime.stop(kill=profile["fault"] == "kill-after-acknowledged-write")
        runtime.start()
        _check(runtime.expect("GET", "/api/workspace?workspace=personal&month=2026-10") == before)
        _check(candidate.id in {row["id"] for row in runtime.expect("GET", "/api/plugins")["plugins"]})
        _check(exercise_candidate(candidate, registry) == candidate_hooks)
        runtime.stop()
        cleanups = runtime.cleanups
    _check(not runtime.home.exists() and runtime.process is None)
    return {**baseline, "configuration": hashlib.sha256(_dump(manifest).encode()).hexdigest(),
            "runtime": {"status": "passed", "profile": profile["profile"], "primary": profile["primary"],
                        "destination": profile["destination"], "fault": profile["fault"],
                        "steps": profile["steps"], "sequence": profile["sequence"], "selections": profile["selections"],
                        "candidate_selection": profile["candidate_selection"], "cleanup_count": cleanups,
                        "cleanup_complete": True}}
