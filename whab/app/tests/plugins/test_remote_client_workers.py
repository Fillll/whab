"""Reviewed installed client versions execute only descriptor metadata in children."""

import base64
import csv
import hashlib
import io
import json
import zipfile

import httpx
from fastapi.testclient import TestClient
from whab_plugin_client.plugin import plugin as client_plugin

from whab import __version__
from whab.app import create_app
from whab.config import Settings
from whab.plugins import LedgerService, PluginError, PluginRegistry
from whab.plugins.environments import ApprovedWheel, inspect_wheel
from whab.plugins.remote_client_adapter import client_record
from whab.plugins.remote_transport import RemoteStore

from .test_environments import local_closure as local_closure

TOKEN = "fictional-worker-host-token-" + "a" * 32
URL = "https://fictional-authority.example"
BUDGET = "fictional-authority"


def wheel(directory, version, *, missing_setting=False):
    distribution = "whab_plugin_fictional_client"
    package = "fictional_client"
    prefix = f"{distribution}-{version}.dist-info/"
    source = (
        "import os\n"
        "from whab.plugins import Plugin, PluginContract, RemoteLedgerClient\n"
        "if any(name.startswith('CLIENT_') for name in os.environ):\n"
        "    raise RuntimeError('Host client configuration must not enter the child')\n"
        f"plugin = Plugin('client', 'Reviewed fictional client {version}',\n"
        f"    storage=RemoteLedgerClient(token_setting={'CLIENT_MISSING_TOKEN' if missing_setting else 'CLIENT_TOKEN'!r}),\n"
        "    contract=PluginContract(authority='remote', lifecycle='live'))\n"
    )
    files = {f"{package}/__init__.py": source.encode(),
        prefix + "METADATA": f"Metadata-Version: 2.3\nName: {distribution}\nVersion: {version}\nRequires-Python: >=3.14\nRequires-Dist: whab=={__version__}\n".encode(),
        prefix + "WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        prefix + "entry_points.txt": f"[whab.plugins]\nclient = {package}:plugin\n".encode()}
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, raw in files.items():
        writer.writerow((name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("="), len(raw)))
    writer.writerow((prefix + "RECORD", "", ""))
    files[prefix + "RECORD"] = record.getvalue().encode()
    path = directory / f"{distribution}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return ApprovedWheel(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_real_immutable_versions_keep_host_transport_unknown_request_reopen_exact_version_and_failed_candidate_unchanged(tmp_path, local_closure, store, monkeypatch):
    import pytest

    monkeypatch.setenv("CLIENT_URL", URL)
    monkeypatch.setenv("CLIENT_TOKEN", TOKEN)
    monkeypatch.setenv("CLIENT_BUDGET_ID", BUDGET)
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "fictional-runtime-key", False, "", "", (), storage="client", ui="")
    server = LedgerService(store, budget_id=BUDGET)
    commands, dropped = [], []

    def authority(request):
        assert request.headers["authorization"] == "Bearer " + TOKEN
        if request.method == "GET":
            result = server.query(request.url.path.rsplit("/", 1)[-1], request.url.params["workspace"], request.url.params.get("month"))
        else:
            body = json.loads(request.content)
            commands.append(body)
            result = server.command(body["operation"], body["workspace"], body["payload"], request_id=body["request_id"], expected_revision=body["expected_revision"])
            if not dropped:
                dropped.append(True)
                raise httpx.ReadTimeout("Fictional lost result")
        return httpx.Response(200, json={"budgetId": BUDGET, **result})

    def application():
        remote = RemoteStore(URL, TOKEN, BUDGET, transport=httpx.MockTransport(authority))
        app = create_app(store=remote, settings=settings, plugins=PluginRegistry((client_plugin,)))
        return app, remote

    app, remote = application()
    first = second = None
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            controller = app.state.remote_client
            transport, ledger = remote._client, app.state.ledger_service
            assert not any(inspect_wheel(item).distribution.startswith(("whab_plugin_", "whab-plugin-")) for item in local_closure)
            one = wheel(tmp_path, "1.0.0")
            assert http.portal.call(controller.upgrade, "client", (*local_closure, one)) == 2
            first = app.state.budget_runtime._active.services["plugins"].workers["client"]
            assert first.worker.info["version"] == "1.0.0"
            assert first.worker.remote_client() == client_plugin.storage
            first_ready = (first.environment.path / "ready.json").read_bytes()
            first_install = first.environment.path
            before = http.get("/api/storage/remote/status").json()
            broken = wheel(tmp_path, "1.1.0", missing_setting=True)
            with pytest.raises(PluginError):
                http.portal.call(controller.upgrade, "client", (*local_closure, broken))
            assert first.worker.healthy and http.get("/api/storage/remote/status").json()["revision"] == before["revision"]
            # Optional selection rebuilds must preserve the required client
            # binding rather than retiring/restarting its descriptor child.
            http.portal.call(app.state.live_plugins.activate, ())
            assert app.state.budget_runtime._active.services["plugins"].workers["client"] is first
            assert first.worker.healthy
            payload = {"name": "Fictional worker cash", "type": "depository", "balance": 125, "currency": "USD", "onBudget": True, "subtype": None}
            assert http.post("/api/accounts", json={"workspace": "personal", **payload}).status_code == 400
            original = json.loads(json.dumps(commands[0]))
            two = wheel(tmp_path, "2.0.0")
            assert http.portal.call(controller.upgrade, "client", (*local_closure, two)) == 4
            second = app.state.budget_runtime._active.services["plugins"].workers["client"]
            assert second.worker.info["version"] == "2.0.0" and second.worker.healthy and not first.worker.healthy
            assert second.environment.identifier != first.environment.identifier
            assert first_install.is_dir() and (first_install / "ready.json").read_bytes() == first_ready
            assert remote._client is transport and app.state.ledger_service is ledger
            assert http.get("/api/storage/remote/status").json()["unknown_outcome"]
            assert http.post("/api/storage/remote/finish", json={"operation": "add_account", "workspace": "personal", "payload": payload}).json()["complete"]
            assert commands[1] == original and len(store.accounts("personal")) == 1
            active = http.get("/api/storage/remote/status").json()
            assert active["worker"]["version"] == "2.0.0"
            assert client_record(second.worker.remote_client()) == client_record(client_plugin.storage)
            http.portal.call(controller.select, "client", False, active["revision"])
            active = http.get("/api/storage/remote/status").json()
            assert not active["enabled"] and active["worker"]["version"] == "2.0.0"
            assert not second.worker.healthy
    finally:
        remote.close()
    assert not second.worker.healthy
    # A paused reopen inspects only host-owned metadata. It must not execute
    # even a retained descriptor worker until the explicit enable succeeds.
    with monkeypatch.context() as patch:
        patch.setattr("whab.budgets.remote_runtime._prepare_worker", lambda *args, **kwargs: pytest.fail("Paused reopen executed a worker"))
        app, remote = application()
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            reopened = http.get("/api/storage/remote/status").json()
            assert reopened["worker"] == active["worker"]
            assert not reopened["enabled"]
            assert http.get("/api/workspace?workspace=personal").status_code == 400
            http.portal.call(app.state.remote_client.select, "client", True, reopened["revision"])
            restored = app.state.budget_runtime._active.services["plugins"].workers["client"]
            assert restored.worker.info["version"] == "2.0.0"
            assert restored.environment.identifier == second.environment.identifier
            assert http.get("/api/workspace?workspace=personal").status_code == 200
            assert len(store.accounts("personal")) == 1
    finally:
        remote.close()

    # Model an incompatible host expectation without manufacturing another
    # released artifact. Preflight must reject the genuine retained wheel
    # before the descriptor's interpreter can execute.
    failed = RemoteStore(URL, TOKEN, BUDGET, transport=httpx.MockTransport(authority))
    try:
        with monkeypatch.context() as patch:
            patch.setattr("whab.plugins.environments.__version__", "99.0.0")
            patch.setattr("whab.budgets.remote_runtime._start", lambda *args, **kwargs: pytest.fail("An incompatible saved interpreter executed"))
            with pytest.raises(PluginError) as error:
                create_app(store=failed, settings=settings, plugins=PluginRegistry((client_plugin,)))
            assert error.value.code == "worker_environment_unavailable"
    finally:
        failed.close()
    from whab.budgets.remote_runtime import remote_client_revision, replace_saved_remote_worker

    old_ready = (second.environment.path / "ready.json").read_bytes()
    expected = remote_client_revision(settings.home, "default", "sandbox")
    three = wheel(tmp_path, "3.0.0")
    receipt = replace_saved_remote_worker(settings.home, "default", "sandbox", "client", (*local_closure, three),
        expected_revision=expected, config=settings.storage_configuration(), binding_key=settings.encryption_key)
    assert receipt["choice"]["worker"]["version"] == "3.0.0"
    assert (second.environment.path / "ready.json").read_bytes() == old_ready
    app, remote = application()
    try:
        with TestClient(app, base_url="https://127.0.0.1:5173") as http:
            assert http.get("/api/storage/remote/status").json()["worker"]["version"] == "3.0.0"
            assert http.get("/api/workspace?workspace=personal").status_code == 200
            assert len(store.accounts("personal")) == 1
    finally:
        remote.close()


