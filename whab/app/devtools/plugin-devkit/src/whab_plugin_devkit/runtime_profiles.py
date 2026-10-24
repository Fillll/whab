"""Versioned replay of real lifecycle, migration and copy management operations."""

import hashlib

from packaging.version import Version

from whab.plugins import PluginRegistry, resolve_plugins
from whab.plugins.contracts import CapabilityError

from .api_interface import (
    MINIMUM_API_HOST,
    api_declaration,
    api_worker_wheels,
    assert_api_interface,
)
from .bank_conformance import (
    BANK_VARIANTS,
    MINIMUM_BANK_HOST,
    BankRuntime,
    bank_declaration,
    disconnect_bank,
    exercise_bank_runtime,
    fictional_scenario,
)
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
from .visual_interface import (
    MINIMUM_UI_HOST,
    assert_visual_interface,
    visual_change,
    visual_declaration,
)

MINIMUM_RUNTIME_HOST = "2.32.0"
_FAULTS = ("invalid-selection", "consumed-preview", "copy-source-changed", "kill-after-acknowledged-write")
_RUNTIME_KEYS = {"profile", "minimum_host", "host", "applicability", "candidate_applicability", "primary", "destination", "sequence", "selections", "candidate_selection", "fault", "steps"}
_API_PROFILE = "native-api-v1"
_UI_PROFILE = "native-ui-v1"
_BANK_PROFILE = "native-bank-v1"
_STEPS = ["candidate-public-hooks", "live-selections", "attach-copy", "acknowledged-write", "copy-catch-up", "promote-copy", "migrate-back", "restart", "verify-data", "cleanup"]



def _api_candidate(plugin):
    if getattr(plugin, "interface", None) is None:
        return None
    try:
        declaration = api_declaration(plugin)
    except ValueError:
        return None
    return declaration if declaration["queries"] and declaration["commands"] else None


def _profile(plugin):
    return (_BANK_PROFILE if _bank_candidate(plugin) else _API_PROFILE if _api_candidate(plugin) is not None
            else _UI_PROFILE if _ui_candidate(plugin) else "native-local-v1")


def _bank_candidate(plugin):
    if getattr(plugin, "bank", None) is None:
        return False
    try:
        bank_declaration(plugin)
    except ValueError:
        return False
    return True


def _ui_candidate(plugin):
    if getattr(plugin, "ui", None) is None:
        return False
    try:
        visual_declaration(plugin)
    except ValueError:
        return False
    return True


def _minimum(profile):
    return {_API_PROFILE: MINIMUM_API_HOST, _UI_PROFILE: MINIMUM_UI_HOST, _BANK_PROFILE: MINIMUM_BANK_HOST}.get(profile, MINIMUM_RUNTIME_HOST)


def _steps(profile):
    if profile == _API_PROFILE:
        return ["api-worker-readiness", "api-auth-command-retry", *_STEPS, "api-reopen-query"]
    if profile == _UI_PROFILE:
        return ["ui-worker-readiness", "ui-pages-assets", "ui-dirty-view-ack", *_STEPS, "ui-reopen-generation"]
    if profile == _BANK_PROFILE:
        return ["bank-worker-readiness", "bank-continuations", "bank-pending-annotations", *_STEPS,
                "bank-reopen-cursor", "bank-disconnect"]
    return _STEPS.copy()


def _candidate_support(plugin, bank_fixture=None):
    if _bank_candidate(plugin):
        return "supported" if bank_fixture == "fictional-v1" else "not-applicable: unapproved bank fixture"
    if _ui_candidate(plugin):
        return "supported"
    # The initial profile must not start providers, interfaces or arbitrary
    # context binders as a side effect of testing a separate public hook.
    return ("not-applicable: unsupported candidate hooks" if any(getattr(plugin, name) is not None for name in (
        "bank", "bank_ui", "storage", "ui", "bind", "budget_connection", "migrate_state",
    )) or (getattr(plugin, "interface", None) is not None and _api_candidate(plugin) is None) else "supported")


def _host(artifacts):
    matches = [row["version"] for row in artifacts if row["distribution"].replace("_", "-").lower() == "whab"]
    if len(matches) != 1:
        raise ValueError("Runtime replay requires one exact host artifact.")
    return matches[0]


def _base(manifest):
    return {**{key: value for key, value in manifest.items() if key != "runtime"},
            "formatVersion": 1, "scope": "resolver-and-public-hooks",
            "unsupported": ["live-coordinator", "migration", "copies", "promotion"]}


