"""Serve an isolated HTTPS preview containing only fictional household data.

Run from whab/app with the bundled development environment:
    .venv/bin/python scripts/preview.py
    .venv/bin/python scripts/preview.py --check --port 0

Every run creates a fresh temporary runtime and CA. No .env, existing budget or
banking credentials are read; OS certificate trust stays unchanged. Ctrl-C
removes the preview. --check verifies HTTPS, rendering and a persisted edit,
then exits.
"""

import socket
import ssl
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic

import httpx
import uvicorn
from whab_plugin_sqlite import Store
from whab_plugin_sqlite.plugin import plugin as sqlite_plugin
from whab_plugin_web.plugin import plugin as web_plugin

from whab.app import create_app
from whab.config import Settings
from whab.domain.accounts import normalize_account
from whab.domain.dates import current_month, shift_month
from whab.plugins import PluginRegistry
from whab.tls import prepare_local_tls

EDIT_TRANSACTION = "preview-groceries-0"
EDIT_NOTE = "Verified fictional preview edit"


def seed_fictional_budget(store, month):
    """Fill a newly created ledger; never import files or connected-bank records."""
    snapshot = store.export_budget("personal")
    snapshot["budgetStartMonth"] = shift_month(month, -1)
    snapshot["accounts"] = [normalize_account({
        "id": identifier, "name": name, "institution": "Fictional Bank",
        "type": kind, "balance": balance, "source": "manual", "stale": False,
        "balanceUnknown": False, "currency": "USD",
    }) for identifier, name, kind, balance in (
        ("preview-checking", "Fictional checking", "depository", 485000),
        ("preview-savings", "Fictional savings", "depository", 250000),
        ("preview-card", "Fictional credit card", "credit", 6500),
        ("preview-retirement", "Fictional retirement", "retirement", 1250000),
    )]
    snapshot["transactions"] = []
    snapshot["assignments"] = []
    for offset in (-1, 0):
        selected = shift_month(month, offset)
        snapshot["assignments"].extend({
            "month": selected, "categoryId": category["id"], "assigned": category["target"],
        } for category in snapshot["categories"])
        for index, (identifier, merchant, amount, category, reviewed) in enumerate((
            ("salary", "Fictional employer", -650000, None, True),
            ("rent", "Fictional landlord", 185000, "rent", True),
            ("groceries", "Fictional market", 8754, "groceries", False),
            ("coffee", "Fictional cafe", 625, "dining", True),
            ("utilities", "Fictional utility", 12400, "utilities", True),
            ("refund", "Fictional shop refund", -1800, "shopping", False),
            ("uncategorized", "Fictional card purchase", 2999, None, False),
        ), start=1):
            snapshot["transactions"].append({
                "id": f"preview-{identifier}-{offset}",
                "accountId": "preview-card" if identifier == "uncategorized" else "preview-checking",
                "date": f"{selected}-{index:02}", "merchant": merchant, "amount": amount,
                "categoryId": category, "pending": identifier == "uncategorized" and offset == 0,
                "reviewed": reviewed, "source": "manual", "note": "Fictional preview data",
                "isTransfer": False, "inferredTransfer": False, "categoryEdited": False,
                "deleted": False, "merchantLogo": None,
            })
    store.restore_budget("personal", snapshot)


def check_https(server, listener, *, port, ca_path, month):
    """Exercise the actual TLS listener using its fresh CA, never verify=False."""
    errors = []

    def serve():
        try:
            server.run(sockets=[listener])
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=serve, daemon=True)
    worker.start()
    try:
        deadline = monotonic() + 15
        while not server.started:
            if not worker.is_alive():
                raise RuntimeError("The fictional preview server failed to start.") from (errors[0] if errors else None)
            if monotonic() >= deadline:
                raise RuntimeError("The fictional preview server did not become ready within 15 seconds.")
            worker.join(timeout=0.05)
        origin = f"https://127.0.0.1:{port}"
        with httpx.Client(
            base_url=origin, verify=ssl.create_default_context(cafile=ca_path),
            trust_env=False, timeout=5, follow_redirects=True, headers={"Origin": origin},
        ) as client:
            health = client.get("/api/health")
            health.raise_for_status()
            if health.json().get("application") != "whab" or health.json().get("ready") is not True:
                raise RuntimeError("The HTTPS health response was not a ready WHAB instance.")
            for page, expected in (("budget", "Fictional checking"), ("transactions", "Fictional market")):
                response = client.get(f"/budgets/home/{page}/{month}/usd")
                response.raise_for_status()
                if expected not in response.text:
                    raise RuntimeError(f"The fictional {page} page did not render its seeded data.")
            saved = client.patch(f"/budgets/home/api/transactions/{EDIT_TRANSACTION}", json={
                "workspace": "personal", "note": EDIT_NOTE, "reviewed": True,
            })
            saved.raise_for_status()
    finally:
        server.should_exit = True
        worker.join(timeout=8)
        if worker.is_alive():
            server.force_exit = True
            worker.join(timeout=3)
        if worker.is_alive():
            raise RuntimeError("The fictional preview server did not stop within 11 seconds.")


