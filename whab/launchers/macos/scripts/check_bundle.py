"""Exercise a complete WHAB.app offline using only disposable fictional data."""

import argparse
import json
import os
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class CheckFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CheckFailure(message)


def environment():
    # Never propagate banking, runtime or Python configuration from the developer.
    return {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG") if key in os.environ}


class BundleCheck:
    def __init__(self, bundle, directory):
        self.bundle = bundle
        self.resources = bundle / "Contents/Resources"
        self.python = self.resources / "python/bin/python3.14"
        self.root = directory / "launcher"
        self.runtime = directory / "runtime"
        self.origin = None
        self.context = None
        self.http = None
        self.checks = []
        self.prefix = [
            str(self.python), "-I", "-B", str(self.resources / "manager/launcher.py"),
            "--root", str(self.root), "--python", str(self.python),
            "--seed", str(self.resources / "seed"),
            "--wheelhouse", str(self.resources / "wheelhouse"),
        ]

    def command(self, *arguments, timeout=300):
        result = subprocess.run([*self.prefix, *arguments], capture_output=True,
                                text=True, timeout=timeout, env=environment(), check=False)
        try:
            value = json.loads(result.stdout)
        except ValueError:
            raise CheckFailure(f"{arguments[0]} did not return a JSON response.") from None
        require(result.returncode == 0 and value.get("ok") is True,
                f"{arguments[0]} failed: {value.get('error', {}).get('message', 'unsuccessful response')}")
        return value

    def verify_signature(self):
        result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(self.bundle)],
                                capture_output=True, timeout=60, env=environment(), check=False)
        require(result.returncode == 0, "The app's code signature failed verification.")

    def get(self, path):
        with self.http.open(self.origin + path, timeout=15) as response:
            require(response.status == 200, f"GET {path} did not succeed.")
            return response.read()

    def health(self, status):
        value = json.loads(self.get("/api/health"))
        require(value.get("application") == "whab" and value.get("ready") is True,
                "The HTTPS endpoint did not report a ready WHAB application.")
        require(value.get("version") == status.get("version") and value.get("build") == status.get("build"),
                "The HTTPS application identity differs from manager status.")
        require(bool(value.get("instance")), "The HTTPS application has no instance identity.")
        return value

    def fictional_account(self):
        accounts = json.loads(self.get("/api/workspace?workspace=personal")).get("accounts", [])
        matches = [account for account in accounts if account.get("name") == "Fictional launcher wallet"]
        require(len(matches) == 1 and matches[0].get("balance") == 12345,
                "The fictional manual account or its integer-cent balance did not persist.")

    def permissions(self):
        require(stat.S_IMODE(self.root.stat().st_mode) == 0o700,
                "The launcher state directory is not private.")
        state_files = list(self.root.glob("*.json"))
        state_files += list(self.root.glob("versions/*/installation.json"))
        state_files += list(self.root.glob("runtimes/*/runtime.json"))
        require(bool(state_files), "No private launcher state was recorded.")
        for path in state_files:
            require(not path.is_symlink() and stat.S_IMODE(path.stat().st_mode) == 0o600,
                    f"State file {path.name} is not private.")
        backup_root = self.root / "backups"
        require(backup_root.is_dir() and any(backup_root.glob("*/backup.json")),
                "Initial installation did not create its private transition backup.")
        for path in [backup_root, *backup_root.rglob("*")]:
            expected = 0o700 if path.is_dir() else 0o600
            require(not path.is_symlink() and stat.S_IMODE(path.stat().st_mode) == expected,
                    "A transition backup file or directory is not private.")

    def run(self):
        for path in (self.python, self.resources / "manager/launcher.py",
                     self.resources / "manager/server.py", self.resources / "seed/build.json"):
            require(path.is_file(), f"The complete bundle is missing {path.name}.")
        require((self.resources / "wheelhouse").is_dir(), "The complete bundle has no offline wheelhouse.")
        self.verify_signature()
        require(self.command("status")["state"] == "unconfigured", "The disposable launcher was already configured.")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.command("configure", "--runtime-home", str(self.runtime), "--port", str(port))
        first = self.command("start", "--offline")
        require(first.get("running") is True and bool(first.get("version")), "Offline first installation did not start.")
        self.checks.append("offline first installation")
        self.origin = first["url"]
        parsed = urllib.parse.urlsplit(self.origin)
        require(parsed.scheme == "https" and parsed.hostname in {"localhost", "127.0.0.1"} and parsed.port == port,
                "The manager returned an unexpected application origin.")
        self.context = ssl.create_default_context(cafile=str(self.runtime / "tls/ca.crt"))
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                urllib.request.HTTPSHandler(context=self.context))
        initial_health = self.health(first)
        self.checks.append("verified HTTPS identity and build")
        page = self.get("/?workspace=demo&page=budget")
        require(b"WHAB" in page and b"Demo budget" in page and b"<h1>Budget</h1>" in page
                and first["version"].encode() in page, "The rendered demo budget or visible application version is missing.")
        self.checks.append("rendered demo budget and version")
        payload = json.dumps({"workspace": "personal", "name": "Fictional launcher wallet",
                              "type": "depository", "balance": 12345}).encode()
        request = urllib.request.Request(self.origin + "/api/accounts", data=payload,
                                         headers={"Content-Type": "application/json"}, method="POST")
        with self.http.open(request, timeout=15) as response:
            require(response.status == 200, "Creating the fictional manual account failed.")
        self.fictional_account()
        repeated = self.command("start", "--offline")
        require(self.health(repeated)["instance"] == initial_health["instance"], "Repeated Start created another server instance.")
        self.checks.append("idempotent start")
        configuration = (self.runtime / ".env").read_bytes()
        restarted = self.command("restart")
        require(restarted.get("running") is True, "Restart did not start WHAB.")
        require(self.health(restarted)["instance"] != initial_health["instance"], "Restart reused the stopped server identity.")
        self.fictional_account()
        require((self.runtime / ".env").read_bytes() == configuration, "Restart changed existing runtime configuration.")
        self.checks.append("restart preserves ledger and configuration")
        doctor = self.command("doctor")
        require(doctor.get("healthy") is True, "Doctor did not verify HTTPS health.")
        native = subprocess.run([str(self.bundle / "Contents/MacOS/WHABLauncher"), "--diagnose", "--root", str(self.root)],
                                capture_output=True, text=True, timeout=30, env=environment(), check=False)
        native_reply = json.loads(native.stdout)
        require(native.returncode == 0 and native_reply.get("healthy") is True,
                "The native launcher could not diagnose its bundled manager.")
        self.checks.append("manager and native diagnostics")
        require(self.command("stop").get("running") is False, "Stop did not stop WHAB.")
        require(self.command("stop").get("running") is False, "Repeated Stop was not idempotent.")
        self.checks.append("idempotent graceful stop")
        relaunched = self.command("start", "--offline")
        require(relaunched.get("running") is True, "Offline relaunch did not start.")
        self.health(relaunched)
        self.fictional_account()
        require((self.runtime / ".env").read_bytes() == configuration, "Offline relaunch changed runtime configuration.")
        require(self.command("stop").get("running") is False, "Final Stop did not stop WHAB.")
        self.checks.append("offline relaunch preserves ledger and configuration")
        self.permissions()
        self.checks.append("private state and backup permissions")
        self.verify_signature()
        self.checks.append("bundle signature preserved after lifecycle")
        return {"ok": True, "version": first["version"], "build": first["build"], "checks": self.checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path, help="Complete locally signed WHAB.app bundle.")
    parser.add_argument("--keep-test-dir", action="store_true", help="Keep disposable data and private logs for diagnosis.")
    args = parser.parse_args()
    bundle = args.bundle.expanduser().resolve()
    require(bundle.suffix == ".app" and bundle.is_dir(), "--bundle must name an existing WHAB.app directory.")
    # Python 3.14 is the project's supported development runtime. delete=False
    # permits retaining diagnostics if an owned process cannot stop gracefully.
    temporary = tempfile.TemporaryDirectory(prefix="whab-bundle-check-", delete=False)
    directory = Path(temporary.name)
    check = BundleCheck(bundle, directory)
    stopped = False
    failure = None
    summary = None
    try:
        summary = check.run()
    except (CheckFailure, OSError, ValueError, KeyError, subprocess.SubprocessError, urllib.error.URLError) as error:
        failure = str(error)
    finally:
        try:
            stopped = check.command("stop", timeout=60).get("running") is False
        except (CheckFailure, OSError, ValueError, subprocess.SubprocessError):
            failure = failure or "The disposable server could not be stopped gracefully."
        if stopped and not args.keep_test_dir:
            temporary.cleanup()
        else:
            print(f"Disposable test directory retained: {directory}", file=sys.stderr)
    if failure:
        raise CheckFailure(failure)
    print(json.dumps(summary))


if __name__ == "__main__":
    try:
        main()
    except (CheckFailure, OSError) as error:
        sys.exit(f"Bundle check failed: {error}")
