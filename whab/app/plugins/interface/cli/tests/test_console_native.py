"""The short commands create and reopen budgets on one CA-verified local host."""

import json
from time import monotonic, sleep
from zipfile import ZipFile

from whab_plugin_cli import console

from tests.budgets.helpers import decode_budget


def test_short_commands_create_reuse_and_reopen_real_budgets(tmp_path, monkeypatch, capsys):
    home = tmp_path / "runtime"
    monkeypatch.setenv("WHAB_HOME", str(home))
    monkeypatch.setenv("WHAB_UI", "")
    monkeypatch.setenv("WHAB_PLUGINS", "")
    monkeypatch.setenv("WHAB_STORAGE", "sqlite")
    monkeypatch.setenv("ALLOW_LIVE_PLAID", "false")
    monkeypatch.setattr(console.Path, "home", lambda: tmp_path)

    def run(*args):
        assert console.main(["--environment", "sandbox", *args]) == 0
        return json.loads(capsys.readouterr().out)

    def wait_for_shutdown():
        deadline = monotonic() + 10
        while endpoint.exists() and monotonic() < deadline:
            sleep(0.05)
        assert not endpoint.exists()

    created = run("budget", "create", "empty-budget", "--path", str(tmp_path / "exports"))
    endpoint = home / "run/sandbox/host.json"
    pid = json.loads(endpoint.read_bytes())["pid"]
    assert created["name"] == "empty-budget"
    assert created["path"] == str(tmp_path / "exports/empty-budget.whab")
    manifest, ledger = decode_budget((tmp_path / "exports/empty-budget.whab").read_bytes())
    assert manifest["id"] == created["id"]
    assert ledger["accounts"] == ledger["transactions"] == ledger["assignments"] == []
    assert run("empty-budget", "plugin", "status")["configured"] == []
    enabled = run("empty-budget", "plugin", "enable", "ynab-zip-importer")
    assert enabled["active"] == enabled["configured"] == ["ynab-zip-importer"]
    summary = run("empty-budget", "insights", "summary", "--month", "2026-11")
    assert summary["budgetId"] == created["id"] and summary["data"]["currency"] == "USD"
    assert summary["data"]["summary"]["spent"] == 0
    assert run("empty-budget", "trnx", "non-reviewed")["data"] == []
    uncategorized = run("empty-budget", "trnx", "uncategorized", "--limit", "10")
    assert uncategorized['total'] == uncategorized['returned'] == 0
    other = run("budget", "create", "Other Budget")
    assert other["id"] != created["id"]
    assert run("Other Budget", "plugin", "status")["configured"] == []
    assert {created["id"], other["id"]} <= {row["id"] for row in run("budget", "list")["budgets"]}
    assert json.loads(endpoint.read_bytes())["pid"] == pid
    assert console.main(["--environment", "sandbox", "budget", "create", "empty-budget"]) == 1
    capsys.readouterr()
    export = tmp_path / "fictional.zip"
    with ZipFile(export, "w") as archive:
        archive.writestr("Budget.csv", "Month,Category Group/Category,Category Group,Category,Assigned,Activity,Available\nOct 2026,Living: Food,Living,Food,$100.00,-$12.34,$87.66\n")
        archive.writestr("Register.csv", "Account,Flag,Date,Payee,Category Group/Category,Category Group,Category,Memo,Outflow,Inflow,Cleared\nChecking,,2026-10-01,Fixture shop,Living: Food,Living,Food,Synthetic memo \ud83d\ude00,$12.34,$0.00,Uncleared\n".encode("utf-8", errors="surrogatepass"))
    def import_run(stage, *extra):
        return run("empty-budget", "import", "ynab-zip-importer", stage, str(export), "--currency", "USD", *extra)
    preview = import_run("preview")
    assert preview["canImport"] and preview["transactions"] == 1 and not preview["alreadyImported"]
    assert run("empty-budget", "trnx", "non-reviewed")["data"] == []
    committed = import_run("commit", "--fingerprint", preview["fingerprint"])
    assert committed["budgetId"] == created["id"] and not committed["alreadyImported"]
    assert committed["counts"] == {key: preview[key] for key in ("accounts", "categories", "transactions", "assignments")}
    assert len(run("empty-budget", "trnx", "non-reviewed")["data"]) == 1
    # A categorized import is still unreviewed; those are independent filters.
    assert run("empty-budget", "transactions", "uncategorized")['total'] == 0
    assert run("Other Budget", "trnx", "non-reviewed")["data"] == []
    assert import_run("commit", "--fingerprint", preview["fingerprint"])["alreadyImported"]
    manifest, ledger = decode_budget((tmp_path / "exports/empty-budget.whab").read_bytes())
    assert len(ledger["accounts"]) == len(ledger["transactions"]) == len(ledger["assignments"]) == 1
    assert ledger["transactions"][0]["note"] == "Synthetic memo 😀\n[YNAB: uncleared]"
    wait_for_shutdown()
    # A new temporary host reopens exactly the registered budget and its plugins.
    assert run("empty-budget", "plugin", "status")["configured"] == ["ynab-zip-importer"]
    assert run(created["id"], "transactions", "unreviewed")["budgetId"] == created["id"]
    assert run(created['id'], 'trnx', 'uncategorised')['budgetId'] == created['id']
    # A fresh parser generation still permits an explicit same-file receipt retry.
    assert import_run("commit", "--fingerprint", preview["fingerprint"])["alreadyImported"]
    wait_for_shutdown()


