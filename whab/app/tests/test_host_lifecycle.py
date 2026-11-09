"""Fictional native HTTPS hosts prove shared ownership and clean idle exit."""

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from whab.hosting import HostOwner, local_connection, process_lock, read_endpoint, runtime_directory
from whab.initialization import initialize_home


def clean_environment():
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("WHAB_", "PLAID_", "SERVER_")) and key not in {"TOKEN_ENCRYPTION_KEY", "PORT", "ALLOW_LIVE_PLAID"}}


def test_two_cli_clients_share_verified_host_and_it_exits_without_web(tmp_path, monkeypatch):
    for name in set(os.environ) - set(clean_environment()):
        monkeypatch.delenv(name)
    home = tmp_path / "fictional home"
    directory = runtime_directory(home, "sandbox")

    def query(_):
        with local_connection(home) as session:
            response = session.get("/budgets/default/api/local/v1/queries/accounts", params={"workspace": "personal"})
            assert response.status_code == 200 and response.json()["data"] == []
            record = read_endpoint(directory)
            assert record is not None
            return record["pid"], response.json()["revision"]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(query, range(2)))
        assert first == second
        assert (directory / "host.json").stat().st_mode & 0o077 == 0
        with local_connection(home) as session:
            response = session.post("/budgets/default/api/local/v1/plugins", json={"action": "enable", "plugin": "web"})
            assert response.status_code == 200
            assert response.json()["host_url"] == str(session.base_url).replace("127.0.0.1", "localhost").rstrip("/")
        sleep(3.5)
        assert read_endpoint(directory)["pid"] == first[0]
        with local_connection(home) as session:
            page = session.get("/budgets/home/budget/current-month/usd")
            assert page.status_code == 200
            response = session.post("/budgets/default/api/local/v1/plugins", json={"action": "disable", "plugin": "web"})
            assert response.status_code == 200
        deadline = monotonic() + 20
        while (directory / "host.json").exists():
            assert monotonic() < deadline
            sleep(0.05)
    finally:
        endpoint = read_endpoint(directory)
        if endpoint:
            os.kill(endpoint["pid"], 15)


def test_process_lock_excludes_a_second_owner(tmp_path):
    path = tmp_path / "host.lock"
    with process_lock(path):
        code = "from whab.hosting import process_lock; import sys\nwith process_lock(sys.argv[1], blocking=False): pass"
        result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True)
        assert result.returncode != 0
    with process_lock(path, blocking=False):
        pass


@pytest.mark.parametrize("arguments,persistent", [([], True), (["--transient"], False), (["--transient", "--serve"], True)])
def test_explicit_server_launch_stays_running_and_cli_launch_is_temporary(tmp_path, monkeypatch, arguments, persistent):
    from whab import __main__ as main
    from whab.config import Settings

    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    monkeypatch.setattr(main.Settings, "load", lambda: settings)
    monkeypatch.setattr(main, "validate_tls", lambda *_: None)
    monkeypatch.setattr("whab.initialization.initialize_home", lambda _: None)
    calls = []
    monkeypatch.setattr("whab.hosting.run_host", lambda _, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, "argv", ["whab", *arguments])
    main.main()
    assert calls == [{"transient": "--transient" in arguments, "persistent": persistent}]


def test_initialization_preserves_configuration_keys_and_tls(tmp_path):
    initialize_home(tmp_path)
    paths = [tmp_path / ".env", *list((tmp_path / "tls").iterdir())]
    original = {path: path.read_bytes() for path in paths}
    initialize_home(tmp_path)
    assert all(path.read_bytes() == value for path, value in original.items())
    assert b"WHAB_UI=\nWHAB_PLUGINS=\n" in original[tmp_path / ".env"]
    legacy = tmp_path / "legacy"
    (legacy / "data").mkdir(parents=True)
    with pytest.raises(ValueError, match="original configuration"):
        initialize_home(legacy)
    assert not (legacy / ".env").exists()


def test_fresh_defaults_and_existing_implicit_selections_are_distinct(tmp_path, monkeypatch):
    from whab.config import Settings

    for name in set(os.environ) - set(clean_environment()):
        monkeypatch.delenv(name)
    monkeypatch.setenv("WHAB_HOME", str(tmp_path))
    fresh = Settings.load()
    assert fresh.ui == "" and fresh.plugins == () and fresh.storage == "sqlite"
    (tmp_path / "data").mkdir()
    legacy = Settings.load()
    assert legacy.ui == "web" and legacy.plugins == ("plaid",)
    monkeypatch.setenv("WHAB_UI", "")
    monkeypatch.setenv("WHAB_PLUGINS", "")
    explicit = Settings.load()
    assert explicit.ui == "" and explicit.plugins == ()


@pytest.mark.parametrize("damage", ["permissions", "remote", "invalid", "dead"])
def test_untrusted_or_stale_rendezvous_is_rejected(tmp_path, damage):
    record = {"url": "https://127.0.0.1:12345", "ca": "/fictional/ca", "token": "x" * 43, "pid": os.getpid()}
    if damage == "remote":
        record["url"] = "https://example.com:12345"
    elif damage == "invalid":
        record["token"] = "short"
    elif damage == "dead":
        record["pid"] = 2147483647
    path = tmp_path / "host.json"
    path.write_text(json.dumps(record))
    path.chmod(0o644 if damage == "permissions" else 0o600)
    assert read_endpoint(tmp_path) is None


