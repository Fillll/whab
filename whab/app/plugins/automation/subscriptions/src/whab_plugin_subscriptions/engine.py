"""Portable subscription labels, repeated-payment reuse and integer-money reports."""

import calendar
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, timedelta

from whab.plugins import StoreError

CADENCES = ("monthly", "annual", "quarterly", "weekly", "unknown")
DEFAULTS = {
    "provider": "openai",
    "connectionId": "",
    "model": "",
    "effort": "default",
    "consent": False,
}
def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def fingerprint(row):
    return digest(
        [row[key] for key in ("id", "merchant", "amount", "currency", "accountId", "date")]
    )


def pattern(row):
    # The last four calendar dates share a month-end anchor (including February).
    return digest(
        [
            " ".join(row["merchant"].casefold().split()),
            row["amount"],
            row["currency"],
            row["accountId"],
            min(int(row["date"][8:]), 28),
        ]
    )


def excluded(row):
    return (
        row.get("pending")
        or row.get("isTransfer")
        or row.get("inferredTransfer")
        or row["amount"] <= 0
    )


def configuration(values):
    """Validate preserved legacy settings without importing an inference provider."""
    if (type(values) is not dict or set(values) != set(DEFAULTS)
            or any(type(values[k]) is not str or len(values[k]) > 200 for k in ("provider", "connectionId", "model", "effort"))
            or type(values["consent"]) is not bool):
        raise StoreError("Saved subscription configuration is invalid.")
    return dict(values)


def valid_decision(item):
    return (
        type(item) is dict
        and set(item) == {"isSubscription", "cadence", "score"}
        and type(item["isSubscription"]) is bool
        and type(item["cadence"]) is str
        and item["cadence"] in CADENCES
        and type(item["score"]) in (int, float)
        and math.isfinite(item["score"])
        and 0 <= item["score"] <= 1
    )


