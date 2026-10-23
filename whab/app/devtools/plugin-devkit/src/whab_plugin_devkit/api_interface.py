"""Public, data-only API declarations and fictional native host contract checks."""

from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

from whab import plugins as sdk

from .artifacts import normalized

MINIMUM_API_HOST = "2.34.0"
_FIELDS = ("protocol_version", "token_setting", "queries", "commands")


def api_declaration(plugin):
    """Revalidate the public adapter without importing routes or executing a factory."""
    interface = getattr(plugin, "interface", None)
    adapter = getattr(interface, "adapter", None)
    expected = getattr(sdk, "RemoteLedgerInterface", None)
    if adapter is None or expected is None or type(adapter) is not expected or interface.factory is not None:
        raise ValueError("Use the supported declarative API interface on WHAB 2.34 or newer.")
    values = {name: getattr(adapter, name) for name in _FIELDS}
    expected(**values)
    if not values["token_setting"].startswith(plugin.id.replace("-", "_").upper() + "_"):
        raise ValueError("The host API token setting must belong to this plugin configuration prefix.")
    return values


def api_worker_wheels(plugin, artifacts, wheelhouse):
    """Select only the already approved installed host/candidate dependency closure.

    The host rechecks every supplied wheel and prepares its immutable environment
    offline. Toolkit, reference backend and fixture registrations never enter it
    unless incorrectly declared as runtime dependencies, which preparation rejects.
    """
    entries = [entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == plugin.id]
    if len(entries) != 1 or wheelhouse is None:
        raise ValueError("API worker execution requires one installed candidate and its approved wheelhouse.")
    root = normalized(entries[0].dist.metadata["Name"])
    approved = {normalized(row["distribution"]): row for row in artifacts}
    selected, pending = {}, ["whab", root]
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in approved or len(selected) >= 128:
            raise ValueError("The API worker dependency closure is incomplete or exceeds its bound.")
        record = approved[name]
        distribution = metadata.distribution(name)
        if distribution.version != record["version"]:
            raise ValueError("The API worker artifact differs from the installed package version.")
        selected[name] = record
        for text in distribution.requires or ():
            requirement = Requirement(text)
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
            dependency = normalized(requirement.name)
            if (requirement.url or requirement.extras or dependency not in approved
                    or not requirement.specifier.contains(approved[dependency]["version"], prereleases=True)):
                raise ValueError("Use a complete approved API worker closure without URL or extra requirements.")
            pending.append(dependency)
    return [{"path": str(Path(wheelhouse).resolve() / selected[name]["filename"]), "sha256": selected[name]["sha256"]}
            for name in sorted(selected)]


def assert_api_interface(runtime, plugin):
    """Exercise host auth, declared operations, validation and same-host retries.

    The caller owns the disposable NativeRuntime, whose configured token never
    enters this returned evidence. No concrete backend or host router is imported.
    """
    declaration = api_declaration(plugin)
    identifier = runtime.expect("GET", "/api/budget")["id"]
    prefix = f"/remote/v1/budgets/{identifier}"
    query = prefix + "/queries/accounts?workspace=personal"
    runtime.expect("GET", query, status=401)
    runtime.expect("GET", query, headers={"Authorization": "Bearer invalid-fictional-token"}, status=401)
    runtime.expect("GET", "/api/budget", headers={"Host": "fictional.remote.example"}, status=403)
    runtime.expect("GET", "/api/budget", headers={"Origin": "https://fictional.remote.example"}, status=403)
    runtime.expect("GET", prefix.replace(identifier, "fictional-other-budget") + "/queries/accounts?workspace=personal", authenticated=True, status=404)
    result = runtime.expect("GET", query, authenticated=True,
                            headers={"Host": "fictional.remote.example", "Origin": "https://fictional.remote.example"},
                            status=200 if declaration["queries"] else 405)
    evidence = {"guards": "passed", "query": "passed" if declaration["queries"] else "not-applicable: declaration disables queries",
                "command": "not-applicable: declaration disables commands", "retry": "not-applicable: no command"}
    if not declaration["commands"]:
        runtime.expect("POST", prefix + "/commands", {}, authenticated=True, status=400)
        # A valid envelope reaches the declaration gate, not body validation.
        runtime.expect("POST", prefix + "/commands", {"operation": "add_account", "workspace": "personal", "payload": {}, "request_id": "disabled", "expected_revision": "fictional"}, authenticated=True, status=405)
        return evidence
    if not declaration["queries"]:
        runtime.expect("POST", prefix + "/commands", {"operation": "add_account", "workspace": "personal", "payload": {"name": "Fictional", "type": "depository", "balance": 125}, "request_id": "without-query", "expected_revision": "fictional-stale-revision"}, authenticated=True, status=409)
        return {**evidence, "command": "passed: stale revision refused; success requires a prior query revision"}
    body = {"operation": "add_account", "workspace": "personal", "payload": {"name": "Fictional API wallet", "type": "depository", "balance": 125}, "request_id": "fictional-api-write", "expected_revision": result["revision"]}
    acknowledged = runtime.expect("POST", prefix + "/commands", body, authenticated=True)
    if runtime.expect("POST", prefix + "/commands", body, authenticated=True) != acknowledged:
        raise AssertionError("The API did not preserve the acknowledged request result.")
    runtime.expect("POST", prefix + "/commands", {**body, "payload": {**body["payload"], "balance": 126}}, authenticated=True, status=409)
    runtime.expect("POST", prefix + "/commands", {**body, "request_id": "fictional-stale"}, authenticated=True, status=409)
    runtime.expect("POST", prefix + "/commands", {**body, "request_id": "fictional-invalid-money", "expected_revision": acknowledged["revision"], "payload": {**body["payload"], "balance": True}}, authenticated=True, status=400)
    after = runtime.expect("GET", query, authenticated=True)["data"]
    if len(after) != len(result["data"]) + 1 or not any(row["id"] == acknowledged["data"] and row["balance"] == 125 for row in after):
        raise AssertionError("The API did not preserve its validated fictional command exactly once.")
    return {**evidence, "command": "passed", "retry": "passed"}