def test_owner_reaps_dead_clients_and_preserves_visual_or_explicit_service(tmp_path):
    async def run():
        settings = SimpleNamespace(home=tmp_path, plaid_env="sandbox")
        owner = HostOwner(settings, idle_seconds=0)
        application = SimpleNamespace(state=SimpleNamespace(ui_plugin="web"))
        owner.app = SimpleNamespace(state=SimpleNamespace(host=SimpleNamespace(applications={"default": (application, None)})))
        assert owner.required()
        owner.persistent = True
        assert owner.required()
        owner.persistent = False
        application.state.ui_plugin = None
        application.state.budget_runtime = SimpleNamespace(_active=SimpleNamespace(services={"interfaces": SimpleNamespace(enabled=lambda: False)}))
        owner.acquire(2147483647)
        owner.server = SimpleNamespace(should_exit=False, started=True)
        await owner.monitor()
        assert owner.stopping and not owner.clients
        with pytest.raises(ValueError):
            owner.acquire(os.getpid())
    asyncio.run(run())


def test_starting_host_cannot_idle_out_before_its_first_lease(tmp_path, monkeypatch):
    async def run():
        owner = HostOwner(SimpleNamespace(home=tmp_path, plaid_env='sandbox'), idle_seconds=0)
        owner.server = SimpleNamespace(should_exit=False, started=False)
        owner.required = lambda: False
        ticks = []
        async def tick(interval):
            ticks.append(interval)
            if len(ticks) == 2:
                assert not owner.stopping and not owner.server.should_exit
                owner.server.started = True
                lease = owner.acquire(os.getpid())
                owner.release(lease)
        monkeypatch.setattr(asyncio, 'sleep', tick)
        await owner.monitor()
        assert len(ticks) == 2 and owner.stopping and owner.server.should_exit
    asyncio.run(run())


def test_first_ready_tick_resets_a_stale_startup_timestamp(tmp_path, monkeypatch):
    async def run():
        clock = [0]
        monkeypatch.setattr('whab.hosting.monotonic', lambda: clock[0])
        owner = HostOwner(SimpleNamespace(home=tmp_path, plaid_env='sandbox'))
        owner.server = SimpleNamespace(should_exit=False, started=True)
        owner.required = lambda: False
        ticks = []
        async def tick(interval):
            ticks.append(interval)
            if len(ticks) == 1:
                clock[0] = 100  # Blocking construction/startup exceeded the idle window.
            else:
                assert not owner.stopping and owner.last_active == 100
                lease = owner.acquire(os.getpid())
                owner.release(lease)
                clock[0] = 104  # Normal ready-state idle expiry is still enforced.
        monkeypatch.setattr(asyncio, 'sleep', tick)
        await owner.monitor()
        assert len(ticks) == 2 and owner.stopping
    asyncio.run(run())


def test_ready_host_without_a_client_has_bounded_startup_grace(tmp_path, monkeypatch):
    async def run():
        clock = [0]
        monkeypatch.setattr('whab.hosting.monotonic', lambda: clock[0])
        owner = HostOwner(SimpleNamespace(home=tmp_path, plaid_env='sandbox'))
        owner.server = SimpleNamespace(should_exit=False, started=True)
        owner.required = lambda: False
        ticks = []
        async def tick(interval):
            ticks.append(interval)
            if len(ticks) == 1:
                clock[0] = 100
            elif len(ticks) == 2:
                clock[0] = 104
            else:
                assert not owner.stopping and owner.last_active == 100
                clock[0] = 191
        monkeypatch.setattr(asyncio, 'sleep', tick)
        await owner.monitor()
        assert len(ticks) == 3 and owner.stopping and not owner.clients
    asyncio.run(run())


def test_real_slow_startup_admits_lease_then_exits_when_idle(tmp_path, monkeypatch):
    home = tmp_path / 'fictional slow startup'
    env = {**clean_environment(), 'WHAB_HOME': str(home), 'WHAB_UI': '', 'WHAB_PLUGINS': '',
           'WHAB_STORAGE': 'sqlite', 'WHAB_ENV': 'sandbox', 'ALLOW_LIVE_PLAID': 'false'}
    for name in set(os.environ) - set(clean_environment()):
        monkeypatch.delenv(name)
    code = '''
import asyncio
from contextlib import asynccontextmanager
from whab.config import Settings
from whab.hosting import run_host
from whab.initialization import initialize_home
initialize_home(__import__('os').environ['WHAB_HOME'])
def wrap(app):
    original = app.router.lifespan_context
    @asynccontextmanager
    async def slow(current):
        async with original(current):
            await asyncio.sleep(4)
            yield
    app.router.lifespan_context = slow
    return app
run_host(Settings.load(), transient=True, wrapper=wrap)
'''
    process = subprocess.Popen([sys.executable, '-I', '-c', code], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    directory = runtime_directory(home, 'sandbox')
    try:
        deadline = monotonic() + 45
        while not (directory / 'host.json').exists():
            assert process.poll() is None and monotonic() < deadline
            sleep(.05)
        with local_connection(home, 'sandbox', timeout=45) as connection:
            response = connection.get('/budgets/default/api/local/v1/queries/accounts', params={'workspace': 'personal'})
            assert response.status_code == 200 and response.json()['data'] == []
            assert read_endpoint(directory)['pid'] == process.pid
        assert process.wait(timeout=20) == 0
        assert not (directory / 'host.json').exists()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
