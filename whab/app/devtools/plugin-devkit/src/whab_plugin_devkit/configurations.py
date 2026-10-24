"""Reproducible resolver/hook configurations; no live lifecycle or migration claims."""

import hashlib
import json
import platform
import random
import re
from dataclasses import asdict, replace
from itertools import combinations
from pathlib import Path
from types import MappingProxyType

from whab.plugins import (
    AccountSnapshot,
    BankLogos,
    BudgetContext,
    BudgetSnapshot,
    Capability,
    CategorySnapshot,
    FileImport,
    Insight,
    Plugin,
    PluginContract,
    PluginRegistry,
    Requirement,
    TransactionSnapshot,
    WorkspaceImport,
    resolve_plugins,
    state_upgrade_path,
)
from whab.plugins.contracts import CapabilityError

from .adapters import BackendAdapter, BackendHarness, public_snapshot
from .api_interface import api_declaration
from .bank_conformance import bank_declaration
from .conformance import Capability as Suite
from .conformance import run_conformance
from .remote_client import remote_declaration
from .synthetic import SyntheticClock
from .visual_interface import visual_declaration

_PEERS = ("synthetic-report-a", "synthetic-report-b", "synthetic-brand-a", "synthetic-brand-b", "synthetic-import-a", "synthetic-import-b")
_FAULTS = ("none", "report-hook", "import-atomicity")
_SEQUENCES = ("disable-enable-peer", "replace-peer", "reorder-peers")


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _peer(identifier):
    suffix = identifier.removeprefix("synthetic-")
    requires = (Requirement("ledger.read"),)
    if suffix == "report-b":
        requires += (Requirement("branding.bank"),)
    if suffix == "import-b":
        requires += (Requirement("reporting.insights"),)
    contract = PluginContract(requires=requires)
    if suffix.startswith("report-"):
        return Plugin(identifier, "Synthetic report", insights=lambda _: (Insight("Fictional", "1"),), contract=contract)
    if suffix.startswith("brand-"):
        return Plugin(identifier, "Synthetic branding", bank_logos=BankLogos(lambda _: None, lambda _: None), contract=contract)
    return Plugin(identifier, "Synthetic import", file_import=FileImport(lambda _: WorkspaceImport((), (), (), (), "2026-10")), contract=contract)


def snapshot():
    return BudgetSnapshot("personal", "2026-10", (AccountSnapshot("wallet", "Fictional wallet", "depository", "checking", 10000),),
                          (CategorySnapshot("food", "Food", "Everyday", 1000, 125, 875),),
                          (TransactionSnapshot("purchase", "2026-10-01", "Fictional shop", 125, "wallet", "food"),))


def exercise_candidate(plugin, registry=None):
    """Exercise the supported public teaching hook families with fictional inputs."""
    exercised = []
    registry = registry or PluginRegistry((plugin,))
    if plugin.insights:
        result = registry.insights(snapshot())
        if any(item.error for item in result):
            raise ValueError("Candidate reporting failed its fictional hook check.")
        exercised.append("insights")
    if plugin.export:
        registry.export(plugin.id, snapshot())
        exercised.append("export")
    if plugin.bank_logos:
        key = plugin.bank_logos.resolve("Fictional Bank")
        if key is not None and type(key) is not str:
            raise ValueError("Candidate branding returned an invalid key.")
        if plugin.bank_logos.fetch("unadvertised-fictional-key") is not None:
            raise ValueError("Unknown branding keys must return no image.")
        exercised.append("branding")
    if plugin.file_import:
        imported = plugin.file_import.parse(b'{"name":"Fictional wallet","balance":125}')
        if type(imported) is not WorkspaceImport:
            raise ValueError("Candidate import did not return a public WorkspaceImport.")
        imported.validated_copy()
        exercised.append("file-import")
    if plugin.categorization:
        files = {}
        context = BudgetContext("fictional", MappingProxyType({}), MappingProxyType({}), lambda _: None, files.get,
                                lambda name, raw: files.__setitem__(name, raw), lambda name: files.pop(name, None))
        engine = plugin.categorization.factory(context)
        if engine.index().get("models"):
            raise ValueError("A fresh fictional categorizer namespace must be empty.")
        row = {"id": "reviewed", "merchant": "Fictional shop", "currency": "USD", "categoryId": "food", "reviewed": True}
        iterator = engine.train([row], {})
        for _ in range(10000):
            try:
                next(iterator)
            except StopIteration as done:
                model = done.value
                break
        else:
            iterator.close()
            raise ValueError("Teaching training exceeded its cooperative step bound.")
        saved = engine.save(model, "Fictional model")
        engine.select(saved["id"])
        inferred = engine.predict([{**row, "id": "new", "reviewed": False, "categoryId": None}])
        if not inferred or any(item["categoryId"] != "food" for item in inferred):
            raise ValueError("Candidate categorization failed its reviewed fictional match.")
        engine.rename(saved["id"], "Renamed fictional model")
        engine.delete(saved["id"])
        exercised.append("categorization")
    if plugin.interface:
        interface = plugin.interface
        # Preserve the original hook-only path for mixed legacy factory plugins.
        # A present declarative adapter must pass; unrelated hooks cannot hide it.
        if (getattr(interface, "adapter", None) is not None
                or not callable(getattr(interface, "factory", None)) or not exercised):
            api_declaration(plugin)
            exercised.append("interface.api")
    if plugin.ui:
        visual_declaration(plugin)
        exercised.append("interface.ui")
    if plugin.storage is not None and not callable(plugin.storage):
        remote_declaration(plugin)
        exercised.append("storage.remote-declaration")
    elif callable(plugin.storage):
        from .local_storage import storage_conformance

        storage_conformance(plugin)
        exercised.append("storage")
    if getattr(plugin, "bank", None) is not None and not exercised:
        bank_declaration(plugin)
        exercised.append("ingestion.bank")
    if not exercised:
        raise ValueError("Choose a supported reporting, branding, file import, categorization, API, visual or offline bank candidate.")
    return exercised


