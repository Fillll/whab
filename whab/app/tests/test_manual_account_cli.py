"""Manual-account creation crosses the real CLI, HTTPS and both native stores."""

import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

import pytest

from whab.budgets.session import BudgetSession
from whab.domain.history import financial
from whab.plugins import StorageContext, load_plugins
from whab.plugins.local import local_connection
from whab.plugins.storage import open_storage


def _environment(temporary, home, storage):
    environment = {
        key: os.environ[key]
        for key in ("PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE")
        if key in os.environ
    }
    environment.update(
        HOME=str(temporary), WHAB_HOME=str(home), WHAB_ENV="sandbox",
        WHAB_STORAGE=storage, WHAB_UI="", WHAB_PLUGINS="",
        ALLOW_LIVE_PLAID="false",
    )
    return environment


def _fixture_budget(temporary, storage):
    """A portable fictional import, built only through public storage contracts."""
    session = BudgetSession.create(
        temporary / "fixture", "sandbox", "Fictional 家庭", storage=storage,
        path=temporary / "fictional.whab",
    )
    backend = open_storage(
        load_plugins((storage,)).storage_plugin(storage),
        StorageContext(session.storage_home, "sandbox", {}),
    )
    try:
        snapshot = backend.export_budget("personal")
        snapshot["accounts"] = [{
            "id": "fictional-checking", "name": "Checking 家", "institution": "Fictional",
            "type": "depository", "subtype": "checking", "onBudget": True,
            "balance": 123456, "currency": "USD", "source": "manual",
            "balanceUnknown": False, "stale": False,
        }]
        snapshot["categories"] = [{
            "id": "fictional-transfer", "name": "Transfers", "group": "Finance",
            "kind": "transfer", "target": 0, "currency": "USD",
        }]
        snapshot["transactions"] = [{
            "id": identifier, "accountId": "fictional-checking", "date": date,
            "merchant": "  Fictional historical transfer  ", "merchantLogo": None,
            "amount": amount, "categoryId": None, "pending": False,
            "reviewed": False, "source": "ynab", "note": "[YNAB: cleared]\n  Exact imported note  ",
            "isTransfer": False, "inferredTransfer": False,
            "categoryEdited": False, "deleted": False,
        } for identifier, date, amount in (
            ("historical-outflow", "2023-08-01", 12345),
            ("historical-inflow", "2023-09-15", -2500),
        )]
        snapshot["history"] = {"formatVersion": 1, "baseline": financial(snapshot), "entries": []}
        backend.restore_budget("personal", snapshot)
        session.bind(backend)
        return session.path.read_bytes(), session.id
    finally:
        backend.close()


def _wait_closed(home):
    endpoint = home / "run/sandbox/host.json"
    deadline = monotonic() + 25
    while endpoint.exists() and monotonic() < deadline:
        sleep(.05)
    assert not endpoint.exists()


