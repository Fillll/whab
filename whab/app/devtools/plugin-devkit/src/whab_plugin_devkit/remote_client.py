"""Fictional remote authority checks through public native HTTPS contracts."""

import base64
import secrets
import sys

from whab import plugins as sdk

from .runtime_harness import NativeRuntime

MINIMUM_REMOTE_HOST = "2.40.0"
_FIELDS = ("protocol_version", "url_setting", "token_setting", "budget_setting")


def remote_declaration(plugin):
    """Read only the public protocol and host setting names, never their values."""
    expected = getattr(sdk, "RemoteLedgerClient", None)
    adapter = getattr(plugin, "storage", None)
    contract = getattr(plugin, "contract", None)
    if (expected is None or type(adapter) is not expected or contract is None
            or contract.authority != "remote" or contract.lifecycle != "live"):
        raise ValueError("Use a declarative remote client on WHAB 2.39 or newer.")
    values = {name: getattr(adapter, name) for name in _FIELDS}
    expected(**values)
    sdk.resolve_plugins((plugin,))
    return values


class RemoteClientRuntime(NativeRuntime):
    """Own a fictional API server and client, with two explicitly verified CAs.

    Every credential is created for these disposable hosts. On the profile's
    actual WHAB 2.40 floor, provisioning and descriptor workers receive no
    endpoint, bearer value, remote budget or encryption key.
    """

    def __init__(self, plugin, selection=(), **kwargs):
        self.declaration = remote_declaration(plugin)
        if plugin.id in selection:
            raise ValueError("The remote storage candidate is separate from optional peers.")
        super().__init__("sqlite", selection, **kwargs)
        self.storage = plugin.id
        self.server = NativeRuntime("sqlite", ("server",), api_token_setting="SERVER_TOKEN",
                                    timeout=self.timeout, transition_timeout=self.transition_timeout)
        self._encryption_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        self._remote_settings = {}

    def environment(self):
        result = super().environment()
        result.update(self._remote_settings)
        result["TOKEN_ENCRYPTION_KEY"] = self._encryption_key
        return result

    def start(self):
        if self.server.process is None:
            self.server.start()
        identifier = self.server.expect("GET", "/api/budget")["id"]
        self._remote_settings = {
            self.declaration["url_setting"]: f"https://127.0.0.1:{self.server.port}",
            self.declaration["token_setting"]: self.server._api_token,
            self.declaration["budget_setting"]: identifier,
            "WHAB_DEVKIT_REMOTE_CA": str(self.server.home / "tls/ca.crt"),
        }
        return super().start()

    def launch_command(self):
        return [sys.executable, "-I", "-B", "-m", "whab_plugin_devkit.remote_launcher", "--port", str(self.port)]

    def lose_next_public_result(self):
        """Arm only the reviewed fixture's next acknowledged add-account result."""
        from .remote_launcher import DROP_RESULT

        (self.home / "devkit-remote-drop-result").write_bytes(DROP_RESULT)

    def __exit__(self, kind, value, traceback):
        try:
            super().__exit__(kind, value, traceback)
        finally:
            self.server.__exit__(kind, value, traceback)


def remote_selection(runtime, plugin, enabled):
    """Await one explicit same-binding pause/enable through host controls."""
    before = runtime.expect("GET", "/api/storage/remote/status")
    runtime.expect("POST", "/api/storage/remote/selection", {
        "pluginId": plugin.id, "enabled": enabled, "expectedRevision": before["revision"],
    }, status=202)
    def observe():
        value = runtime.expect("GET", "/api/storage/remote/status")
        if value.get("unknown_outcome") != before.get("unknown_outcome"):
            raise AssertionError("A selection must not clear an unresolved remote write.")
        active = value["runtime"]
        ready = active["admission"] == "open" and active["pending"] is None and active["phase"] == "active"
        return value if value.get("enabled") is enabled and ready else None
    return runtime.wait(observe, timeout=runtime.transition_timeout)