def deterministic_negatives():
    def report(identifier, **contract):
        return Plugin(identifier, identifier, insights=lambda _: (), contract=PluginContract(**contract))

    cases = (
        ("missing_requirement", lambda: resolve_plugins((report("needs", requires=(Requirement("extension.absent"),)),))),
        ("dependency_cycle", lambda: resolve_plugins((report("a", provides=(Capability("extension.a"),), requires=(Requirement("extension.b"),)), report("b", provides=(Capability("extension.b"),), requires=(Requirement("extension.a"),))))),
        ("incompatible_requirement", lambda: resolve_plugins((report("provider", provides=(Capability("extension.custom", 1),)), report("consumer", requires=(Requirement("extension.custom", 2),))))),
        ("conflicting_providers", lambda: resolve_plugins((Plugin("first", "First", storage=lambda _: None), Plugin("second", "Second", storage=lambda _: None)))),
        ("unsupported_state", lambda: state_upgrade_path(PluginContract(), 2)),
    )
    observed = []
    for code, operation in cases:
        try:
            operation()
        except CapabilityError as error:
            if error.code != code:
                raise ValueError("A deterministic negative produced an unexpected public resolution outcome.") from None
        else:
            raise ValueError("A deterministic invalid selection was accepted.")
        observed.append(code)
    return observed


def sample_configurations(candidate, reference, artifacts, *, combinations_count=10, seed=42):
    """Sample distinct selected peer sets; order-only differences never count twice."""
    if callable(candidate.storage):
        from .storage_profiles import sample_storage_configurations

        return sample_storage_configurations(candidate, reference, artifacts, combinations_count=combinations_count, seed=seed, runtime=False)
    if candidate.storage is not None and not callable(candidate.storage):
        from .remote_profiles import sample_remote_configurations

        return sample_remote_configurations(candidate, reference, artifacts, combinations_count=combinations_count, seed=seed, runtime=False)
    if type(combinations_count) is not int or not 1 <= combinations_count <= 32 or type(seed) is not int:
        raise ValueError("Choose an integer seed and one to 32 distinct constrained configurations.")
    randomizer = random.Random(seed)
    valid = []
    for length in range(1, len(_PEERS) + 1):
        for peers in combinations(_PEERS, length):
            try:
                resolve_plugins((candidate, reference, *(_peer(name) for name in peers)))
            except CapabilityError:
                continue
            valid.append(peers)
    if len(valid) < combinations_count:
        raise ValueError("The candidate constraints do not permit the requested number of distinct reviewed configurations.")
    selections = randomizer.sample(valid, combinations_count)
    manifests = []
    for index, peers in enumerate(selections):
        selected = [candidate.id, reference.id, *peers]
        discovered, configured = selected.copy(), selected.copy()
        randomizer.shuffle(discovered)
        randomizer.shuffle(configured)
        sequence = _SEQUENCES[index % len(_SEQUENCES)]
        if not any(_valid((candidate, reference, *(_peer(name) for name in peers if name != removed))) for removed in peers):
            sequence = "reorder-peers"
        manifests.append({"formatVersion": 1, "scope": "resolver-and-public-hooks", "seed": seed, "index": index,
                          "python": platform.python_version(), "candidate": candidate.id, "reference": reference.id,
                          "peers": list(peers), "discovery_order": discovered, "configuration_order": configured,
                          "sequence": sequence, "fault": _FAULTS[index % len(_FAULTS)],
                          "artifacts": artifacts, "unsupported": ["live-coordinator", "migration", "copies", "promotion"],
                          "replay": f"whab-plugin replay configuration-{index:02d}.json --wheelhouse APPROVED_WHEELHOUSE"})
    return manifests


