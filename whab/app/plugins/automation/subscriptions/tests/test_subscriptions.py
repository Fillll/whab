"""Caching, review priority, strict inference and currency-isolated integer reports."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from whab_plugin_ai_credentials import api as ai
from whab_plugin_subscriptions.engine import (
    DEFAULTS,
    Engine,
    configuration,
    fingerprint,
    valid_decision,
)

from whab.plugins import StoreError

YES = {"isSubscription": True, "cadence": "monthly", "score": 0.95}
NO = {"isSubscription": False, "cadence": "unknown", "score": 1}


def responder(monkeypatch, decision=YES):
    calls = []

    def complete(vault, provider, model, payload, guidance, effort, **kwargs):
        calls.append(deepcopy(payload))
        return json.dumps(
            {
                "classifications": [
                    {"transactionId": r["transactionId"], **decision}
                    for r in payload["transactions"]
                ]
            }
        )

    monkeypatch.setattr(ai, "complete", complete)
    return calls


def test_positive_and_negative_caches_reopen_and_invalidate_material_edits(
    context, config, row, monkeypatch
):
    engine = Engine(context)
    engine.configure(config)
    calls = responder(monkeypatch)
    rows = [row, {**row, "id": "two", "date": "2026-09-15"}]
    assert engine.classify(rows) == {"remaining": 0, "classified": 2, "reused": 0}
    assert len(calls) == 1 and len(calls[0]["transactions"]) == 1
    assert "account" not in json.dumps(calls) and "private note" not in json.dumps(calls)
    engine.write()
    reopened = Engine(context)
    new = {**row, "id": "three", "date": "2026-11-15"}
    assert reopened.classify([*rows, new])["reused"] == 1
    assert len(calls) == 1 and reopened.label(new)["source"] == "cache"
    for key, value in (
        ("amount", 1300),
        ("merchant", "Other"),
        ("date", "2026-10-16"),
        ("accountId", "different"),
    ):
        assert reopened.label({**row, key: value}) is None
    assert reopened.label({**row, "categoryId": "different", "note": "changed"})
    reopened.override([*rows, new], row["id"], fingerprint(row), NO)
    assert all(reopened.label(r)["decision"] == NO for r in [*rows, new])
    fourth = {**row, "id": "four", "date": "2026-12-15"}
    assert reopened.classify([*rows, new, fourth])["reused"] == 1
    assert reopened.label(fourth)["source"] == "manual"
    assert len(calls) == 1
    changed = {**config, "model": "new-model"}
    reopened.configure(changed)
    assert reopened.label(row)["decision"] == NO
    fresh = Engine(context)
    fresh.saved["labels"] = {}
    fresh.configure(config)
    fresh.classify([row])
    fresh.configure(changed)
    assert fresh.label(row) is None


def test_batches_are_bounded_partial_work_is_durable_and_uncertain_results_remain_reviewable(
    context, config, row, monkeypatch
):
    engine = Engine(context)
    engine.configure(config)
    calls = responder(monkeypatch, {**YES, "score": 0.5})
    rows = [{**row, "id": str(i), "merchant": f"Merchant {i}"} for i in range(22)]
    assert engine.classify(rows)["remaining"] == 2
    engine.write()
    other = Engine(context)
    assert other.classify(rows)["classified"] == 2
    assert other.classify(rows)["classified"] == 0
    assert [len(c["transactions"]) for c in calls] == [20, 2]
    assert (
        other.classify(
            [{**row, "pending": True}, {**row, "isTransfer": True}, {**row, "amount": -1}]
        )["classified"]
        == 0
    )
    report = other.report(
        rows, [{"id": "account", "name": "Fictional account"}], [], "2026-10", "USD"
    )
    assert report["needsReview"] == 22 and report["estimatedMonthly"] == 0


@pytest.mark.parametrize(
    "output",
    [
        "invalid",
        [],
        {},
        {"classifications": []},
        {"classifications": [None]},
        {"classifications": [{}]},
        {"classifications": [{"transactionId": "wrong", **YES}]},
        {"classifications": [{"transactionId": [], **YES}]},
        {"classifications": [{"transactionId": "0", **YES, "extra": True}]},
        {"classifications": [{"transactionId": "0", **YES, "score": float("nan")}]},
        {"classifications": [{"transactionId": "0", **YES, "isSubscription": 1}]},
        {"classifications": [{"transactionId": "0", **YES, "cadence": "daily"}]},
    ],
)
def test_malformed_model_output_preserves_saved_state(context, config, row, monkeypatch, output):
    engine = Engine(context)
    engine.configure(config)
    before = context.read_file("subscriptions.json")
    monkeypatch.setattr(
        ai, "complete", lambda *a, **k: output if type(output) is str else json.dumps(output)
    )
    with pytest.raises(StoreError, match="invalid subscription"):
        engine.classify([row])
    assert context.read_file("subscriptions.json") == before


def test_duplicate_model_ids_rejected_atomically(context, config, row, monkeypatch):
    engine = Engine(context)
    engine.configure(config)
    monkeypatch.setattr(
        ai,
        "complete",
        lambda *a, **k: json.dumps({"classifications": [{"transactionId": "0", **YES}] * 2}),
    )
    with pytest.raises(StoreError):
        engine.classify([row, {**row, "id": "two", "merchant": "Other"}])


@pytest.mark.parametrize(
    "change",
    [
        {"provider": []},
        {"provider": "missing"},
        {"model": ""},
        {"model": None},
        {"model": "x" * 201},
        {"model": "control\x00"},
        {"effort": []},
        {"effort": "bad"},
        {"connectionId": "invalid"},
        {"consent": False},
        {"extra": True},
    ],
)
def test_invalid_configuration(config, change):
    with pytest.raises(StoreError):
        configuration({**config, **change})


def test_invalid_configuration_shape_and_decisions():
    for config in (None, {}, DEFAULTS):
        with pytest.raises(StoreError):
            configuration(config)
    assert not valid_decision(None)
    assert not valid_decision({**YES, "score": True})


@pytest.mark.parametrize(
    "raw",
    [
        b"[1]",
        b"invalid",
        b"{}",
        b'{"version":2,"labels":{},"config":{}}',
        json.dumps({"version": 1, "config": DEFAULTS, "labels": {"a": None}}).encode(),
        json.dumps(
            {
                "version": 1,
                "config": DEFAULTS,
                "labels": {"a": {"fingerprint": 1, "decision": YES, "source": "bad", "policy": ""}},
            }
        ).encode(),
    ],
)
def test_corrupted_state_is_not_silently_replaced(context, raw):
    context.write_file("subscriptions.json", raw)
    with pytest.raises(StoreError, match="invalid"):
        Engine(context)
    assert context.read_file("subscriptions.json") == raw


def test_stale_override_and_invalid_label_do_not_save(context, row):
    engine = Engine(context)
    for target, expected, decision in (
        ("missing", fingerprint(row), NO),
        (row["id"], "stale", NO),
        (row["id"], fingerprint(row), {}),
    ):
        with pytest.raises(StoreError):
            engine.override([row], target, expected, decision)
    pending = {**row, "pending": True}
    with pytest.raises(StoreError):
        engine.override([pending], row["id"], fingerprint(row), NO)
    assert context.read_file("subscriptions.json") is None


def test_report_actual_spikes_monthly_equivalents_currency_history_and_unknown_intervals(
    context, row
):
    engine = Engine(context)
    rows = []
    for i, (cadence, amount) in enumerate(
        (
            ("monthly", 1200),
            ("annual", 12000),
            ("quarterly", 3000),
            ("weekly", 300),
            ("unknown", 900),
        )
    ):
        item = {**row, "id": str(i), "merchant": f"Plan {i}", "amount": amount}
        rows.append(item)
        engine.put(item, {**YES, "cadence": cadence}, "manual")
    old = {**row, "id": "old", "date": "2025-01-15", "merchant": "Ended plan"}
    rows.append(old)
    engine.put(old, YES, "manual")
    euro = {**row, "id": "euro", "currency": "EUR"}
    rows.append(euro)
    engine.put(euro, YES, "manual")
    unknown = {**row, "id": "unchecked", "amount": 888}
    rows.append(unknown)
    negative = {**row, "id": "negative", "amount": -100}
    rows.append(negative)
    no = {**row, "id": "no", "merchant": "Shop"}
    rows.append(no)
    engine.put(no, NO, "manual")
    accounts = [{"id": "account", "name": "Account"}]
    result = engine.report(
        rows, accounts, [{"id": "media", "name": "Entertainment"}], "2026-10", "USD"
    )
    assert result["estimatedMonthly"] == 4500  # 1200 + 1000 + 1000 + 1300
    assert result["actualMonth"] == 17400
    assert result["months"][-1]["change"] == 17400
    assert len(result["months"]) == 12
    assert result["unclassified"] == 1
    assert len(result["subscriptions"]) == 6 and any(
        not r["active"] for r in result["subscriptions"]
    )
    assert result["byCategory"] == [{"name": "Entertainment", "amount": 4500}]
    assert engine.report(rows, accounts, [], "2026-10", "EUR")["estimatedMonthly"] == 1200
    # Identical merchant/amount on another account must never share labels.
    files = {}
    other = replace(
        context,
        budget_id="other",
        read_file=files.get,
        write_file=lambda n, r: files.__setitem__(n, r),
    )
    assert Engine(other).label(row) is None
