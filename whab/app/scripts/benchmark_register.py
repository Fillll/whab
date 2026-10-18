"""Measure server rendering using an isolated, entirely fictional SQLite ledger.

Run from the application directory with an installed bundled environment:
    .venv/bin/python scripts/benchmark_register.py

The reported request time includes the local ASGI server/template work, but not
browser DOM parsing, layout or network latency. No existing runtime is opened.
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
from whab.config import Settings
from whab.domain.accounts import normalize_account
from whab.plugins import PluginRegistry


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=int, default=31)
    parser.add_argument("--transactions", type=int, default=31000)
    parser.add_argument("--requests", type=int, default=5)
    parser.add_argument("--all-accounts", action="store_true")
    parser.add_argument("--page", choices=("transactions", "accounts", "budget"), default="transactions")
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
            with TestClient(create_app(store=store, settings=settings, plugins=registry),
                            base_url="https://127.0.0.1:5173") as client:
                url = f"/budgets/home/{args.page}/2026-10/usd"
                if args.page == "transactions":
                    url += "?all_dates=true" + ("" if args.all_accounts else "&account=account-0")
                headers = {"X-WHAB-Transactions-Fragment": "1"}
                client.get(url, headers=headers).raise_for_status()
                times = []
                for _ in range(args.requests):
                    started = perf_counter()
                    result = client.get(url, headers=headers)
                    result.raise_for_status()
                    times.append(round((perf_counter() - started) * 1000, 1))
                print(f"Fictional ledger: {args.accounts} accounts, {args.transactions} transactions, "
                      f"{len(snapshot['categories'])} categories")
                print(f"View: {args.page}; {'all accounts' if args.all_accounts else 'one account'}")
                print(f"Selected register rows: {result.text.count('class=\"transaction-row\"')}")
                print(f"Server request milliseconds: {times}; median {median(times):.1f}")
                print(f"HTML bytes: {len(result.content)}; option elements: {result.text.count('<option ')}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