def validate_manifest(value):
    keys = {"formatVersion", "scope", "seed", "index", "python", "candidate", "reference", "peers", "discovery_order", "configuration_order", "sequence", "fault", "artifacts", "unsupported", "replay"}
    if type(value) is not dict or set(value) != keys or type(value["formatVersion"]) is not int or value["formatVersion"] != 1 or value["scope"] != "resolver-and-public-hooks":
        raise ValueError("Unsupported configuration manifest schema.")
    if type(value["seed"]) is not int or type(value["index"]) is not int or not 0 <= value["index"] < 32 or value["python"] != platform.python_version():
        raise ValueError("Configuration seed/index or Python version is incompatible.")
    if type(value["candidate"]) is not str or len(value["candidate"]) > 64 or re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", value["candidate"]) is None or value["reference"] != "sqlite":
        raise ValueError("Choose the declared candidate and the supported SQLite reference slot.")
    if type(value["peers"]) is not list or any(type(name) is not str for name in value["peers"]) or len(set(value["peers"])) != len(value["peers"]) or not set(value["peers"]) <= set(_PEERS):
        raise ValueError("The manifest contains an unknown or duplicate reviewed synthetic peer.")
    selected = [value["candidate"], value["reference"], *value["peers"]]
    for key in ("discovery_order", "configuration_order"):
        if type(value[key]) is not list or any(type(name) is not str for name in value[key]) or len(value[key]) != len(selected) or set(value[key]) != set(selected) or len(set(value[key])) != len(value[key]):
            raise ValueError("Configuration/discovery order must list every selected provider exactly once.")
    if value["sequence"] not in _SEQUENCES or value["fault"] not in _FAULTS or value["unsupported"] != ["live-coordinator", "migration", "copies", "promotion"]:
        raise ValueError("The manifest requests an unsupported sequence, fault or runtime extension.")
    if type(value["artifacts"]) is not list or not 1 <= len(value["artifacts"]) <= 128:
        raise ValueError("Record a bounded exact local artifact closure.")
    distributions = []
    for artifact in value["artifacts"]:
        if type(artifact) is not dict or set(artifact) != {"distribution", "version", "filename", "sha256"}:
            raise ValueError("Artifact evidence has unsupported fields.")
        if any(type(artifact[key]) is not str or not artifact[key] for key in artifact):
            raise ValueError("Artifact evidence must contain bounded identity text and a SHA-256 hash.")
        if len(artifact["distribution"]) > 200 or len(artifact["version"]) > 80 or len(artifact["filename"]) > 255 or Path(artifact["filename"]).name != artifact["filename"] or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None:
            raise ValueError("Artifact evidence must contain bounded identity text and a SHA-256 hash.")
        distributions.append(artifact["distribution"])
    if len(set(distributions)) != len(distributions) or value["replay"] != f"whab-plugin replay configuration-{value['index']:02d}.json --wheelhouse APPROVED_WHEELHOUSE":
        raise ValueError("Artifact distributions must be unique and the replay command must match this manifest.")
    return value


def reference_conformance(reference):
    """Run the independent reviewed backend suites without selecting it as a peer."""
    suites = set(Suite) - {Suite.NATIVE_REPLICA, Suite.DURABLE_COMMANDS}
    provided = getattr(getattr(reference, "contract", None), "provides", ())
    if any(capability.name == "storage.replica" and capability.version == 1 for capability in provided):
        suites.add(Suite.NATIVE_REPLICA)
    if any(capability.name == "storage.commands" and capability.version == 1 for capability in provided):
        suites.add(Suite.DURABLE_COMMANDS)
    report = run_conformance(BackendAdapter(reference.storage, frozenset(suites)), clock=SyntheticClock())
    report.assert_success()
    return [asdict(result) for result in report.results]


