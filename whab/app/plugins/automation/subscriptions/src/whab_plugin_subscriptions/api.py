"""Public v1 subscription tools and optional in-process inference integration."""

from dataclasses import replace
from datetime import date

from whab.plugins import FeatureOperations, StoreError

from .engine import (
    CADENCES,
    DEFAULTS,
    digest,
    excluded,
    fingerprint,
    pattern,
    valid_decision,
)
from .engine import (
    Engine as LabelStore,
)

__all__ = ["CADENCES", "DEFAULTS", "LabelStore", "Subscriptions", "digest", "excluded",
           "fingerprint", "install_ai", "pattern", "valid_decision"]


def scope(parameters):
    if type(parameters) is not dict or not {"month", "currency"} <= parameters.keys():
        raise StoreError("Choose a month and currency.")
    month, currency = parameters["month"], parameters["currency"]
    try:
        if (type(month) is not str or len(month) != 7
                or not "1900-01" <= month <= "2200-12"
                or type(currency) is not str or len(currency) != 3
                or not currency.isascii() or not currency.isupper() or not currency.isalpha()):
            raise ValueError()
        date.fromisoformat(month + "-01")
    except ValueError:
        raise StoreError("Choose a valid month and currency.") from None
    return month, currency


class Subscriptions:
    """Budget-scoped labels/reports; inference can attach through install_ai only."""

    def __init__(self, context):
        self.context, self.ai = context, None
        context.service.register_feature("subscriptions", FeatureOperations(
            queries=("report",), commands=("set_label",),
            query=self.query, command=self.command, adapter=self,
        ))

    def budget(self, workspace):
        base = self.context.budget
        if base is None:
            raise StoreError("Open subscriptions on the local budget host.")
        return replace(base,
            read_file=lambda name: base.read_file("demo/" + name),
            write_file=lambda name, raw: base.write_file("demo/" + name, raw),
        ) if workspace == "demo" else base

    def snapshot(self, workspace):
        snapshot = self.context.service.query("portable", workspace)
        data = snapshot["data"]
        accounts = {row["id"]: row for row in data["accounts"]}
        rows = [{**row, "currency": accounts[row["accountId"]]["currency"]}
                for row in data["transactions"] if not row.get("deleted")]
        return snapshot, rows

    def query(self, workspace, action, parameters):
        if set(parameters) != {"month", "currency"}:
            raise StoreError("Invalid subscription report fields.")
        month, currency = scope(parameters)
        snapshot, rows = self.snapshot(workspace)
        report = LabelStore(self.budget(workspace)).report(
            rows, snapshot["data"]["accounts"], snapshot["data"]["categories"], month, currency,
        )
        # Provider settings are local, separate from the agent-facing report.
        return {key: value for key, value in report.items() if key != "config"}

    def command(self, workspace, action, parameters):
        if (set(parameters) != {"transactionId", "fingerprint", "isSubscription", "cadence"}
                or type(parameters["transactionId"]) is not str
                or type(parameters["fingerprint"]) is not str):
            raise StoreError("Invalid subscription label fields.")
        if self.ai is not None:
            self.ai.require_idle(workspace)
        _, rows = self.snapshot(workspace)
        store = LabelStore(self.budget(workspace))
        store.override(rows, parameters["transactionId"], parameters["fingerprint"], {
            "isSubscription": parameters["isSubscription"], "cadence": parameters["cadence"], "score": 1,
        })
        return {"saved": True, "scope": "matching-payments-in-account"}


def install_ai(service, factory):
    """Attach an explicitly selected companion to the existing saved namespace.

    The companion receives the public budget context through this API, preserving
    historical profiles, connection references, labels and job receipts in place.
    """
    subscriptions = service.feature_adapter("subscriptions")
    if subscriptions.ai is not None:
        raise StoreError("A subscription classifier is already enabled.")
    subscriptions.ai = factory(subscriptions.context)
