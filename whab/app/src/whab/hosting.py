"""Device-local host ownership, authenticated rendezvous and temporary clients.

This module manages processes, never financial data. A failed command is never
replayed. The existing storage authority remains the final budget writer fence.
"""

import asyncio
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import RLock
from time import monotonic
from urllib.parse import urlsplit

import httpx

from whab.budgets.document import atomic_write

_OWNER = None
_STARTUP_SECONDS = 90


def current_owner():
    return _OWNER


@contextmanager
def process_lock(path, *, blocking=True):
    """Lock an inode for the complete ownership interval, without unlink races."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, 0)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(descriptor)


def runtime_directory(home, environment):
    if environment not in {"sandbox", "production"}:
        raise ValueError("Choose sandbox or production.")
    return Path(home).expanduser().resolve() / "run" / environment


def read_endpoint(directory):
    """Only accept a private, same-user record pointing back to loopback."""
    path = Path(directory) / "host.json"
    try:
        info = path.stat()
        if info.st_size > 16 * 1024 or os.name != "nt" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
            raise ValueError
        value = json.loads(path.read_bytes())
        if type(value) is not dict or type(value.get("url")) is not str:
            raise ValueError
        origin = urlsplit(value["url"])
        if (set(value) != {"url", "ca", "token", "pid"} or origin.scheme != "https"
                or origin.hostname != "127.0.0.1" or origin.port is None or origin.path
                or origin.username or origin.password or origin.query or origin.fragment
                or type(value["token"]) is not str or len(value["token"]) != 43
                or type(value["pid"]) is not int or value["pid"] <= 0
                or type(value["ca"]) is not str):
            raise ValueError
        os.kill(value["pid"], 0)
        return value
    except (OSError, ValueError, TypeError, KeyError):
        return None


class HostOwner:
    """One runtime-home/environment owner, shared by all its open budgets."""

    def __init__(self, settings, *, persistent=False, idle_seconds=3):
        self.settings = settings
        self.directory = runtime_directory(settings.home, settings.plaid_env)
        self.token = secrets.token_urlsafe(32)
        self.persistent, self.idle_seconds = persistent, idle_seconds
        self.clients, self.app, self.server = {}, None, None
        self.lock = RLock()
        self.last_active = monotonic()
        self.stopping = False
        self._had_client = False

    def publish(self):
        atomic_write(self.directory / "host.json", json.dumps({
            "url": f"https://127.0.0.1:{self.settings.port}",
            "ca": str(self.settings.home / "tls" / "ca.crt"),
            "token": self.token, "pid": os.getpid(),
        }).encode())

    def acquire(self, pid):
        with self.lock:
            if self.stopping or type(pid) is not int or pid <= 0:
                raise ValueError("The local host is stopping or the client is invalid.")
            identifier = secrets.token_hex(16)
            self.clients[identifier] = pid
            self._had_client = True
            self.last_active = monotonic()
            return identifier

    def release(self, identifier):
        with self.lock:
            self.clients.pop(identifier, None)
            self.last_active = monotonic()

    def required(self):
        if self.persistent:
            return True
        applications = self.app.state.host.applications.values()
        return any(app.state.ui_plugin or app.state.budget_runtime._active.services["interfaces"].enabled()
                   for app, _ in list(applications))

    async def monitor(self):
        ready = False
        while not self.server.should_exit:
            await asyncio.sleep(0.25)
            with self.lock:
                if not self.server.started:
                    continue
                if not ready:
                    # Startup can block the loop past the original idle deadline.
                    # Begin the idle window when readiness is first observed.
                    self.last_active = monotonic()
                    ready = True
                for identifier, pid in list(self.clients.items()):
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        self.release(identifier)
                if self.clients or self.required():
                    self.last_active = monotonic()
                elif monotonic() - self.last_active >= (self.idle_seconds if self._had_client else _STARTUP_SECONDS):
                    self.stopping = True
                    self.server.should_exit = True


def run_host(settings, *, transient=False, persistent=False, wrapper=None):
    """Own the runtime before constructing plugins or opening any budget."""
    import uvicorn

    from whab.app import create_app

    global _OWNER
    directory = runtime_directory(settings.home, settings.plaid_env)
    with process_lock(directory / "host.lock", blocking=False):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0 if transient else settings.port))
            settings = replace(settings, port=listener.getsockname()[1])
            owner = HostOwner(settings, persistent=persistent)
            _OWNER = owner
            try:
                owner.app = create_app(settings=settings)
                application = wrapper(owner.app) if wrapper is not None else owner.app
                owner.server = uvicorn.Server(uvicorn.Config(application, access_log=False, log_level="warning",
                    ssl_certfile=str(settings.tls_cert_file), ssl_keyfile=str(settings.tls_key_file),
                    ssl_version=ssl.PROTOCOL_TLS_SERVER,
                    proxy_headers=False, forwarded_allow_ips="", timeout_graceful_shutdown=60))

                async def serve():
                    owner.publish()
                    monitor = asyncio.create_task(owner.monitor())
                    try:
                        await owner.server.serve(sockets=[listener])
                    finally:
                        monitor.cancel()
                        await asyncio.gather(monitor, return_exceptions=True)
                asyncio.run(serve())
            finally:
                (directory / "host.json").unlink(missing_ok=True)
                _OWNER = None


@contextmanager
def local_connection(home, environment="sandbox", *, ca=None, timeout=_STARTUP_SECONDS):
    """Acquire a host lease before execution; never retry an executed operation."""
    directory = runtime_directory(home, environment)
    child, client, lease = None, None, None
    deadline = monotonic() + timeout
    with process_lock(directory / "start.lock"):
        endpoint = read_endpoint(directory)
        if endpoint is None:
            environment_values = dict(os.environ)
            for name in ("WHAB_BUDGET", "WHAB_BUDGET_COPIES", "WHAB_BUDGET_PASSWORD_ENV"):
                environment_values.pop(name, None)
            child = subprocess.Popen([sys.executable, "-m", "whab", "--home", str(Path(home).expanduser().resolve()),
                "--environment", environment, "--transient"], env=environment_values,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        while lease is None:
            if monotonic() >= deadline or child is not None and child.poll() is not None:
                raise OSError("The local WHAB host could not start. Inspect its status before retrying.")
            endpoint = read_endpoint(directory)
            if endpoint is not None:
                if client is not None:
                    client.close()
                client = httpx.Client(base_url=endpoint["url"], verify=ssl.create_default_context(cafile=ca or endpoint["ca"]),
                    headers={"Authorization": "Bearer " + endpoint["token"]},
                    trust_env=False, follow_redirects=False, timeout=60)
                try:
                    response = client.post("/api/local/v1/leases", json={"pid": os.getpid()})
                    if response.status_code == 200:
                        lease = response.json()["lease"]
                    else:
                        raise OSError("The active WHAB host refused local access.")
                except httpx.ConnectError:
                    pass  # Startup only: no operation has been submitted.
            if lease is None:
                import time
                time.sleep(0.05)
    try:
        yield client
    finally:
        try:
            client.delete("/api/local/v1/leases/" + lease)
        except httpx.HTTPError:
            pass  # Releasing a lease cannot turn a committed result into a retry.
        finally:
            client.close()
