"""Actual CLI dispatch never confirms a committed edit whose file save failed."""

import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from whab_plugin_cli import console

from tests.budgets.helpers import decode_budget
from whab import hosting
from whab.app import create_app
from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.plugins import PluginRegistry


@pytest.mark.parametrize("action", ["categorize", "classify", "bulk"])
def test_console_reports_unconfirmed_and_exact_original_request_finishes_save(store, tmp_path, monkeypatch, capsys, action):
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "Fictional", (), path=tmp_path / "fictional.whab")
    settings = Settings(session.home, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(store=store, settings=settings, plugins=PluginRegistry(()), budget_session=session)
    service = app.state.ledger_service
    owner = hosting.HostOwner(settings)
    owner.app = app
    monkeypatch.setattr(hosting, "_OWNER", owner)
    monkeypatch.setattr(console.Path, "home", lambda: tmp_path)
    account = service.add_account("personal", {"name": "Fictional", "type": "other", "balance": 1000})
    category = service.add_category("personal", {"name": "Food", "group": "Living", "target": 0})
    transactions = [service.add_transaction("personal", {"accountId": account,
        "date": "2026-10-01", "merchant": "Fictional", "amount": 100, "categoryId": None}) for _ in range(2)]
    initial_file = session.path.read_bytes()
    revision = service.revision
    bundle = session.bundle

    def fail_export(*args, **kwargs):
        raise OSError("Private fictional failure")

    monkeypatch.setattr(session, "bundle", fail_export)
    posts = []
    exchange = console.exchange

    def capture(connection, method, path, **kwargs):
        if method == "POST" and path.endswith("/commands"):
            posts.append(kwargs["json"])
        return exchange(connection, method, path, **kwargs)

    monkeypatch.setattr(console, "exchange", capture)
    with TestClient(app, base_url="https://localhost:5173") as client:
        client.headers["Authorization"] = "Bearer " + owner.token

        @contextmanager
        def connection(*args, **kwargs):
            yield client

        monkeypatch.setattr(console, "local_connection", connection)
        if action == "classify":
            arguments = ["account", "classify", account, "--type", "credit", "--subtype", "credit_card", "--off-budget"]
        else:
            targets = transactions if action == "bulk" else transactions[:1]
            arguments = ["trnx", "categorize", *targets, "--category", category]
        assert console.main([session.id, *arguments, "--agent", "Codex", "--revision", revision,
            "--request-id", "exact-original-request"]) == 1
        output = capsys.readouterr()
        assert not output.out
        message = json.loads(output.err)["error"]
        assert "not confirmed" in message and "exact-original-request" in message and revision in message
        assert "Private fictional failure" not in message and "no new items were saved" not in message
        assert len(posts) == 1 and session.status == "pending"
        assert session.path.read_bytes() == initial_file
        committed = store.export_budget()
        if action == "classify":
            assert committed["accounts"][0]["type"] == "credit"
        else:
            assert sum(row["categoryId"] == category for row in committed["transactions"]) == len(targets)
        monkeypatch.setattr(session, "bundle", bundle)
        recovered = client.post(f"/budgets/{session.id}/api/local/v1/commands", json=posts[0])
        assert recovered.status_code == 200, recovered.text
        assert session.status == "saved"
        assert store.export_budget() == committed
        assert decode_budget(session.path.read_bytes())[1] == committed
