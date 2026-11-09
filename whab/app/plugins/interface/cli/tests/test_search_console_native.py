"""Actual console entry points search and verify fictional history over HTTPS."""

import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

from whab.plugins.local import local_connection


def test_real_console_search_and_separate_category_edit_preserve_history(tmp_path):
    home = tmp_path / "runtime"
    env = {**os.environ, "HOME": str(tmp_path), "WHAB_HOME": str(home), "WHAB_UI": "",
           "WHAB_PLUGINS": "", "WHAB_STORAGE": "sqlite", "ALLOW_LIVE_PLAID": "false"}
    env.pop("WHAB_BUDGET", None)
    executable = Path(sys.executable).parent / "whab"

    def cli(*args):
        result = subprocess.run([str(executable), *args], env=env, capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stderr
        return result.stdout

    # Offline installed help neither reads the nonexistent profile nor creates a home.
    help_text = cli("--profile", "/missing/fictional-profile", "Fictional 家庭", "trnx", "list", "--help")
    assert "complete=true" in help_text and "16 MiB" in help_text and not home.exists()

    def run(*args):
        return json.loads(cli("--environment", "sandbox", *args))

    # Keep this fictional host leased while parallel CI schedules separate CLI processes.
    # Each command still acquires its own verified HTTPS lease and executes normally.
    with patch.dict(os.environ, env, clear=True), local_connection(home, "sandbox"):
        budget = run("budget", "create", "Fictional 家庭")["id"]
        other = run("budget", "create", "Fictional isolated")["id"]

        def advanced(operation, payload):
            revision = run(budget, "trnx", "categories")["revision"]
            return run("api", "--budget", budget, "command", operation, "--parameters", json.dumps(payload),
                       "--revision", revision, "--request-id", uuid4().hex)["data"]

        account = advanced("add_account", {"name": "Fictional 🍏 checking", "type": "depository", "balance": 100000})
        identifiers = {}
        for name, day, merchant, amount, category in [
            ("before", "2026-07-31", "Fictional Village", 900, "groceries"),
            ("boundary", "2026-08-01", "Fictional Village", 1200, "groceries"),
            ("refund", "2026-08-31", "FICTIONAL VILLAGE refund", -250, None),
            ("upper", "2026-08-31", "Fictional Mortgage", 2000, "dining"),
            ("after", "2026-09-01", "Fictional Mortgage", 3000, None),
            ("other-lender", "2026-08-01", "Other Mortgage lender", 4000, "dining"),
            ("unicode", "2026-08-01", "🍏 工作室 Straße", 500, None),
        ]:
            identifiers[name] = advanced("add_transaction", {"accountId": account, "date": day, "merchant": merchant,
                "amount": amount, "categoryId": category, "note": "Preserve fictional annotation 🏡"})
        advanced("edit_transaction", {"transactionId": identifiers["boundary"], "reviewed": True})
        before = run("api", "--budget", budget, "query", "portable")["data"]
        profile = tmp_path / "fictional.budget"
        profile.write_text(json.dumps({"home": str(home), "environment": "sandbox"}))
        filters = ["--since", "2026-08-01", "--until", "2026-08-31", "--merchant", "Fictional Village", "--merchant", "Fictional Mortgage"]
        result = json.loads(cli("--profile", str(profile), "FICTIONAL 家庭", "trnx", "list", *filters,
                                "--ca", str(home / "tls/ca.crt")))
        matched = {identifiers[key] for key in ("boundary", "refund", "upper")}
        assert result["complete"] and result["total"] == result["returned"] == 3
        assert {row["id"] for row in result["data"]} == matched
        assert next(row for row in result["data"] if row["id"] == identifiers["boundary"])["reviewed"] is True
        assert next(row for row in result["data"] if row["id"] == identifiers["refund"])["amount"] == -250
        assert run(other, "trnx", "list")["data"] == []
        unicode = json.loads(cli(budget, "transactions", "list", "--merchant", "🍏 工作室 STRASSE", "--environment", "sandbox", "--home", str(home)))
        assert [row["id"] for row in unicode["data"]] == [identifiers["unicode"]]
        assert run("api", "--budget", budget, "query", "portable")["data"] == before

        # Explicitly separate categorization, then verify completeness with identical filters.
        run(budget, "trnx", "categorize", identifiers["boundary"], "--category", "Dining", "--agent", "Fictional agent")
        saved = run(budget, "transactions", "list", *filters)
        assert saved["total"] == saved["returned"] == 3 and {row["id"] for row in saved["data"]} == matched
        changed = next(row for row in saved["data"] if row["id"] == identifiers["boundary"])
        assert changed["categoryId"] == "dining" and changed["reviewed"] is True
        assert changed["categoryAgent"] == "Fictional agent"
        after = run("api", "--budget", budget, "query", "portable")["data"]
        for old, new in zip(before["transactions"], after["transactions"], strict=True):
            assert {key: value for key, value in old.items() if key not in {"categoryId", "categoryAgent", "categoryEdited"}} == {
                key: value for key, value in new.items() if key not in {"categoryId", "categoryAgent", "categoryEdited"}}
        assert after["history"]["entries"][:len(before["history"]["entries"])] == before["history"]["entries"]
        table = cli("--environment", "sandbox", budget, "trnx", "list", *filters, "--format", "table")
        assert all(identifier in table for identifier in matched) and "3 matched, 3 shown (complete)" in table
    endpoint = home / "run/sandbox/host.json"
    deadline = monotonic() + 15
    while endpoint.exists() and monotonic() < deadline:
        sleep(.05)
    assert not endpoint.exists(), "Fictional host must stop before its home is removed"
