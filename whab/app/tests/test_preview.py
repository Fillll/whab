"""The fictional preview ignores private configuration and cleans up owned state."""

import socket
import sqlite3
import ssl
from importlib import util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preview.py"
SPEC = util.spec_from_file_location("whab_fictional_preview", SCRIPT)
preview = util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


@pytest.fixture
def runtimes(monkeypatch):
    paths = []
    temporary_directory = preview.TemporaryDirectory

    def tracked_directory(**kwargs):
        directory = temporary_directory(**kwargs)
        paths.append(Path(directory.name).resolve())
        return directory

    monkeypatch.setattr(preview, "TemporaryDirectory", tracked_directory)
    return paths


def test_smoke_ignores_private_configuration_and_reopens_saved_fictional_data(tmp_path, monkeypatch, runtimes, capsys):
    private = tmp_path / "existing-runtime"
    private.mkdir()
    files = {
        ".env": "WHAB_ENV=production\nWHAB_PLUGINS=plaid\n",
        "private.whab": "preserve this unrelated budget",
        "server.key": "preserve this unrelated key",
        "server.crt": "preserve this unrelated certificate",
    }
    for name, contents in files.items():
        (private / name).write_text(contents)
    for name, value in {
        "WHAB_HOME": str(private), "WHAB_BUDGET": str(private / "private.whab"),
        "WHAB_BUDGET_PASSWORD_ENV": "MISSING_PREVIEW_PASSWORD", "WHAB_ENV": "production",
        "WHAB_STORAGE": "client", "WHAB_UI": "invalid-interface", "WHAB_PLUGINS": "plaid,server",
        "WHAB_TLS_CERT_FILE": str(private / "server.crt"), "WHAB_TLS_KEY_FILE": str(private / "server.key"),
        "PLAID_CLIENT_ID": "synthetic-private-client", "PLAID_SECRET": "synthetic-private-secret",
        "TOKEN_ENCRYPTION_KEY": "synthetic-private-key", "ALLOW_LIVE_PLAID": "true",
        "PORT": "invalid-port", "HTTPS_PROXY": "https://proxy.invalid:1",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(private)
    monkeypatch.setattr(preview.Settings, "load", lambda: pytest.fail("Must not load private configuration"))
    monkeypatch.setattr("whab.config.load_dotenv", lambda *a, **k: pytest.fail("Must not read .env"))
    monkeypatch.setattr("whab.app.load_plugins", lambda *a, **k: pytest.fail("Use only explicit preview plugins"))
    monkeypatch.setattr(preview.Settings, "bank_configuration", lambda *a: pytest.fail("No banking configuration"))
    applications = []
    create_app = preview.create_app

    def capture_application(**kwargs):
        settings = kwargs["settings"]
        assert settings.home != private and settings.home in runtimes
        assert settings.plaid_env == "sandbox" and settings.allow_live is False
        assert settings.plaid_client_id == settings.plaid_secret == settings.encryption_key == ""
        assert settings.webhook_url == settings.redirect_uri == ""
        assert settings.budget_path is None and settings.copy_paths == () and settings.plugins == ()
        assert settings.enabled_plugins() == ("sqlite", "web")
        assert {plugin.id for plugin in kwargs["plugins"].descriptors()} == {"sqlite", "web"}
        assert Path(kwargs["store"].database_path).is_relative_to(settings.home)
        assert all(account["name"].startswith("Fictional ") for account in kwargs["store"].accounts("personal"))
        application = create_app(**kwargs)
        applications.append(application)
        return application

    monkeypatch.setattr(preview, "create_app", capture_application)
    client = preview.httpx.Client

    def verified_client(**kwargs):
        assert isinstance(kwargs["verify"], ssl.SSLContext)
        assert kwargs["verify"].verify_mode == ssl.CERT_REQUIRED and kwargs["verify"].check_hostname
        assert kwargs["trust_env"] is False
        return client(**kwargs)

    monkeypatch.setattr(preview.httpx, "Client", verified_client)
    preview.main(["--check", "--port", "0"])
    output = capsys.readouterr().out
    assert "PASS: CA-verified HTTPS" in output and "saved edit and reopened SQLite" in output
    assert "CA certificate (manual browser trust only):" in output
    assert all(not path.exists() for path in runtimes)
    assert {path.name: path.read_text() for path in private.iterdir()} == files
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        applications[0].state.store.db.execute("SELECT 1")


def test_startup_failure_closes_store_and_removes_temporary_runtime(monkeypatch, runtimes):
    stores = []
    store_type = preview.Store

    def capture_store(*args, **kwargs):
        store = store_type(*args, **kwargs)
        stores.append(store)
        return store

    def fail_startup(**kwargs):
        raise RuntimeError("Fictional startup failure")

    monkeypatch.setattr(preview, "Store", capture_store)
    monkeypatch.setattr(preview, "create_app", fail_startup)
    with pytest.raises(RuntimeError, match="Fictional startup failure"):
        preview.main(["--check", "--port", "0"])
    assert len(runtimes) == 1 and not runtimes[0].exists()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        stores[0].db.execute("SELECT 1")


def test_failed_smoke_request_stops_listener_and_removes_runtime(monkeypatch, runtimes):
    applications = []
    ports = []
    create_app = preview.create_app

    def capture_application(**kwargs):
        applications.append(create_app(**kwargs))
        ports.append(kwargs["settings"].port)
        return applications[-1]

    def fail_save(*args, **kwargs):
        raise RuntimeError("Fictional save failure")

    monkeypatch.setattr(preview, "create_app", capture_application)
    monkeypatch.setattr(preview.httpx.Client, "patch", fail_save)
    with pytest.raises(RuntimeError, match="Fictional save failure"):
        preview.main(["--check", "--port", "0"])
    assert len(runtimes) == 1 and not runtimes[0].exists()
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", ports[0]), timeout=1):
        pytest.fail("The failed smoke check left its server listening")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        applications[0].state.store.db.execute("SELECT 1")


def test_ctrl_c_cleans_up_and_server_configuration_stays_local_https(monkeypatch, runtimes, capsys):
    stores = []

    def interrupt(server, *, sockets):
        config = server.config
        assert config.host == "127.0.0.1" and config.port == sockets[0].getsockname()[1]
        assert config.proxy_headers is False and config.forwarded_allow_ips == ""
        assert config.ssl_version == ssl.PROTOCOL_TLS_SERVER
        assert Path(config.ssl_certfile).is_relative_to(runtimes[0])
        assert Path(config.ssl_keyfile).is_relative_to(runtimes[0])
        stores.append(config.app.state.store)
        raise KeyboardInterrupt

    monkeypatch.setattr(preview.uvicorn.Server, "run", interrupt)
    preview.main(["--port", "0"])
    assert len(runtimes) == 1 and not runtimes[0].exists()
    assert "Temporary preview runtime removed." in capsys.readouterr().out
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        stores[0].db.execute("SELECT 1")


def test_occupied_port_is_rejected_before_creating_a_runtime(monkeypatch, runtimes, capsys):
    monkeypatch.setattr(preview, "serve_preview", lambda *a, **k: pytest.fail("Do not start on an occupied port"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        with pytest.raises(SystemExit) as error:
            preview.main(["--port", str(occupied.getsockname()[1])])
    assert error.value.code == 2 and "Choose another --port" in capsys.readouterr().err
    assert runtimes == []


@pytest.mark.parametrize("port", ["-1", "443", "65536"])
def test_invalid_port_is_rejected_before_creating_a_runtime(port, runtimes):
    with pytest.raises(SystemExit) as error:
        preview.main(["--port", port])
    assert error.value.code == 2 and runtimes == []
