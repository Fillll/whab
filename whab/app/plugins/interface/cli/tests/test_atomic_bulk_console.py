"""Actual console -> verified HTTPS host -> native SQLite/JSON worker."""

import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

import pytest
from whab_plugin_sqlite import Store

from whab.initialization import initialize_home
from whab.plugins import StorageContext, load_plugins
from whab.plugins.local import local_connection
from whab.plugins.storage import open_storage


@pytest.mark.parametrize("storage", ["sqlite", "json"])
def test_bulk_real_console_preservation_restart_and_saved_restore(tmp_path, storage):
    home = tmp_path / "runtime"
    env = {
        key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "LC_ALL") if key in os.environ
    }
    env.update(
        HOME=str(tmp_path),
        WHAB_HOME=str(home),
        WHAB_UI="",
        WHAB_PLUGINS="",
        WHAB_STORAGE=storage,
        ALLOW_LIVE_PLAID="false",
    )
    with patch.dict(os.environ, env, clear=True):
        initialize_home(home)
    seed = Store(":memory:", month="2026-11")
    try:
        a = seed.add_account(
            "personal",
            {"name": "🍏旅行 card", "type": "other", "balance": 100000, "onBudget": False},
        )
        b = seed.add_account(
            "personal", {"name": "旅行 card", "type": "other", "balance": 100000, "onBudget": False}
        )
        c = seed.add_category("personal", {"name": "食料品", "group": "家族", "target": 1234})
        ids = [
            seed.add_transaction(
                "personal",
                {
                    "accountId": a,
                    "merchant": "Fictional shop",
                    "date": "2026-11-01",
                    "amount": 100,
                    "categoryId": None,
                    "note": "[YNAB]  ",
                },
            )
            for _ in range(100)
        ]
        snapshot = seed.export_budget("personal")
    finally:
        seed.close()
    snapshot.pop("history", None)
    for row in snapshot["transactions"]:
        row["merchant"] = "  Fictional merchant  "
    backend = open_storage(
        load_plugins((storage,)).storage_plugin(storage), StorageContext(home, "sandbox", {})
    )
    try:
        backend.restore_budget("personal", snapshot)
    finally:
        backend.close()
    executable = str(Path(sys.executable).parent / "whab")

    def run(*args, ok=True, stdin=None):
        result = subprocess.run(
            [executable, "--home", str(home), "--environment", "sandbox", *args],
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == (0 if ok else 1), result.stderr
        return json.loads(result.stdout if ok else result.stderr)

    def query(name):
        return run("api", "--budget", "default", "query", name)

    def rows():
        return {row["id"]: row for row in query("transactions")["data"]}

    with patch.dict(os.environ, env, clear=True), local_connection(home, "sandbox"):
        before = rows()
        plan = run(
            "My budget",
            "trnx",
            "categorize",
            *ids,
            "--category",
            "家族 / 食料品",
            "--agent",
            "Codex",
            "--dry-run",
        )
        assert plan["dryRun"] and plan["data"]["count"] == 100 and rows() == before
        saved = run(
            "My budget",
            "trnx",
            "categorize",
            *ids,
            "--category",
            "家族 / 食料品",
            "--agent",
            "Codex",
            "--revision",
            plan["revision"],
        )
        assert saved["data"] == plan["data"] and len(saved["data"]["changes"]) == 100
        history = run("default", "history", "list")["data"]["entries"]
        assert history[0]["operation"] == "edit_transactions" and history[0]["changeCount"] == 100
        assert history[0]["actor"] == "Codex"
        after = rows()
        for row in after.values():
            assert row["merchant"] == "  Fictional merchant  " and row["categoryAgent"] == "Codex"
        duplicate = run("default", "trnx", "categorize", ids[0], ids[0], "--clear", ok=False)
        assert "duplicate" in duplicate["error"] and rows() == after
        edits = {
            "edits": [
                {"transactionId": ids[0], "appendNote": "exact\ntext", "reviewed": True},
                {"transactionId": ids[1], "categoryId": None, "categoryAgent": None, "note": ""},
                {"transactionId": ids[2], "category": c, "categoryAgent": None},
            ]
        }
        saved_note = run(
            "default",
            "trnx",
            "edit-many",
            "--file",
            "-",
            "--agent",
            "Codex",
            "--batch",
            "mixed",
            stdin=json.dumps(edits),
        )
        after = rows()
        assert (
            after[ids[0]]["note"] == "[YNAB]  \nexact\ntext" and after[ids[0]]["reviewed"] is True
        )
        assert (
            after[ids[1]]["categoryId"] is None
            and after[ids[1]]["note"] == ""
            and "categoryAgent" not in after[ids[1]]
        )
        assert "categoryAgent" not in after[ids[2]]
        recovery = (
            "default",
            "trnx",
            "edit-many",
            "--file",
            "-",
            "--agent",
            "Codex",
            "--batch",
            "mixed",
            "--revision",
            saved_note["expectedRevision"],
            "--request-id",
            saved_note["requestId"],
        )
        assert run(*recovery, stdin=json.dumps(edits))["data"] == saved_note["data"]
        assert rows() == after
        assert (
            "refused"
            in run(
                "default",
                "trnx",
                "edit-many",
                "--file",
                "-",
                "--revision",
                saved_note["expectedRevision"],
                stdin=json.dumps(edits),
                ok=False,
            )["error"]
        )
        unicode_edits = {'edits': [({'transactionId':key,'categoryId':c,'reviewed':True} if index < 3 else {'transactionId':key,'note':'🍏'*1000}) for index,key in enumerate(ids)]}
        large = run('default','trnx','edit-many','--file','-',stdin=json.dumps(unicode_edits))
        assert large['data']['count'] == 100
        after = rows()
        assert all(after[key]['note'] == '🍏'*1000 for key in ids[3:])
        account_plan = run(
            "default", "account", "classify", "🍏旅行 card", b, "--type", "credit", "--dry-run"
        )
        assert all(not row["after"]["onBudget"] for row in account_plan["data"]["changes"])
        account_saved = run(
            "default",
            "account",
            "classify",
            "🍏旅行 card",
            "旅行 card",
            "--type",
            "credit",
            "--on-budget",
            "--agent",
            "Codex",
            "--batch",
            "accounts",
        )
        assert account_saved["data"]["count"] == 2 and all(
            row["after"]["onBudget"] for row in account_saved["data"]["changes"]
        )
        single = run('default','account','classify',a,'--type','depository','--agent','Codex','--batch','single',
            '--revision',account_saved['revision'],'--request-id','fictional-single-classification')
        assert single['data']['id'] == a and single['data']['type'] == 'depository'
        assert rows() == after
        assert (
            "duplicate"
            in run(
                "default", "account", "classify", "🍏旅行 card", a, "--type", "credit", ok=False
            )["error"]
        )
        pristine = query("portable")["data"]
        other = run("budget", "create", "Fictional separate")["id"]
        run(other, "trnx", "categorize", *ids, "--clear", ok=False)
        assert query("portable")["data"] == pristine
    endpoint = home / "run/sandbox/host.json"
    deadline = monotonic() + 25
    while endpoint.exists() and monotonic() < deadline:
        sleep(0.05)
    assert not endpoint.exists()
    with patch.dict(os.environ, env, clear=True), local_connection(home, "sandbox"):
        replay = run(*recovery, stdin=json.dumps(edits))
        assert replay["replayed"] and replay["data"] == saved_note["data"] and rows() == after
        history = run("default", "history", "list")["data"]["entries"]
        assert sum(row["requestId"] == saved_note["requestId"] for row in history) == 1