def serve_preview(listener, *, check=False):
    """Own the entire disposable runtime, including the injected SQLite store."""
    port = listener.getsockname()[1]
    month = current_month()
    with TemporaryDirectory(prefix="whab-fictional-preview-") as temporary:
        home = Path(temporary).resolve()
        cert, key = prepare_local_tls(home)
        ca = home / "tls" / "ca.crt"
        # Construct directly: Settings.load() would consult WHAB_HOME and .env.
        settings = Settings(
            home=home, port=port, plaid_env="sandbox", plaid_client_id="", plaid_secret="",
            encryption_key="", allow_live=False, webhook_url="", redirect_uri="", plugins=(),
            storage="sqlite", ui="web", tls_cert_file=cert, tls_key_file=key,
        )
        database = home / "data" / "whab-sandbox.sqlite"
        with closing(Store(database, month=month)) as store:
            seed_fictional_budget(store, month)
            application = create_app(
                store=store, settings=settings, plugins=PluginRegistry((sqlite_plugin, web_plugin)),
            )
            print("Fictional preview only; temporary data is deleted on exit.", flush=True)
            print(f"Runtime: {home}", flush=True)
            print(f"CA certificate (manual browser trust only): {ca}", flush=True)
            print(f"URL: https://127.0.0.1:{port}/budgets/home/budget/{month}/usd", flush=True)
            print("Ctrl-C stops the preview and removes its runtime.", flush=True)
            server = uvicorn.Server(uvicorn.Config(
                application, host="127.0.0.1", port=port, access_log=False, log_level="warning",
                ssl_certfile=str(cert), ssl_keyfile=str(key), ssl_version=ssl.PROTOCOL_TLS_SERVER,
                proxy_headers=False, forwarded_allow_ips="", lifespan="on",
            ))
            if check:
                check_https(server, listener, port=port, ca_path=ca, month=month)
            else:
                server.run(sockets=[listener])
                if not server.started:
                    raise RuntimeError("The fictional preview server failed to start.")
        if check:
            # Reopen only after server shutdown and closure of the first connection.
            with closing(Store(database, month=month)) as reopened:
                row = next(row for row in reopened.transactions("personal") if row["id"] == EDIT_TRANSACTION)
                if row["note"] != EDIT_NOTE or row["reviewed"] is not True:
                    raise RuntimeError("The saved fictional edit did not survive reopening SQLite.")
            print("PASS: CA-verified HTTPS, health, rendered pages, saved edit and reopened SQLite.", flush=True)
    print("Temporary preview runtime removed.", flush=True)


def main(argv=None):
    parser = ArgumentParser(description=__doc__, formatter_class=RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=5174, help="Loopback HTTPS port (default: 5174; 0 chooses a free port)")
    parser.add_argument("--check", action="store_true", help="Verify HTTPS/render/save/reopen, then stop and remove the preview")
    args = parser.parse_args(argv)
    if args.port != 0 and not 1024 <= args.port <= 65535:
        parser.error("Port must be 0 (a free port), or between 1024 and 65535.")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", args.port))
            except OSError as error:
                parser.error(f"Cannot bind the preview port: {error}. Choose another --port.")
            serve_preview(listener, check=args.check)
    except KeyboardInterrupt:
        print("Temporary preview runtime removed.", flush=True)


if __name__ == "__main__":
    main()
