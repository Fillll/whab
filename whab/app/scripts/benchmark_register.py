"""Measure server rendering using an isolated, entirely fictional SQLite ledger.

Run from the application directory with an installed bundled environment:
    .venv/bin/python scripts/benchmark_register.py

The reported request time includes the local ASGI server/template work, but not
browser DOM parsing, layout or network latency. No existing runtime is opened.
Use --portable to include synchronous portable-budget checkpoints on writes.
"""

from argparse import ArgumentParser
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from fastapi.testclient import TestClient
from whab_plugin_sqlite import Store
from whab_plugin_web.plugin import plugin as web_plugin

from whab.app import create_app
from whab.budgets.container import decode_container
from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.domain.accounts import normalize_account
from whab.plugins import PluginRegistry


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=int, default=31)
    parser.add_argument("--transactions", type=int, default=31000)
    parser.add_argument("--requests", type=int, default=5)
    parser.add_argument("--all-accounts", action="store_true")
    parser.add_argument("--portable", action="store_true")
    parser.add_argument("--page", choices=(
        "transactions", "accounts", "budget", "account-settings", "transaction-edit",
    ), default="transactions")
    args = parser.parse_args()
    if not (1 <= args.accounts <= 1000 and 1 <= args.transactions <= 250000 and 1 <= args.requests <= 100):
        parser.error("Use 1-1000 accounts, 1-250000 transactions and 1-100 requests.")
    store = Store(":memory:", month="2026-10")
    try:
        snapshot = store.export_budget("personal")
        snapshot["budgetStartMonth"] = "2024-01"
        snapshot["accounts"] = [normalize_account({
            "id": f"account-{index}", "name": f"Account {index}",
            "institution": "Synthetic Bank", "type": "depository", "balance": 1000000,
            "source": "manual", "stale": False, "balanceUnknown": False, "currency": "USD",
        }) for index in range(args.accounts)]
        snapshot["categories"].extend({
            "id": f"category-{index}", "name": f"Category {index}",
            "group": f"Group {index // 12}", "target": 0, "currency": "USD",
        } for index in range(48))
        snapshot["transactions"] = [{
            "id": f"transaction-{index:06}", "accountId": f"account-{index % args.accounts}",
            "date": f"{2024 + (index % 36) // 12}-{1 + index % 12:02}-05",
            "merchant": f"Synthetic payee {index}", "amount": 100,
            "categoryId": snapshot["categories"][0]["id"], "pending": False,
            "reviewed": False, "source": "manual", "note": "Synthetic note",
            "isTransfer": False, "inferredTransfer": False, "categoryEdited": False,
            "deleted": False, "merchantLogo": None,
        } for index in range(args.transactions)]
        store.restore_budget("personal", snapshot)
        with TemporaryDirectory(prefix="whab-register-benchmark-") as home:
            settings = Settings(Path(home), 5173, "sandbox", "", "", "", False, "", "", ())
            registry = PluginRegistry((web_plugin,))
            session = BudgetSession.create(home, "sandbox", "Fictional benchmark", ("web",)) if args.portable else None
            with TestClient(create_app(store=store, settings=settings, plugins=registry, budget_session=session),
                            base_url="https://127.0.0.1:5173") as client:
                url = f"/budgets/home/{args.page}/2026-10/usd"
                if args.page == "transactions":
                    url += "?all_dates=true" + ("" if args.all_accounts else "&account=account-0")
                elif args.page == "account-settings":
                    url = "/api/accounts/account-0/settings?workspace=personal"
                elif args.page == "transaction-edit":
                    url = "/api/transactions/transaction-000000"
                headers = {"X-WHAB-Transactions-Fragment": "1"}

                def request():
                    if args.page == "transaction-edit":
                        return client.patch(url, json={"workspace": "personal", "note": "Synthetic benchmark memo"})
                    return client.get(url, headers=headers)

                request().raise_for_status()
                times = []
                for _ in range(args.requests):
                    started = perf_counter()
                    result = request()
                    result.raise_for_status()
                    times.append(round((perf_counter() - started) * 1000, 1))
                print(f"Fictional ledger: {args.accounts} accounts, {args.transactions} transactions, "
                      f"{len(snapshot['categories'])} categories")
                print(f"View: {args.page}; {'all accounts' if args.all_accounts else 'one account'}")
                print(f"Portable synchronous saves: {args.portable}")
                print(f"Selected register rows: {result.text.count('class=\"transaction-row\"')}")
                print(f"Server request milliseconds: {times}; median {median(times):.1f}")
                print(f"Response bytes: {len(result.content)}; option elements: {result.text.count('<option ')}")
                if session:
                    assert session.status == "saved"
                    saved = decode_container(session.path.read_bytes())
                    restored = Store(":memory:", month="2026-10")
                    try:
                        prefix = "plugins/sqlite/"
                        restored.prepare_budget_files("personal", {
                            name[len(prefix):]: raw for name, raw in saved.files.items() if name.startswith(prefix)
                        }, existing=False)
                        rows = restored.export_budget("personal")["transactions"]
                    finally:
                        restored.close()
                    assert len(rows) == args.transactions
                    if args.page == "transaction-edit":
                        assert next(row for row in rows if row["id"] == "transaction-000000")["note"] == "Synthetic benchmark memo"
                    print("Saved portable budget independently reopened and verified.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
