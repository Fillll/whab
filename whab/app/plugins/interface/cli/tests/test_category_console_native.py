"""The installed console manages only fictional categories through real HTTPS."""

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4


def test_real_console_creates_groups_moves_preserving_history_and_isolates_budgets(
    tmp_path, monkeypatch
):
    today = date.today()
    month = today.strftime("%Y-%m")
    next_month = (today.replace(day=28) + timedelta(days=4)).strftime("%Y-%m")
    home = tmp_path / "runtime"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "WHAB_HOME": str(home),
        "WHAB_UI": "",
        "WHAB_PLUGINS": "",
        "WHAB_STORAGE": "sqlite",
        "ALLOW_LIVE_PLAID": "false",
    }
    env.pop("WHAB_BUDGET", None)
    executable = Path(sys.executable).parent / "whab"

    def cli(*args, ok=True):
        result = subprocess.run(
            [str(executable), *args], env=env, capture_output=True, text=True, timeout=45
        )
        assert result.returncode == (0 if ok else 1), result.stderr
        return json.loads(result.stdout if ok else result.stderr)

    def run(*args, ok=True):
        return cli("--environment", "sandbox", *args, ok=ok)

    budget = run("budget", "create", "Fictional категории")["id"]
    other = run("budget", "create", "Fictional isolated")["id"]
    pristine = run(other, "category", "list")["data"]
    finance = run(budget, "category", "group", "create", "Fictional finance")["data"]
    studio = cli(
        "Fictional категории",
        "category",
        "create",
        "🧰 工作室",
        "--parent",
        finance["id"],
        "--target",
        "1234",
        "--home",
        str(home),
        "--environment",
        "sandbox",
    )["data"]

    def advanced(operation, payload):
        observed = run(budget, "category", "list")
        return run(
            "api",
            "--budget",
            budget,
            "command",
            operation,
            "--parameters",
            json.dumps(payload),
            "--revision",
            observed["revision"],
            "--request-id",
            uuid4().hex,
        )["data"]

    cash = advanced(
        "add_account", {"name": "Fictional checking", "type": "depository", "balance": 100000}
    )
    advanced(
        "add_transaction",
        {
            "accountId": cash,
            "date": month + "-01",
            "merchant": "Fictional invoice",
            "amount": 100,
            "categoryId": studio["id"],
            "note": "Keep note",
        },
    )
    advanced("assign", {"categoryId": studio["id"], "month": month, "assigned": 900})
    advanced("assign", {"categoryId": studio["id"], "month": next_month, "assigned": 700})
    before = run("api", "--budget", budget, "query", "portable")["data"]
    projects = run("Fictional категории", "category", "group", "create", "Fictional проекты")[
        "data"
    ]
    assert projects["children"] == []
    profile = tmp_path / "fictional.budget"
    profile.write_text(json.dumps({"home": str(home), "environment": "sandbox"}))
    moved = cli(
        "--profile",
        str(profile),
        budget,
        "category",
        "move",
        "Fictional finance / 🧰 工作室",
        "--parent",
        "Fictional проекты",
        "--ca",
        str(home / "tls/ca.crt"),
    )["data"]
    assert moved == {**studio, "group": "Fictional проекты", "parentId": projects["id"]}
    added = run(budget, "category", "create", "Fictional venture", "--parent", projects["id"])[
        "data"
    ]
    assert added["target"] == 0 and added["currency"] == "USD"
    assert (
        "already exists"
        in run(
            budget, "category", "create", "fictional VENTURE", "--parent", projects["id"], ok=False
        )["error"]
    )
    listed = run(budget, "category", "list", "--parent", "Fictional проекты")["data"][0]
    assert {row["id"] for row in listed["children"]} == {studio["id"], added["id"]}
    assert run(budget, "category", "list", "--parent", finance["id"])["data"][0]["children"] == []
    after = run("api", "--budget", budget, "query", "portable")["data"]
    for key in ["transactions", "assignments", "accounts"]:
        assert after[key] == before[key]
    assert (
        after["history"]["entries"][: len(before["history"]["entries"])]
        == before["history"]["entries"]
    )
    assert run(other, "category", "list")["data"] == pristine
    # Repeated independent console entry points observe the same persisted IDs.
    assert run(budget, "category", "list", "--parent", projects["id"])["data"][0] == listed
    assert (
        "Candidates"
        in run(budget, "category", "move", studio["id"], "--parent", added["id"], ok=False)["error"]
    )
