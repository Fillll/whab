"""Owned fictional native HTTPS processes accessed only through management APIs."""

import http.client
import json
import os
import re
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory


class NativeRuntime:
    """Launch one installed host in an entirely new disposable Sandbox home."""

    def __init__(self, storage, selection, *, timeout=30, api_token_setting=None):
        if storage not in {"sqlite", "json"} or type(timeout) not in {int, float} or not 0 < timeout <= 60:
            raise ValueError("Choose a reviewed local backend and a bounded startup deadline.")
        self.storage, self.selection, self.timeout = storage, tuple(selection), timeout
        reserved = {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP", "LANG", "PORT", "VIRTUAL_ENV", "ALLOW_LIVE_PLAID"}
        if api_token_setting is not None and (
            type(api_token_setting) is not str or len(api_token_setting) > 100
            or re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", api_token_setting) is None
            or api_token_setting in reserved
            or not api_token_setting.endswith("_TOKEN")
        ):
            raise ValueError("Choose a dedicated host API token setting, separate from runtime selectors.")
        self.api_token_setting = api_token_setting
        self._api_token = secrets.token_urlsafe(48) if api_token_setting else None
        self._temporary = TemporaryDirectory(prefix="whab-native-conformance-")
        self.home = Path(self._temporary.name)
        self.process = None
        self.port = None
        self.context = None
        self.cleanups = 0

    def __enter__(self):
        try:
            self.start()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def environment(self):
        # An allowlist prevents a caller's credentials or runtime selectors from
        # reaching the fictional host, independently of their variable names.
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP", "LANG"}
        result = {key: value for key, value in os.environ.items() if key in allowed or key.startswith("LC_")}
        result.update(WHAB_HOME=str(self.home), WHAB_ENV="sandbox", PLAID_ENV="sandbox",
                      WHAB_UI="", WHAB_STORAGE=self.storage, WHAB_PLUGINS=",".join(self.selection),
                      ALLOW_LIVE_PLAID="false")
        if self.api_token_setting is not None:
            result[self.api_token_setting] = self._api_token
        return result

    def start(self):
        if self.process is not None:
            raise ValueError("Stop the owned runtime before starting another process.")
        subprocess.run([sys.executable, "-I", "-B", "-c",
                        "from pathlib import Path; import sys; from whab.tls import prepare_local_tls; prepare_local_tls(Path(sys.argv[1]))",
                        str(self.home)], cwd=self.home, env=self.environment(), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=self.timeout)
        self.context = ssl.create_default_context(cafile=str(self.home / "tls/ca.crt"))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.process = subprocess.Popen([sys.executable, "-I", "-B", "-m", "whab", "--port", str(self.port)],
                                        cwd=self.home, env=self.environment(), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        expected = metadata.version("whab")

        def ready():
            if self.process.poll() is not None:
                raise RuntimeError("The fictional host failed startup; verify its installed artifacts and declared requirements.")
            try:
                status, body = self.request("GET", "/api/health")
            except (OSError, http.client.HTTPException):
                return None
            return body if status == 200 and body.get("ready") is True and body.get("version") == expected else None
        self.wait(ready)

    def request(self, method, path, body=None, *, headers=None, authenticated=False):
        if authenticated and (self._api_token is None or not path.startswith("/remote/v1/")):
            raise ValueError("Fictional API authentication requires the configured remote protocol path.")
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.context, timeout=min(5, self.timeout))
        try:
            raw = None if body is None else json.dumps(body, allow_nan=False).encode()
            supplied = {} if raw is None else {"Content-Type": "application/json"}
            if authenticated:
                supplied["Authorization"] = "Bearer " + self._api_token
            supplied.update(headers or {})
            connection.request(method, path, body=raw, headers=supplied)
            response = connection.getresponse()
            value = response.read(2 * 1024 * 1024 + 1)
            if len(value) > 2 * 1024 * 1024:
                raise ValueError("A fictional runtime response exceeded its bound.")
            return response.status, json.loads(value)
        finally:
            connection.close()

    def expect(self, method, path, body=None, *, status=200, headers=None, authenticated=False):
        options = {"headers": headers, "authenticated": authenticated} if headers is not None or authenticated else {}
        actual, value = self.request(method, path, body, **options)
        if actual != status:
            raise AssertionError("A public runtime operation returned an unexpected status.")
        return value

    def wait(self, observe):
        deadline = time.monotonic() + self.timeout
        while True:
            value = observe()
            if value:
                return value
            if time.monotonic() >= deadline:
                raise TimeoutError("The fictional runtime did not reach the required observable state.")
            time.sleep(0.02)

    def operation(self, status_path, identifier):
        def observe():
            rows = self.expect("GET", status_path)["operations"]
            result = next((row for row in rows if row["id"] == identifier), None)
            if result and result["state"] == "failed":
                raise AssertionError("A required runtime transition failed; inspect the recorded replay profile.")
            return result if result and result["state"] == "complete" else None
        return self.wait(observe)

    def stop(self, *, kill=False):
        process, self.process = self.process, None
        if process is None:
            return
        self.cleanups += 1
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL if kill else signal.SIGTERM)
            elif kill:
                process.kill()
            else:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            # A trusted plugin may spawn children which outlive its leader.
            # Escalate only this host's owned session after graceful shutdown.
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def __exit__(self, kind, value, traceback):
        try:
            self.stop()
        finally:
            self._temporary.cleanup()
