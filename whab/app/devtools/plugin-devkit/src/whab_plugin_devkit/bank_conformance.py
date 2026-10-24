"""Public HTTPS contracts for reviewed offline bank fixtures, without backend imports.

This additive helper is not wired into the CLI's native-local-v1 sampler. Callers
must install their reviewed fictional provider and a supported local backend.
"""

import re
import secrets
from dataclasses import dataclass, field
from importlib import metadata
from urllib.parse import quote

from packaging.version import Version

from .runtime_harness import NativeRuntime

MINIMUM_BANK_HOST = "2.38.0"


def bank_applicability(host_version):
    """Older supported toolkit hosts explicitly lack this native bank profile."""
    return ("supported" if Version(host_version) >= Version(MINIMUM_BANK_HOST)
            else "not-applicable: host predates typed bank runtime")


@dataclass(frozen=True)
class BankScenario:
    """Expected bounded fictional data; credentials never enter result artifacts."""

    plugin_id: str
    item_id: str
    account_name: str
    merchant: str
    amount: int
    public_token: str = field(repr=False)
    link_token: str = field(repr=False)
    currency: str = "USD"
    month: str = "2026-10"

    def __post_init__(self):
        strings = (self.plugin_id, self.item_id, self.account_name, self.merchant, self.public_token, self.link_token)
        if (any(type(value) is not str or not 1 <= len(value) <= 200 or "\x00" in value for value in strings)
                or re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", self.plugin_id) is None
                or type(self.amount) is not int or not 1 <= self.amount <= 2**53 - 1
                or type(self.currency) is not str or re.fullmatch(r"[A-Z]{3}", self.currency) is None
                or type(self.month) is not str or re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", self.month) is None):
            raise ValueError("Use bounded fictional bank scenario fields.")
        try:
            for value in strings:
                value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Use bounded fictional bank scenario fields.") from None


class BankRuntime(NativeRuntime):
    """NativeRuntime with a new host-owned key retained across its own restarts.

    The inherited environment allowlist excludes the caller's bank credentials.
    The selected fixture is trusted Python and must itself be reviewed as offline.
    This process boundary is not a network sandbox.
    """

    def __init__(self, storage, selection, *, timeout=30):
        if bank_applicability(metadata.version("whab")) != "supported":
            raise ValueError("The native bank profile requires WHAB 2.38 or newer.")
        super().__init__(storage, selection, timeout=timeout)
        self._fictional_key = secrets.token_hex(32)

    def environment(self):
        return {**super().environment(), "TOKEN_ENCRYPTION_KEY": self._fictional_key}


def _check(value):
    if not value:
        raise AssertionError("The offline bank violated its advertised public runtime contract.")


def exercise_bank_runtime(runtime, scenario):
    """Exercise one new empty budget through the installed host's public HTTPS API.

    The fixture must expose one pending transaction on its first background sync,
    post it on the next sync, then return empty changes at the saved cursor. All
    lookup identities are learned from public responses, never native internals.
    The caller owns runtime teardown, including when an assertion fails.
    """
    if type(scenario) is not BankScenario:
        raise ValueError("Supply one explicit reviewed fictional scenario.")
    prefix = "/api/banks/" + scenario.plugin_id
    workspace_path = "/api/workspace?workspace=personal&month=" + scenario.month
    demo_path = "/api/workspace?workspace=demo&month=" + scenario.month
    demo_before = runtime.expect("GET", demo_path)
    _check(not any(row["source"] == scenario.plugin_id for row in runtime.expect("GET", workspace_path)["accounts"]))
    ready = runtime.expect("POST", prefix + "/link-token", {})
    _check(ready["stage"] == "ready" and ready["kind"] == "link" and ready["link_token"] == scenario.link_token)
    _check(runtime.expect("POST", prefix + "/link-token", {}) == ready)
    flow = prefix + "/continuations/" + ready["flow_id"]
    _check(runtime.expect("DELETE", flow)["stage"] == "cancelled")
    ready = runtime.expect("POST", prefix + "/link-token", {})
    _check(ready["flow_id"] != flow.rsplit("/", 1)[-1])
    flow = prefix + "/continuations/" + ready["flow_id"]
    _check(runtime.expect("POST", prefix + "/exchange", {"flowId": ready["flow_id"],
        "publicToken": scenario.public_token, "institutionName": "Fictional Bank"})["stage"] == "complete")
    _check(runtime.expect("GET", flow)["stage"] == "complete")

    def pending():
        data = runtime.expect("GET", workspace_path)
        matches = [row for row in data["transactions"] if row["source"] == scenario.plugin_id]
        return (data, matches[0]) if len(matches) == 1 and matches[0]["pending"] else None
    data, original = runtime.wait(pending)
    account, = [row for row in data["accounts"] if row["source"] == scenario.plugin_id]
    _check(account["name"] == scenario.account_name and account["currency"] == scenario.currency)
    _check(original["merchant"] == scenario.merchant and original["amount"] == scenario.amount)
    runtime.expect("PATCH", "/api/transactions/" + quote(original["id"], safe=""),
                   {"workspace": "personal", "reviewed": True, "note": "Fictional retained annotation"})
    result = runtime.expect("POST", prefix + "/sync", {"itemId": scenario.item_id})
    _check(result == {"ok": True, "added": 1, "modified": 0, "removed": 1})
    data = runtime.expect("GET", workspace_path)
    posted, = [row for row in data["transactions"] if row["source"] == scenario.plugin_id]
    _check(posted["id"] != original["id"] and not posted["pending"] and posted["reviewed"]
           and posted["amount"] == scenario.amount and posted["note"] == "Fictional retained annotation")
    _check(runtime.expect("GET", demo_path) == demo_before)
    runtime.stop()
    runtime.start()
    _check(runtime.expect("GET", workspace_path) == data)
    _check(runtime.expect("POST", prefix + "/sync", {"itemId": scenario.item_id})
           == {"ok": True, "added": 0, "modified": 0, "removed": 0})
    update = runtime.expect("POST", prefix + "/link-token", {"itemId": scenario.item_id})
    _check(update["kind"] == "update")
    _check(runtime.expect("POST", prefix + "/exchange", {"flowId": update["flow_id"]})["stage"] == "complete")
    runtime.expect("DELETE", prefix + "/items/" + quote(scenario.item_id, safe=""))
    # Disconnect is idempotent and preserves financial history.
    runtime.expect("DELETE", prefix + "/items/" + quote(scenario.item_id, safe=""))
    _check(runtime.expect("GET", workspace_path)["transactions"] == data["transactions"])
    _check(runtime.expect("GET", demo_path) == demo_before)
    return {"status": "passed", "profile": "offline-bank-v1", "plugin": scenario.plugin_id,
            "steps": ["begin-retry", "cancel", "complete", "pending-annotations", "posted", "reopen",
                      "cursor-retry", "update", "disconnect", "demo-isolation"]}