def replay_configuration(manifest, candidate, reference, *, wheelhouse=None):
    """Run resolver stages and public contracts; actual authority never changes."""
    if type(manifest) is dict and manifest.get("formatVersion") == 4:
        from .storage_profiles import replay_storage_configuration

        return replay_storage_configuration(manifest, candidate, reference, wheelhouse=wheelhouse)
    if type(manifest) is dict and manifest.get("formatVersion") == 3:
        from .remote_profiles import replay_remote_configuration

        return replay_remote_configuration(manifest, candidate, reference, wheelhouse=wheelhouse)
    manifest = validate_manifest(manifest)
    if (candidate.id, reference.id) != (manifest["candidate"], manifest["reference"]):
        raise ValueError("Selected installed descriptors do not match the replay manifest.")
    descriptors = {plugin.id: plugin for plugin in (candidate, reference, *(_peer(name) for name in manifest["peers"]))}
    discovered = {name: descriptors[name] for name in manifest["discovery_order"]}
    selected = tuple(discovered[name] for name in manifest["configuration_order"])
    baseline = resolve_plugins(selected)
    if baseline != resolve_plugins(tuple(reversed(selected))):
        raise ValueError("Resolved behavior changed with configuration order.")
    candidate_result = exercise_candidate(candidate, PluginRegistry(baseline))
    peer_result = {plugin.id: exercise_candidate(plugin, PluginRegistry(baseline)) for plugin in baseline if plugin.id in _PEERS}
    conformance = reference_conformance(reference)
    transitions = [tuple(plugin.id for plugin in baseline)]
    peers = list(manifest["peers"])
    if manifest["sequence"] == "reorder-peers":
        transitions.append(tuple(plugin.id for plugin in resolve_plugins(tuple(reversed(selected)))))
    else:
        # Remove a peer together with consumers that depend on it. This is
        # selection preflight, not a claim that active processes were replaced.
        candidate_sets = [tuple(plugin for plugin in selected if plugin.id != name) for name in reversed(peers)]
        reduced = next((selection for selection in candidate_sets if _valid(selection)), None)
        if reduced is None:
            raise ValueError("This selection cannot remove a peer without breaking requirements; use an ordering-only sequence.")
        transitions.append(tuple(plugin.id for plugin in resolve_plugins(reduced)))
        if manifest["sequence"] == "replace-peer":
            removed = next(plugin for plugin in baseline if plugin.id not in {item.id for item in reduced})
            replacement = replace(removed, id=removed.id + "-replacement")
            transitions.append(tuple(plugin.id for plugin in resolve_plugins((*reduced, replacement))))
        transitions.append(tuple(plugin.id for plugin in baseline))
    if manifest["fault"] == "report-hook":
        def failed(_):
            raise RuntimeError("fictional fault; never report payloads")

        broken = Plugin("synthetic-fault", "Fault", insights=failed)
        if not PluginRegistry((broken,)).insights(snapshot())[0].error:
            raise ValueError("The injected report failure was not attributed.")
    if manifest["fault"] == "import-atomicity":
        with BackendHarness(BackendAdapter(reference.storage)) as harness:
            backend = harness.open()
            before = public_snapshot(backend)
            invalid = WorkspaceImport((), (), (), ({"month": "2026-10", "categoryId": "absent", "assigned": 1},), "2026-10")
            try:
                backend.import_workspace("personal", "synthetic-fault", "0" * 64, invalid)
            except ValueError:
                if public_snapshot(backend) != before:
                    raise ValueError("The injected invalid import changed its fictional ledger.") from None
            else:
                raise ValueError("The injected invalid import was accepted.")
    return {"configuration": hashlib.sha256(_dump(manifest).encode()).hexdigest(), "candidate_hooks": candidate_result, "peer_hooks": peer_result,
            "resolved": list(transitions[0]), "preflight_transitions": transitions,
            "fault": manifest["fault"], "conformance": conformance,
            "deterministic_negatives": deterministic_negatives()}


def _valid(selection):
    try:
        resolve_plugins(selection)
    except CapabilityError:
        return False
    return True
