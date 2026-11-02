"""The installed CLI transport verifies a real temporary CA and persists labels."""

import json
import socket
from threading import Event, Thread
from time import monotonic

import uvicorn
from whab_plugin_cli.client import main
from whab_plugin_server.plugin import plugin as server_plugin
from whab_plugin_subscriptions.plugin import plugin as subscriptions

from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry
from whab.tls import prepare_local_tls


def test_native_ca_verified_cli_and_saved_label_reopen(store, tmp_path, monkeypatch, capsys):
    token = "fictional-native-token-" + "x" * 32
    monkeypatch.setenv("WHAB_CLI_TOKEN", token)
    monkeypatch.setenv("SERVER_TOKEN", token)
    certificate, key = prepare_local_tls(tmp_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    settings = Settings(tmp_path, port, "sandbox", "", "", "", False, "", "", (), ui="", tls_cert_file=certificate, tls_key_file=key)
    app = create_app(store=store, settings=settings, plugins=PluginRegistry((server_plugin, subscriptions)))
    account = store.add_account("personal", {"name": "Fictional checking", "type": "depository", "balance": 5000})
    transaction = store.add_transaction("personal", {"accountId": account, "date": "2026-10-15", "merchant": "Fictional membership", "amount": 1200, "categoryId": None})
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, ssl_certfile=str(certificate), ssl_keyfile=str(key)))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 5
        while not server.started:
            assert thread.is_alive() and monotonic() < deadline
            Event().wait(0.01)
        args = ["--url", f"https://localhost:{port}", "--budget", "default"]
        assert main(args + ["query", "accounts"]) == 1
        capsys.readouterr()
        connection_file = tmp_path / "default.budget"
        connection_file.write_text(json.dumps({"url": f"https://localhost:{port}", "budget": "default", "ca": "tls/ca.crt"}))
        args = [str(connection_file)]
        assert main(args + ["describe", "--json"]) == 0
        documentation = json.loads(capsys.readouterr().out)
        assert documentation["source"] == "authority"
        assert "/remote/v1/budgets/{budget_id}/commands" in documentation["http"]["openapi"]["paths"]
        feature = next(row for row in documentation["plugins"] if row["id"] == "subscriptions")
        assert feature["enabled"] and feature["documented"]
        assert token not in json.dumps(documentation) and "Fictional membership" not in json.dumps(documentation)
        report_args = ["feature-query", "subscriptions", "report", "--parameters", '{"month":"2026-10","currency":"USD"}']
        assert main(args + report_args) == 0
        report = json.loads(capsys.readouterr().out)
        from whab.plugins.operation_schema import validate_value

        report_operation = next(row for row in feature["documentation"]["operations"] if row["kind"] == "feature-query" and row["name"] == "report")
        validate_value(report_operation["output"], report["data"])
        row = report["data"]["transactions"][0]
        assert main(args + ["feature-command", "subscriptions", "set_label", "--parameters", json.dumps({"transactionId": transaction, "fingerprint": row["fingerprint"], "isSubscription": True, "cadence": "monthly"}), "--request-id", "native-label", "--revision", report["revision"]]) == 0
        capsys.readouterr()
        assert main(args + report_args) == 0
        assert json.loads(capsys.readouterr().out)["data"]["estimatedMonthly"] == 1200
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        assert not thread.is_alive()
    reopened = create_app(store=store, settings=settings, plugins=PluginRegistry((server_plugin, subscriptions)))
    restored = reopened.state.ledger_service.query("feature", "personal", parameters={"pluginId": "subscriptions", "action": "report", "parameters": {"month": "2026-10", "currency": "USD"}})
    assert restored["data"]["estimatedMonthly"] == 1200