def assert_remote_client(runtime, plugin):
    """Exercise this candidate against its own fictional CA-verified API host.

    This profile has no local backend migration/copy claim and no public receipt
    lookup or automatic unknown-write recovery promise.
    """
    remote_declaration(plugin)
    original = runtime.expect("GET", "/api/storage/remote/status")
    if not original["enabled"] or original["receipt_lookup"] or original["automatic_reconciliation"]:
        raise AssertionError("The remote authority status must describe the supported protocol exactly.")
    runtime.expect("GET", "/api/storage/remote/status", headers={"Host": "fictional.remote.example"}, status=403)
    runtime.expect("GET", "/api/storage/remote/status", headers={"Origin": "https://fictional.remote.example"}, status=403)
    runtime.expect("POST", "/api/accounts", {"workspace": "personal", "name": "Fictional remote wallet",
                                            "type": "depository", "balance": 3210})
    def verify():
        rows = runtime.expect("GET", "/api/workspace?workspace=personal")["accounts"]
        matches = [row for row in rows if row["name"] == "Fictional remote wallet"]
        if len(matches) != 1 or matches[0]["balance"] != 3210:
            raise AssertionError("The fictional remote ledger did not retain the acknowledged account.")
        server_rows = runtime.server.expect("GET", "/api/workspace?workspace=personal")["accounts"]
        if [row for row in server_rows if row["name"] == "Fictional remote wallet"] != matches:
            raise AssertionError("The client and its addressed remote authority disagree.")
    verify()
    remote_selection(runtime, plugin, False)
    runtime.expect("GET", "/api/workspace?workspace=personal", status=400)
    remote_selection(runtime, plugin, True)
    verify()
    runtime.stop()
    runtime.start()
    verify()
    reopened = runtime.expect("GET", "/api/storage/remote/status")
    if reopened["unknown_outcome"] or not reopened["enabled"]:
        raise AssertionError("Reopening must retain the exact acknowledged authority decision.")
    return {"guards": "passed", "read_write": "passed", "pause_enable": "passed", "reopen": "passed",
            "migration_copies": "not-applicable: remote authority", "automatic_reconciliation": "not-supported"}


def assert_lost_remote_result(runtime):
    """Keep an acknowledged lost result unknown through restart, then Finish.

    Finish is an explicit resubmission of the original authorized body under its
    retained ID/CAS on the same live server. It is not a receipt lookup, automatic
    recovery, or proof of durable server retry storage across a server restart.
    """
    payload = {"name": "Fictional lost result", "type": "depository", "balance": 730,
               "currency": "USD", "onBudget": True, "subtype": None}
    runtime.lose_next_public_result()
    runtime.expect("POST", "/api/accounts", {"workspace": "personal", **payload}, status=400)
    def unknown():
        if not runtime.expect("GET", "/api/storage/remote/status")["unknown_outcome"]:
            raise AssertionError("A lost public result must retain its original unknown intent.")
        rows = runtime.server.expect("GET", "/api/workspace?workspace=personal")["accounts"]
        if len([row for row in rows if row["name"] == payload["name"] and row["balance"] == payload["balance"]]) != 1:
            raise AssertionError("The dropped result must follow exactly one acknowledged public mutation.")
    unknown()
    runtime.stop(kill=True)
    runtime.start()
    unknown()
    runtime.expect("POST", "/api/accounts", {"workspace": "personal", **payload, "name": "Must remain blocked"}, status=400)
    runtime.expect("POST", "/api/storage/remote/finish", {"operation": "add_account", "workspace": "personal", "payload": payload})
    runtime.expect("POST", "/api/storage/remote/finish", {"operation": "add_account", "workspace": "personal", "payload": payload})
    if runtime.expect("GET", "/api/storage/remote/status")["unknown_outcome"]:
        raise AssertionError("Only an exactly correlated original response may clear the unknown intent.")
    rows = runtime.server.expect("GET", "/api/workspace?workspace=personal")["accounts"]
    if len([row for row in rows if row["name"] == payload["name"]]) != 1:
        raise AssertionError("Explicit original-request completion duplicated the remote mutation.")
    return {"lost_public_result": "passed", "unknown_after_client_restart": "passed",
            "explicit_original_finish": "passed", "server_restart_retry": "not-supported"}