def sample_runtime_configurations(candidate, reference, artifacts, *, combinations_count=10, seed=42, bank_fixture=None):
    if bank_fixture is not None and (bank_fixture != "fictional-v1" or not _bank_candidate(candidate)):
        raise ValueError("Opt in only a reviewed pure offline fictional-v1 bank fixture.")
    manifests = sample_configurations(candidate, reference, artifacts, combinations_count=combinations_count, seed=seed)
    host = _host(artifacts)
    profile = _profile(candidate)
    minimum = _minimum(profile)
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
        live_candidate = (_candidate_support(candidate, bank_fixture) == "supported"
                          and candidate.contract is not None and candidate.contract.lifecycle == "live")
        candidate_reduced = [name for name in initial if name != candidate.id]
        candidate_selection = "not-applicable: candidate profile constraints"
        if live_candidate and valid(candidate_reduced):
            selections.extend([candidate_reduced, initial])
            candidate_selection = "disable-enable"
        primary = "sqlite" if manifest["index"] % 2 == 0 else "json"
        manifest.update(formatVersion=2, scope="runtime-lifecycle-storage",
                        unsupported=["worker-code-upgrade", "private-durable-boundary-injection", "provider-network"],
                        runtime={"profile": profile, "minimum_host": minimum, "host": host,
                                 "applicability": "supported" if Version(host) >= Version(minimum) else "not-applicable: host predates runtime profile",
                                 "candidate_applicability": _candidate_support(candidate, bank_fixture),
                                 "primary": primary, "destination": "json" if primary == "sqlite" else "sqlite",
                                 "sequence": sequence, "selections": selections, "candidate_selection": candidate_selection,
                                 "fault": _FAULTS[manifest["index"] % len(_FAULTS)], "steps": _steps(profile)})
        if profile == _BANK_PROFILE:
            manifest["runtime"]["bank"] = {"fixture": bank_fixture, "variant": BANK_VARIANTS[(manifest["index"] // 2) % len(BANK_VARIANTS)]}
    return manifests


def validate_runtime_manifest(manifest):
    if type(manifest) is not dict or manifest.get("formatVersion") != 2 or type(manifest["formatVersion"]) is not int or manifest.get("scope") != "runtime-lifecycle-storage":
        raise ValueError("Unsupported runtime manifest schema.")
    validate_manifest(_base(manifest))
    runtime = manifest.get("runtime")
    if type(runtime) is not dict or set(runtime) != (_RUNTIME_KEYS | ({"bank"} if runtime.get("profile") == _BANK_PROFILE else set())):
        raise ValueError("Unsupported runtime profile fields.")
    if runtime["profile"] not in {"native-local-v1", _API_PROFILE, _UI_PROFILE, _BANK_PROFILE} or runtime["minimum_host"] != _minimum(runtime["profile"]) or runtime["host"] != _host(manifest["artifacts"]):
        raise ValueError("Runtime profile and recorded host artifact differ.")
    expected = "supported" if Version(runtime["host"]) >= Version(_minimum(runtime["profile"])) else "not-applicable: host predates runtime profile"
    if runtime["applicability"] != expected or runtime["steps"] != _steps(runtime["profile"]) or runtime["fault"] not in _FAULTS:
        raise ValueError("Runtime profile makes an unsupported applicability or execution claim.")
    if runtime["candidate_applicability"] not in {"supported", "not-applicable: unsupported candidate hooks", "not-applicable: unapproved bank fixture"}:
        raise ValueError("Record the candidate's actual supported runtime family.")
    if runtime["profile"] == _BANK_PROFILE:
        bank = runtime["bank"]
        if (type(bank) is not dict or set(bank) != {"fixture", "variant"} or bank["fixture"] not in (None, "fictional-v1")
                or bank["variant"] not in BANK_VARIANTS or runtime["candidate_applicability"] != (
                    "supported" if bank["fixture"] == "fictional-v1" else "not-applicable: unapproved bank fixture")):
            raise ValueError("Use one explicit reviewed versioned fictional bank fixture and flow variant.")
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


def _selection(runtime, selection, *, visual=None):
    ticket = runtime.expect("POST", "/api/plugins/management/transition", {"plugins": [name for name in selection if name != visual]}, status=202)
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


def replay_runtime_configuration(manifest, candidate, reference, *, native=NativeRuntime, wheelhouse=None):
    """Run installed SDK hooks plus actual public HTTPS runtime operations."""
    manifest = validate_runtime_manifest(manifest)
    baseline = replay_configuration(_base(manifest), candidate, reference)
    profile = manifest["runtime"]
    fixture = profile.get("bank", {}).get("fixture")
    if profile["profile"] != _profile(candidate) or profile["candidate_applicability"] != _candidate_support(candidate, fixture):
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
    api = _api_candidate(candidate) if profile["profile"] == _API_PROFILE else None
    options = {"api_token_setting": api["token_setting"]} if api is not None else {}
    visual = candidate.id if profile["profile"] == _UI_PROFILE else None
    bank = profile["profile"] == _BANK_PROFILE
    if bank and native is NativeRuntime:
        native = BankRuntime
    initial = [name for name in profile["selections"][0] if name != visual]
    if visual is not None:
        options["ui"] = visual
    with native(profile["primary"], initial, **options) as runtime:
        _check(runtime.expect("GET", "/api/health")["version"] == profile["host"])
        registry = PluginRegistry(tuple(available[name] for name in [reference.id, *profile["selections"][0]]))
        candidate_hooks = exercise_candidate(candidate, registry)
        api_evidence = None
        visual_evidence = None
        bank_evidence = None
        if api is not None or bank:
            ticket = runtime.expect("POST", "/api/plugins/management/upgrade", {
                "pluginId": candidate.id, "wheels": api_worker_wheels(candidate, manifest["artifacts"], wheelhouse),
            }, status=202)
            runtime.operation("/api/plugins/runtime", ticket["id"])
            worker, = (row for row in runtime.expect("GET", "/api/plugins/runtime")["workers"] if row["plugin"] == candidate.id)
            _check(worker["health"] == "ready")
            if api is not None:
                api_evidence = {**assert_api_interface(runtime, candidate), "worker": worker}
        if visual is not None:
            active = visual_change(runtime, visual, wheels=api_worker_wheels(candidate, manifest["artifacts"], wheelhouse), dirty=True)
            resources = candidate.ui.resources
            asset = next((name.removeprefix("static/") for name in sorted(resources.files) if name.startswith("static/")), None) if resources is not None else None
            visual_evidence = {**assert_visual_interface(runtime, candidate, asset_name=asset), "worker": active["worker"]}
        if profile["fault"] == "invalid-selection":
            runtime.expect("POST", "/api/plugins/management/transition", {"plugins": [*profile["selections"][0], "synthetic-not-installed"]}, status=409)
        for selection in profile["selections"][1:]:
            if visual is not None:
                selected = visual if visual in selection else None
                if runtime.expect("GET", "/api/ui/status")["active"]["plugin_id"] != selected:
                    visual_change(runtime, selected)
            _selection(runtime, selection, visual=visual)
            if api is not None:
                identifier = runtime.expect("GET", "/api/budget")["id"]
                query = f"/remote/v1/budgets/{identifier}/queries/accounts?workspace=personal"
                runtime.expect("GET", query, authenticated=True, status=200 if candidate.id in selection else 404)
                if candidate.id not in selection:
                    runtime.expect("GET", query, authenticated=True, headers={"Host": "fictional.remote.example"}, status=403)
        if bank:
            scenario = fictional_scenario(candidate.id)
            bank_evidence = exercise_bank_runtime(runtime, scenario, variant=profile["bank"]["variant"], disconnect=False)
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
        if api is not None:
            identifier = runtime.expect("GET", "/api/budget")["id"]
            result = runtime.expect("GET", f"/remote/v1/budgets/{identifier}/queries/accounts?workspace=personal", authenticated=True)
            _check(result["data"] == before["accounts"])
            worker, = (row for row in runtime.expect("GET", "/api/plugins/runtime")["workers"] if row["plugin"] == candidate.id)
            _check(worker == api_evidence["worker"])
        if visual is not None:
            _check(runtime.expect("GET", "/api/ui/status")["active"]["worker"] == visual_evidence["worker"])
            _check(assert_visual_interface(runtime, candidate, asset_name=asset)["generation"] == visual_evidence["generation"])
        if bank:
            _check(runtime.expect("POST", "/api/banks/" + candidate.id + "/sync", {"itemId": scenario.item_id})
                   == {"ok": True, "added": 0, "modified": 0, "removed": 0})
            disconnect_bank(runtime, scenario)
            _check(runtime.expect("GET", "/api/workspace?workspace=personal&month=2026-10")["transactions"] == before["transactions"])
        runtime.stop()
        cleanups = runtime.cleanups
    _check(not runtime.home.exists() and runtime.process is None)
    return {**baseline, "configuration": hashlib.sha256(_dump(manifest).encode()).hexdigest(),
            "runtime": {"status": "passed", "profile": profile["profile"], "primary": profile["primary"],
                        "destination": profile["destination"], "fault": profile["fault"],
                        "steps": profile["steps"], "sequence": profile["sequence"], "selections": profile["selections"],
                        "candidate_selection": profile["candidate_selection"], "cleanup_count": cleanups,
                        "cleanup_complete": True, **({"api": api_evidence} if api_evidence is not None else {}),
                        **({"bank": bank_evidence} if bank_evidence is not None else {}),
                        **({"visual": visual_evidence} if visual_evidence is not None else {})}}