class Engine:
    def __init__(self, context):
        self.context = context
        raw = context.read_file("subscriptions.json")
        try:
            self.saved = (
                json.loads(raw) if raw else {"version": 1, "config": DEFAULTS.copy(), "labels": {}}
            )
            if (
                set(self.saved) != {"version", "config", "labels"}
                or self.saved["version"] != 1
                or type(self.saved["labels"]) is not dict
                or len(self.saved["labels"]) > 250000
            ):
                raise ValueError()
            if self.saved["config"] != DEFAULTS:
                configuration(self.saved["config"])
            for key, row in self.saved["labels"].items():
                if (
                    type(key) is not str
                    or type(row) is not dict
                    or set(row) != {"fingerprint", "decision", "source", "policy"}
                    or not valid_decision(row["decision"])
                    or row["source"] not in {"manual", "ai", "cache"}
                    or type(row["fingerprint"]) is not str
                    or type(row["policy"]) is not str
                ):
                    raise ValueError()
        except ValueError, TypeError, KeyError:
            raise StoreError(
                "Saved subscription labels are invalid. Preserve this budget before restoring a valid copy."
            ) from None

    def write(self):
        self.context.write_file(
            "subscriptions.json", json.dumps(self.saved, allow_nan=False).encode()
        )

    def label(self, row, manual=None):
        item = self.saved["labels"].get(row["id"])
        if (
            item
            and item["fingerprint"] == fingerprint(row)
            and (item["source"] == "manual" or item["policy"] == digest(self.saved["config"]))
        ):
            return item
        candidate = (manual or {}).get(pattern(row))
        return {**candidate, "fingerprint": fingerprint(row)} if candidate else None

    def manual_index(self, rows):
        """Read-only reuse of explicit decisions, including newly imported peers."""
        result = {}
        for row in sorted(rows, key=lambda peer: (peer["date"], peer["id"]), reverse=True):
            item = self.saved["labels"].get(row["id"])
            if (item and item["source"] == "manual"
                    and item["fingerprint"] == fingerprint(row) and not excluded(row)):
                result.setdefault(pattern(row), item)
        return result

    def put(self, row, decision, source):
        self.saved["labels"][row["id"]] = {
            "fingerprint": fingerprint(row),
            "decision": decision,
            "source": source,
            "policy": digest(self.saved["config"]),
        }

    def override(self, rows, transaction_id, expected, decision):
        row = next((r for r in rows if r["id"] == transaction_id), None)
        if row is None or fingerprint(row) != expected:
            raise StoreError(
                "This transaction changed. Refresh subscriptions before saving its label."
            )
        if excluded(row) or not valid_decision(decision):
            raise StoreError(
                "Choose a posted expense and a valid subscription label and billing interval."
            )
        # Correct the complete matching payment pattern, including older AI guesses.
        for peer in rows:
            if pattern(peer) == pattern(row) and not excluded(peer):
                self.put(peer, decision, "manual")
        self.write()

    def report(self, rows, accounts, categories, month, currency):
        manual = self.manual_index(rows)
        names = {r["id"]: f"{r['group']} / {r['name']}" if r.get("group") else r["name"] for r in categories}
        account_names = {r["id"]: r["name"] for r in accounts}
        visible, series, monthly = [], defaultdict(list), defaultdict(int)
        reference = min(
            date.today(),
            date.fromisoformat(
                month + f"-{calendar.monthrange(int(month[:4]), int(month[5:]))[1]}"
            ),
        )
        previous_year = reference.year - 1
        cutoff = date(previous_year, reference.month, min(reference.day, calendar.monthrange(previous_year, reference.month)[1]))
        for row in sorted(rows, key=lambda r: (r["date"], r["id"]), reverse=True):
            if row["currency"] != currency:
                continue
            item = self.label(row, manual)
            decision = item["decision"] if item else None
            state = (
                "excluded"
                if excluded(row)
                else "unclassified"
                if not decision
                else "needs-review"
                if decision["score"] < 0.8
                else "subscription"
                if decision["isSubscription"]
                else "not-subscription"
            )
            visible.append(
                {
                    "id": row["id"],
                    "date": row["date"],
                    "merchant": row["merchant"],
                    "amount": row["amount"],
                    "category": names.get(row.get("categoryId"), "Uncategorized"),
                    "account": account_names[row["accountId"]],
                    "status": state,
                    "cadence": decision["cadence"] if decision else "unknown",
                    "source": item["source"] if item else "rules" if excluded(row) else "",
                    "fingerprint": fingerprint(row),
                }
            )
            if state == "subscription" and row["date"] <= reference.isoformat():
                series[(pattern({**row, "amount": 0}), decision["cadence"])].append(visible[-1])
                monthly[row["date"][:7]] += row["amount"]
        subscriptions, by_category, by_merchant = [], defaultdict(int), defaultdict(int)
        annual_total = 0
        for (_, cadence), charges in series.items():
            latest = charges[0]
            gap = (reference - date.fromisoformat(latest["date"])).days
            active = (
                gap
                <= {"monthly": 62, "annual": 400, "quarterly": 110, "weekly": 16, "unknown": 62}[
                    cadence
                ]
            )
            # Unknown intervals have no invented monthly equivalent.
            numerator, denominator = {
                "monthly": (1, 1),
                "annual": (1, 12),
                "quarterly": (1, 3),
                "weekly": (52, 12),
                "unknown": (0, 1),
            }[cadence]
            latest_charges = [r for r in charges if r["date"] == latest["date"]]
            amount = sum(r["amount"] for r in latest_charges)
            estimates = [(r, (r["amount"] * numerator + denominator // 2) // denominator) for r in latest_charges]
            estimate = sum(value for _, value in estimates)
            annual = sum((r["amount"] * numerator * 12 + denominator // 2) // denominator for r in latest_charges)
            subscriptions.append(
                {
                    **latest,
                    "amount": amount,
                    "cadence": cadence,
                    "monthly": estimate,
                    "annual": annual,
                    "active": active,
                    "occurrences": len(charges),
                    "occurrencesLast12Months": sum(r["date"] > cutoff.isoformat() for r in charges),
                    "charges": charges,
                }
            )
            if active:
                annual_total += annual
                for charge, value in estimates:
                    by_category[charge["category"]] += value
                by_merchant[latest["merchant"]] += estimate
        def ranked(values):
            return [{"name": k, "amount": v} for k, v in sorted(values.items(), key=lambda r: (-r[1], r[0]))]
        months = []
        year, m = map(int, month.split("-"))
        for offset in range(11, -1, -1):
            y, n = divmod(year * 12 + m - 1 - offset, 12)
            key = f"{y:04}-{n + 1:02}"
            previous_y, previous_m = divmod(y * 12 + n - 1, 12)
            previous = monthly.get(f"{previous_y:04}-{previous_m + 1:02}", 0)
            months.append(
                {
                    "month": key,
                    "amount": monthly.get(key, 0),
                    "change": monthly.get(key, 0) - previous,
                }
            )
        return {
            "config": self.saved["config"],
            "transactions": visible,
            "subscriptions": sorted(subscriptions, key=lambda r: (-r["monthly"], r["merchant"])),
            "byCategory": ranked(by_category),
            "byMerchant": ranked(by_merchant),
            "months": months,
            "estimatedMonthly": sum(by_category.values()),
            "estimatedAnnual": annual_total,
            "asOfDate": reference.isoformat(),
            "chargesSince": (cutoff + timedelta(days=1)).isoformat(),
            "actualMonth": monthly.get(month, 0),
            "unclassified": sum(r["status"] == "unclassified" for r in visible),
            "needsReview": sum(r["status"] == "needs-review" for r in visible),
        }