def test_agent_labels_roundtrip_through_whab_api_and_native_storage(tmp_path, monkeypatch, capsys):
    home = tmp_path / "agent-runtime"
    monkeypatch.setattr(console.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("WHAB_UI", "")
    monkeypatch.setenv("WHAB_PLUGINS", "")
    monkeypatch.setenv("WHAB_STORAGE", "sqlite")
    monkeypatch.setenv("ALLOW_LIVE_PLAID", "false")

    def run(*args):
        assert console.main(["--home", str(home), "--environment", "sandbox", *args]) == 0
        return json.loads(capsys.readouterr().out)

    budget = run("budget", "create", "Fictional agent budget")["id"]
    revision = run("api", "--budget", budget, "query", "accounts")["revision"]

    def change(operation, parameters, identity, agent=None):
        nonlocal revision
        args = [
            "api",
            "--budget",
            budget,
            "command",
            operation,
            "--parameters",
            json.dumps(parameters),
            "--revision",
            revision,
            "--request-id",
            identity,
        ]
        if agent is not None:
            args += ["--agent", agent]
        result = run(*args)
        revision = result["revision"]
        return result["data"]

    account = change(
        "add_account",
        {"name": "Fictional checking", "type": "depository", "balance": 100000},
        "create-account",
    )
    identifier = change(
        "add_transaction",
        {
            "accountId": account,
            "date": "2026-10-01",
            "merchant": "Fictional shop",
            "amount": 1200,
            "categoryId": None,
        },
        "create-transaction",
    )
    change(
        "edit_transaction",
        {"transactionId": identifier, "categoryId": "groceries"},
        "agent-category",
        "Codex",
    )
    row = run("api", "--budget", budget, "query", "transactions")["data"][0]
    assert (
        row["categoryAgent"] == "Codex" and row["source"] == "manual" and row["reviewed"] is False
    )
    change("edit_transaction", {"transactionId": identifier, "reviewed": True}, "human-review")
    assert (
        run("api", "--budget", budget, "query", "transactions")["data"][0]["categoryAgent"]
        == "Codex"
    )
    change(
        "edit_transaction", {"transactionId": identifier, "categoryId": "dining"}, "human-category"
    )
    assert "categoryAgent" not in run("api", "--budget", budget, "query", "transactions")["data"][0]
    endpoint = home / "run/sandbox/host.json"
    deadline = monotonic() + 10
    while endpoint.exists() and monotonic() < deadline:
        sleep(0.05)
    assert not endpoint.exists()