@pytest.mark.parametrize("storage", ["sqlite", "json"])
def test_manual_account_historical_transfer_and_saved_restart(tmp_path, storage):
    home = tmp_path / "runtime"
    environment = _environment(tmp_path, home, storage)
    raw, budget_id = _fixture_budget(tmp_path, storage)
    executable = Path(sys.executable).parent / "whab"

    def run(*arguments, ok=True, stdin=None):
        # Global options after subcommand arguments exercise the installed parser.
        result = subprocess.run(
            [str(executable), *arguments, "--home", str(home), "--environment", "sandbox"],
            env=environment, input=stdin, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == (0 if ok else 1), result.stderr
        return json.loads(result.stdout if ok else result.stderr)

    def portable():
        return run("api", "--budget", budget_id, "query", "portable")["data"]

    creation = (
        "account", "create", "Épargne 旅行", "--type", "depository", "--subtype", "savings",
        "--off-budget", "--currency", "USD", "--agent", "Codex", "--batch", "fictional-history",
    )
    saved_copy = tmp_path / "saved-fictional.whab"
    with patch.dict(os.environ, environment, clear=True), local_connection(home, "sandbox") as owner:
        opened = owner.post("/api/budget/open", content=raw, headers={"Content-Type": "application/zip"})
        assert opened.status_code == 200, opened.text
        assert opened.json()["id"] == budget_id
        copied = owner.post(f"/budgets/{budget_id}/api/budget/copies", json={
            "name": "Fictional verification copy", "path": str(saved_copy),
        })
        assert copied.status_code == 200, copied.text
        isolated = run("budget", "create", "Fictional isolated")["id"]
        other_before = run("api", "--budget", isolated, "query", "portable")["data"]
        before = portable()
        original_rows = {row["id"]: row for row in run(budget_id, "trnx", "list")["data"]}
        saved_before = saved_copy.read_bytes()

        preview = run("Fictional 家庭", *creation, "--dry-run")
        assert preview["budgetId"] == budget_id and preview["dryRun"] is True
        assert preview["data"]["id"] is None
        assert preview["data"]["balanceUnknown"] is True
        assert portable() == before and saved_copy.read_bytes() == saved_before
        retry = (
            budget_id, *creation, "--revision", preview["expectedRevision"],
            "--request-id", preview["requestId"],
        )
        created = run(*retry)
        account_id = created["data"]["id"]
        assert account_id and created["budgetId"] == budget_id
        account = run("Fictional 家庭", "account", "show", "Épargne 旅行")["data"]
        assert account == created["data"]
        assert run(budget_id, "account", "show", account_id)["data"] == account
        assert account["type"] == "depository" and account["subtype"] == "savings"
        assert account["onBudget"] is False and account["source"] == "manual"
        assert account["currency"] == "USD" and account["balanceUnknown"] is True
        assert account["balanceStatus"] == "unknown" and account["recordedActivity"] == 0
        after_creation = portable()
        assert after_creation["transactions"] == before["transactions"]
        assert len(after_creation["accounts"]) == len(before["accounts"]) + 1
        assert [row for row in after_creation["accounts"] if row["id"] != account_id] == before["accounts"]
        durable_created = saved_copy.read_bytes()
        replay = run(*retry)
        assert replay["data"] == created["data"] and replay["replayed"]
        assert portable() == after_creation and saved_copy.read_bytes() == durable_created
        entry = run(budget_id, "history", "list")["data"]["entries"][0]
        assert entry["operation"] == "add_account" and entry["actor"] == "Codex"
        assert entry["actorKind"] == "agent" and entry["batchId"] == "fictional-history"
        assert entry["requestId"] == preview["requestId"]

        duplicate = run(budget_id, *creation, ok=False)
        assert "name" in duplicate["error"].lower()
        assert portable() == after_creation
        # One observed old revision cannot create a different account after a commit.
        stale = run(
            budget_id, "account", "create", "Fictional stale", "--type", "depository",
            "--off-budget", "--currency", "USD", "--revision", preview["expectedRevision"],
            "--request-id", "fictional-stale-create", ok=False,
        )
        assert "HTTP 409" in stale["error"] and "Budget changed" in stale["error"]
        assert portable() == after_creation

        pairs = []
        created_ids = []
        for original_id, date, amount in (
            ("historical-outflow", "2023-08-01", -12345),
            ("historical-inflow", "2023-09-15", 2500),
        ):
            made = run(
                "Fictional 家庭", "trnx", "create", "--account", "Épargne 旅行",
                "--date", date, "--amount", str(amount), "--currency", "USD",
                "--payee", "Fictional historical transfer", "--note", "Explicit counterpart",
                "--agent", "Codex",
            )
            transaction_id = made["data"]["createdIds"][0]
            created_ids.append(transaction_id)
            pairs.append({"outId": original_id if amount < 0 else transaction_id,
                          "inId": transaction_id if amount < 0 else original_id})
        match = run(budget_id, "trnx", "match-transfers", "--file", "-", "--agent", "Codex", stdin=json.dumps(pairs))
        assert match["data"]["count"] == 4
        all_ids = [*original_rows, *created_ids]
        run(budget_id, "trnx", "categorize", *all_ids, "--category", "Finance / Transfers", "--agent", "Codex")
        run(budget_id, "trnx", "review", *all_ids, "--reviewed", "--agent", "Codex")
        rows = {row["id"]: row for row in run(budget_id, "trnx", "list")["data"]}
        assert set(rows) == set(all_ids)
        for identifier, original in original_rows.items():
            for field in ("id", "accountId", "date", "merchant", "amount", "currency", "note", "source", "pending"):
                assert rows[identifier][field] == original[field]
        assert all(row["reviewed"] and row["categoryId"] == "fictional-transfer" and row["transferPeerId"] for row in rows.values())
        final_account = run(budget_id, "account", "show", account_id)["data"]
        assert final_account["balanceUnknown"] is True and final_account["balanceStatus"] == "unknown"
        assert final_account["recordedActivity"] == 9845
        assert sum(row["amount"] for row in rows.values() if row["accountId"] == account_id) == -9845
        # Classification preserves the native accumulator. Recorded activity is
        # the signed transaction sum, so changing asset/liability vocabulary
        # must not flip its meaning or invent a verified current balance.
        register_before = portable()
        for kind in ("credit", "depository"):
            run(budget_id, "account", "classify", account_id, "--type", kind, "--off-budget")
            reclassified = run(budget_id, "account", "show", account_id)["data"]
            assert reclassified["recordedActivity"] == 9845
            assert reclassified["balance"] == final_account["balance"]
            assert reclassified["balanceUnknown"] and reclassified["balanceStatus"] == "unknown"
        final_account = reclassified
        final = portable()
        assert final["transactions"] == register_before["transactions"]
        assert next(row for row in final["accounts"] if row["id"] == "fictional-checking") == before["accounts"][0]
        assert run("api", "--budget", isolated, "query", "portable")["data"] == other_before
        status = owner.get(f"/budgets/{budget_id}/api/budget").json()
        assert status["storage"] == storage and all(copy["status"] == "saved" for copy in status["copies"])
        durable = saved_copy.read_bytes()
    _wait_closed(home)

    # Exact recovery is durable across host restart in the same runtime, even
    # after transactions have changed the account's recorded activity.
    with patch.dict(os.environ, environment, clear=True), local_connection(home, "sandbox"):
        assert portable() == final
        reopened_copy = saved_copy.read_bytes()
        replay = run(*retry)
        assert replay["replayed"] and replay["data"]["id"] == account_id
        assert portable() == final and saved_copy.read_bytes() == reopened_copy
        history = run(budget_id, "history", "list")["data"]["entries"]
        assert sum(row["requestId"] == preview["requestId"] for row in history) == 1
    _wait_closed(home)

    # Reopen the actual saved copy in a wholly empty runtime. No database or
    # runtime metadata is edited. Account identity and permanent history travel
    # with the file; device-local host revision/request mappings do not.
    home = tmp_path / "restored-runtime"
    environment = _environment(tmp_path, home, storage)
    assert not home.exists()
    with patch.dict(os.environ, environment, clear=True), local_connection(home, "sandbox") as owner:
        opened = owner.post("/api/budget/open", content=durable, headers={"Content-Type": "application/zip"})
        assert opened.status_code == 200, opened.text
        assert opened.json()["id"] == budget_id
        assert portable() == final
        assert run("Fictional 家庭", "account", "show", account_id)["data"] == final_account
        rejected = run(*retry, ok=False)
        assert "HTTP 409" in rejected["error"] and "Budget changed" in rejected["error"]
        assert portable() == final
        history = run(budget_id, "history", "list")["data"]["entries"]
        assert sum(row["requestId"] == preview["requestId"] for row in history) == 1
    _wait_closed(home)