def test_installed_host_client_uses_native_https_verified_runtime_ca_and_public_server(tmp_path, local_closure):
    """Stock installed CLI and host client exercise TLS/auth with no OS trust change."""
    import os
    import shutil
    import socket
    import ssl
    import subprocess
    import sys
    import time
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    wheelhouse = tmp_path / "native-wheels"
    wheelhouse.mkdir()
    additional = []
    for name, source in (("sqlite", root / "plugins/storage/sqlite"), ("server", root / "plugins/interface/server")):
        target = tmp_path / (name + "-source")
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", "build", "dist"))
        built = subprocess.run([sys.executable, "-I", "-B", "-m", "pip", "--isolated", "wheel", "--no-deps",
                                "--index-url", "https://pypi.org/simple", "--disable-pip-version-check",
                                "--wheel-dir", str(wheelhouse), str(target)], cwd=tmp_path,
                               capture_output=True, timeout=90)
        if built.returncode:
            # Keep authored fixture-build diagnostics available without printing
            # arbitrary subprocess output or caller configuration into CI logs.
            (tmp_path / f"{name}-wheel-build.stdout").write_bytes(built.stdout)
            (tmp_path / f"{name}-wheel-build.stderr").write_bytes(built.stderr)
            built.check_returncode()
        additional.extend(wheelhouse.glob("whab_plugin_" + name + "-*.whl"))
    environment = tmp_path / "installed"
    subprocess.run([sys.executable, "-I", "-m", "venv", str(environment)], cwd=tmp_path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    python = environment / "bin/python"
    subprocess.run([str(python), "-I", "-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check",
                    *(str(item.path) for item in local_closure), *(str(path) for path in additional)],
                   cwd=tmp_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    subprocess.run([str(python), "-I", "-m", "pip", "check"], cwd=tmp_path, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    home = tmp_path / "server-home"
    home.mkdir()
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP", "LANG"}
    env = {name: value for name, value in os.environ.items() if name in allowed or name.startswith("LC_")}
    env.update(WHAB_HOME=str(home), WHAB_ENV="sandbox", PLAID_ENV="sandbox", WHAB_UI="", WHAB_STORAGE="sqlite",
               WHAB_PLUGINS="server", ALLOW_LIVE_PLAID="false", SERVER_TOKEN=TOKEN)
    subprocess.run([str(python), "-I", "-B", "-c", "from pathlib import Path; import sys; from whab.tls import prepare_local_tls; prepare_local_tls(Path(sys.argv[1]))", str(home)],
                   cwd=home, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    process = subprocess.Popen([str(python), "-I", "-B", "-m", "whab", "--port", str(port)], cwd=home, env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        context = ssl.create_default_context(cafile=str(home / "tls/ca.crt"))
        with httpx.Client(verify=context, trust_env=False, timeout=2) as http:
            deadline = time.monotonic() + 15
            while True:
                assert process.poll() is None, "The fictional native host stopped during startup"
                try:
                    if http.get(origin + "/api/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "The fictional native host did not become ready"
                time.sleep(0.02)
            path = origin + "/remote/v1/budgets/default/queries/workspace?workspace=personal"
            assert http.get(path).status_code == 401
            assert http.get(path, headers={"Authorization": "Bearer " + TOKEN}).status_code == 200
        script = r'''
import json, pathlib, ssl, sys
import httpx, whab
from whab.budgets.remote_runtime import binding_identity
from whab.plugins import RemoteLedgerClient
from whab.plugins.remote_selection import RemoteSelectionStore
from whab.plugins.remote_transport import RemoteStore
assert pathlib.Path(whab.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix))
url, token, ca, home = sys.argv[1:]
config = {"CLIENT_URL":url, "CLIENT_TOKEN":token, "CLIENT_BUDGET_ID":"default"}
binding = binding_identity(RemoteLedgerClient(), config, "fictional-native-key", "local", "sandbox")
initial = {"format":1,"binding":binding,"origin_plugin":"client","original_source":None,"choice":{"plugin":"client","adapter":{"protocol_version":1,"url_setting":"CLIENT_URL","token_setting":"CLIENT_TOKEN","budget_setting":"CLIENT_BUDGET_ID"},"enabled":True,"worker":None},"decisions":[],"pending":None,"acknowledged":None}
context=ssl.create_default_context(cafile=ca)
with RemoteSelectionStore(home,"local","sandbox",initial) as journal:
    remote=RemoteStore(url,token,"default",transport=httpx.HTTPTransport(verify=context,trust_env=False))
    remote.pending_journal=journal
    try:
        assert remote.accounts("personal") == []
        remote.add_account("personal",{"name":"Fictional TLS cash","type":"depository","balance":125,"currency":"USD","onBudget":True,"subtype":None})
        assert len(remote.accounts("personal")) == 1
        assert journal.snapshot()["pending"] is None
    finally: remote.close()
print(json.dumps({"native_tls":True,"installed_host":True,"accounts":1,"unknown":False}))
'''
        # The bearer is held only in the isolated test process's environment;
        # subprocess arguments and captured output contain no credential value.
        script = script.replace('url, token, ca, home = sys.argv[1:]', 'import os\nurl, ca, home = sys.argv[1:]\ntoken=os.environ["FICTIONAL_NATIVE_TOKEN"]')
        result = subprocess.run([str(python), "-I", "-B", "-c", script, origin, str(home / "tls/ca.crt"), str(tmp_path / "client-home")],
            cwd=tmp_path, env={**env, "FICTIONAL_NATIVE_TOKEN": TOKEN}, check=True, capture_output=True, text=True, timeout=30)
        assert json.loads(result.stdout) == {"native_tls": True, "installed_host": True, "accounts": 1, "unknown": False}
        assert TOKEN not in result.stdout + result.stderr
        if output := os.environ.get("WHAB_REMOTE_CLIENT_TEST_EVIDENCE"):
            artifacts = []
            for path in (*[item.path for item in local_closure], *additional):
                approved = ApprovedWheel(path, hashlib.sha256(path.read_bytes()).hexdigest())
                record = inspect_wheel(approved)
                artifacts.append({"filename": path.name, "distribution": record.distribution,
                                  "version": record.version, "sha256": approved.sha256})
            Path(output).write_text(json.dumps({"native_tls": True, "installed_host": True,
                "unauthenticated_status": 401, "authenticated_status": 200, "acknowledged_accounts": 1,
                "unknown_outcome": False, "artifacts": artifacts}, indent=2) + "\n")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
