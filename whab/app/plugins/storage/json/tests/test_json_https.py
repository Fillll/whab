"""Actual local TLS and saved-container verification using fictional budgets."""

import os
import socket
import ssl
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
from whab_plugin_devkit import receipt_png

from whab.budgets.container import decode_container
from whab.tls import prepare_local_tls


@contextmanager
def _https(home, budget, *, create=False):
    home.mkdir(mode=0o700, exist_ok=True)
    prepare_local_tls(home)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("WHAB_", "PLAID_", "CLIENT_", "SERVER_", "TOKEN_ENCRYPTION_", "ALLOW_LIVE_"))
    }
    environment.update({
        "WHAB_HOME": str(home), "WHAB_ENV": "sandbox", "WHAB_STORAGE": "json",
        "WHAB_UI": "", "WHAB_PLUGINS": "", "ALLOW_LIVE_PLAID": "false",
    })
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    arguments = [sys.executable, "-m", "whab", "--port", str(port), "--budget", str(budget)]
    if create:
        arguments.extend(["--create", "Fictional JSON verification"])
    process = subprocess.Popen(arguments, cwd=home, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    context = ssl.create_default_context(cafile=str(home / "tls" / "ca.crt"))
    client = httpx.Client(base_url=f"https://127.0.0.1:{port}", verify=context, trust_env=False, timeout=5)
    try:
        deadline = time.monotonic() + 15
        ready = False
        while time.monotonic() < deadline and process.poll() is None:
            try:
                ready = client.get("/api/status").status_code == 200
            except httpx.HTTPError:
                pass
            if ready:
                break
            time.sleep(0.05)
        assert ready, "The fictional JSON HTTPS runtime did not become ready."
        yield client
    finally:
        client.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stderr is not None:
            process.stderr.close()


def _request(client, method, path, **arguments):
    response = client.request(method, path, **arguments)
    assert response.status_code == 200, f"Fictional HTTPS request failed: {method} {path}"
    return response


def test_json_https_save_export_fresh_restore_restart_and_demo_storage(tmp_path):
    home, fresh_home, demo_home = (tmp_path / name for name in ("runtime", "restored", "demo"))
    budget, exported, demo = (tmp_path / name for name in ("fictional.whab", "exported.whab", "demo.whab"))
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    with _https(home, budget, create=True) as client:
        assert _request(client, "GET", "/api/budget").json()["storage"] == "json"
        account = _request(client, "POST", "/api/accounts", json={
            "workspace": "personal", "name": "Fictional checking", "type": "depository", "balance": 10000,
        }).json()["accountId"]
        _request(client, "POST", "/api/transactions", json={
            "workspace": "personal", "accountId": account, "date": f"{month}-01", "merchant": "Fictional shop",
            "amount": 125, "categoryId": "groceries", "note": "Initial fictional note",
        })
        view = _request(client, "GET", "/api/workspace", params={"workspace": "personal", "month": month}).json()
        transaction = view["transactions"][0]["id"]
        _request(client, "PATCH", f"/api/transactions/{transaction}", json={"workspace": "personal", "reviewed": True, "note": ""})
        _request(client, "PUT", f"/api/transactions/{transaction}/receipt", params={"workspace": "personal"}, content=receipt_png(), headers={"Content-Type": "image/png"})
        _request(client, "PUT", "/api/budget/funding/next-month", json={"workspace": "personal", "month": month, "currency": "USD", "assigned": 1000})
        _request(client, "PUT", "/api/budget/groceries", json={"workspace": "personal", "month": month, "assigned": 500})
        _request(client, "PUT", "/api/budget/settings/rollover", json={"workspace": "personal", "month": month, "currency": "USD", "mode": "balance"})
        saved = _request(client, "POST", "/api/budget/save", json={}).json()
        assert saved["copies"][0]["status"] == "saved"
        exported.write_bytes(_request(client, "GET", "/api/budget/export").content)
        demo.write_bytes(_request(client, "GET", "/api/budget/export", params={"workspace": "demo"}).content)
    for path in (budget, exported, demo):
        container = decode_container(path.read_bytes())
        assert container.manifest["storage"] == "json"
        assert "plugins/json/budget.json" in container.files
        assert not any(name.endswith(".sqlite") for name in container.files)
    with _https(fresh_home, exported) as client:
        view = _request(client, "GET", "/api/workspace", params={"workspace": "personal", "month": month}).json()
        assert view["accounts"][0]["id"] == account
        assert view["accounts"][0]["balance"] == 9875
        assert view["transactions"][0]["reviewed"] is True
        assert view["transactions"][0]["note"] == ""
        assert view["summary"]["assignedNextMonth"] == 1000
        assert view["rolloverMode"] == "balance"
        assert next(row for row in view["budget"] if row["categoryId"] == "groceries")["assigned"] == 500
        assert _request(client, "GET", f"/api/transactions/{transaction}/receipt", params={"workspace": "personal"}).content == receipt_png()
        _request(client, "POST", "/api/transactions", json={
            "workspace": "personal", "accountId": account, "date": f"{month}-02", "merchant": "Fictional later purchase",
            "amount": 25, "categoryId": "groceries", "note": "Saved after fresh restoration",
        })
    with _https(fresh_home, exported) as client:
        view = _request(client, "GET", "/api/workspace", params={"workspace": "personal", "month": month}).json()
        assert len(view["transactions"]) == 2
        assert view["accounts"][0]["balance"] == 9850
        assert any(row["note"] == "Saved after fresh restoration" for row in view["transactions"])
    with _https(demo_home, demo) as client:
        assert _request(client, "GET", "/api/budget").json()["storage"] == "json"
        view = _request(client, "GET", "/api/workspace", params={"workspace": "personal", "month": month}).json()
        assert any(row["source"] == "demo" for row in view["transactions"])
    for runtime in (home, fresh_home, demo_home):
        assert list(runtime.rglob("*.json"))
        assert not list(runtime.rglob("*.sqlite"))
