#!/usr/bin/env python3
"""Private, stdlib-only WHAB installation and process manager (Python >=3.14).

The native app is a client of this JSON CLI. It never imports the managed app,
reads banking state, modifies OS trust, or signals a process by its port.
"""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPOSITORY = "Fillll/whab"
API = f"https://api.github.com/repos/{REPOSITORY}"
REQUIRED_CHECKS = {"CI (Python 3.14)", "CI (Python 3.15)"}
CHANNELS = ("main", "alpha", "stable")
PLUGINS = (
    "interface/bank-logos", "storage/client", "ingestion/plaid", "interface/server",
    "storage/sqlite", "interface/web", "ingestion/ynab",
)
MAX_DOWNLOAD = 128 * 1024 * 1024
MAX_EXTRACTED = 512 * 1024 * 1024
DEFAULT_ROOT = Path.home() / "Library/Application Support/WHAB/launcher"
BACKUP_FORMAT = 2
LOCAL_STORAGE = {"sqlite", "json"}
RUNTIME_NAMES = {".env", "plugins.json", "data", "budgets", "tls", "cache",
                 "plugin-workers", "visual-ui", "remote-client", "storage-authority", "storage-instances"}
SOURCE_PRIVATE = RUNTIME_NAMES | {".venv", ".git", "work", "build", "dist"}
SOURCE_CACHES = {"__pycache__", ".pytest_cache", ".ruff_cache"}


class LauncherError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def absolute(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise LauncherError("invalid_path", "Choose an absolute local directory or executable path.")
    return path.resolve()


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
        raise LauncherError("unsafe_directory", "The managed directory must belong to this user.")
    path.chmod(0o700)


def read_json(path, default=None):
    if not path.exists():
        return default
    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise LauncherError("invalid_state", "A launcher state file is unsafe or too large.")
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise LauncherError("invalid_state", "A launcher state file could not be read.") from None


def write_json(path, data):
    private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".launcher-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(data, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextlib.contextmanager
def operation_lock(root):
    private_directory(root)
    descriptor = os.open(root / "operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LauncherError("busy", "Another WHAB launcher operation is still running.") from None
        yield
    finally:
        os.close(descriptor)


def environment_values(home):
    """Read only launcher-relevant fields; never echo or rewrite the user's env."""
    path = home / ".env"
    if not path.exists():
        if path.is_symlink():
            raise LauncherError("invalid_environment", "The adopted .env symbolic link must target an existing private regular file.")
        return {}
    target = path.resolve()
    if (not target.is_file() or target.stat().st_size > 1024 * 1024
            or target.stat().st_uid != os.getuid()
            or target.stat().st_mode & 0o077):
        raise LauncherError("invalid_environment", "Runtime .env must point to a private regular file owned by this user (chmod 600).")
    values = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not match:
            continue
        key, value = match.groups()
        if key not in {"PORT", "WHAB_TLS_CERT_FILE", "WHAB_TLS_KEY_FILE", "WHAB_BUDGET", "WHAB_BUDGET_COPIES", "WHAB_STORAGE"}:
            continue
        if value[:1] in {"'", '"'}:
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0:
                raise LauncherError("invalid_environment", "A launcher-relevant runtime setting is malformed.")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if "$" in value or "\\" in value:
            raise LauncherError("unsupported_environment", "Use literal paths and port values in launcher-relevant runtime settings.")
        values[key] = value
    return values


def request_json(url):
    if not url.startswith(API + "/"):
        raise LauncherError("invalid_source", "Only the official WHAB repository is supported.")
    request = urllib.request.Request(url, headers={
        "User-Agent": "WHAB-Launcher/1", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError()
        return json.loads(raw)
    except (OSError, ValueError, urllib.error.URLError):
        raise LauncherError("update_unavailable", "Could not verify GitHub updates. Your installed version is preserved.") from None


def commit_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise LauncherError("invalid_source", "GitHub did not return an exact source revision.")
    return value


def successful_checks(sha):
    result = request_json(f"{API}/commits/{commit_sha(sha)}/check-runs?per_page=100")
    latest = {}
    for check in result.get("check_runs", []):
        if (check.get("name") in REQUIRED_CHECKS and check.get("head_sha") == sha
                and check.get("app", {}).get("slug") == "github-actions"):
            old = latest.get(check["name"])
            if old is None or check.get("id", 0) > old.get("id", 0):
                latest[check["name"]] = check
    return REQUIRED_CHECKS <= latest.keys() and all(
        latest[name].get("status") == "completed"
        and latest[name].get("conclusion") == "success" for name in REQUIRED_CHECKS
    )


def latest_target(channel):
    if channel not in CHANNELS:
        raise LauncherError("invalid_channel", "Choose a supported WHAB update channel.")
    if channel == "alpha":
        head = request_json(f"{API}/commits/main")
        sha = commit_sha(head.get("sha") if isinstance(head, dict) else None)
        return {"build": sha, "version": sha[:12], "channel": channel, "tested": False}
    if channel == "main":
        history = request_json(f"{API}/commits?sha=main&per_page=10")
        if not isinstance(history, list):
            raise LauncherError("invalid_source", "GitHub returned an invalid commit history.")
        for entry in history:
            sha = commit_sha(entry.get("sha"))
            if successful_checks(sha):
                return {"build": sha, "version": sha[:12], "channel": channel, "tested": True}
        raise LauncherError("ci_not_ready", "No recent main revision has passed both required Python CI checks.")
    release = request_json(f"{API}/releases/latest")
    tag = release.get("tag_name", "")
    if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise LauncherError("invalid_release", "No supported stable WHAB release is available.")
    reference = request_json(f"{API}/git/ref/tags/{tag}").get("object", {})
    if reference.get("type") == "tag":
        reference = request_json(f"{API}/git/tags/{commit_sha(reference.get('sha'))}").get("object", {})
    if reference.get("type") != "commit":
        raise LauncherError("invalid_release", "The stable release does not identify a source commit.")
    sha = commit_sha(reference.get("sha"))
    if not successful_checks(sha):
        raise LauncherError("ci_not_ready", "The stable release has not passed both required Python CI checks.")
    return {"build": sha, "version": tag.removeprefix("v"), "channel": channel, "tested": True}


def extract_archive(archive, destination):
    """Bounded regular-file extraction; no links, device files, or traversal."""
    private_directory(destination)
    with tarfile.open(archive, "r|gz") as source:
        top = None
        count = total = 0
        for member in source:
            count += 1
            total += member.size
            if count > 30000 or total > MAX_EXTRACTED or member.size < 0:
                raise LauncherError("unsafe_archive", "The source archive exceeds the supported size.")
            parts = Path(member.name).parts
            if not parts or Path(member.name).is_absolute() or ".." in parts:
                raise LauncherError("unsafe_archive", "The source archive contains an unsafe path.")
            if top is None:
                top = parts[0]
            if parts[0] != top or not (member.isfile() or member.isdir()):
                raise LauncherError("unsafe_archive", "The source archive contains unsupported entries.")
            if len(parts) == 1:
                if not member.isdir():
                    raise LauncherError("unsafe_archive", "The source archive has no root directory.")
                continue
            target = destination.joinpath(*parts[1:])
            if member.isdir():
                private_directory(target)
            else:
                private_directory(target.parent)
                with source.extractfile(member) as incoming:
                    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(descriptor, "wb") as output:
                        shutil.copyfileobj(incoming, output)


def download_source(sha, destination):
    url = f"https://codeload.github.com/{REPOSITORY}/tar.gz/{commit_sha(sha)}"
    archive = destination.parent / "source.tar.gz"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "WHAB-Launcher/1"}), timeout=30) as response:
            descriptor = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > MAX_DOWNLOAD:
                        raise LauncherError("unsafe_archive", "The source download exceeds the supported size.")
                    output.write(block)
        extract_archive(archive, destination)
    except (OSError, tarfile.TarError, urllib.error.URLError):
        raise LauncherError("download_failed", "Could not download the selected WHAB source. The current installation is preserved.") from None
    finally:
        archive.unlink(missing_ok=True)


def app_directory(source):
    for candidate in (source / "whab/app", source / "whab", source):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src/whab/__init__.py").is_file():
            return candidate
    raise LauncherError("unsupported_source", "This source revision has no supported WHAB application layout.")


def source_version(app):
    text = (app / "src/whab/__init__.py").read_text()
    match = re.search(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', text, re.MULTILINE)
    if not match:
        raise LauncherError("unsupported_source", "The application version could not be verified.")
    return match[1]


def source_files(app):
    for directory, directories, files in os.walk(app):
        root = Path(directory)
        ignored = SOURCE_CACHES | (SOURCE_PRIVATE if root == app else set())
        if root == app:
            ignored |= {name for name in (*directories, *files) if name.startswith((".whab-", ".plugins-"))}
        directories[:] = sorted(name for name in directories if name not in ignored)
        for name in directories:
            if (root / name).is_symlink():
                raise LauncherError("unsafe_source", "The bundled application source must contain regular files, not symbolic links.")
        for name in sorted(files):
            if name in ignored:
                continue
            path = root / name
            if path.is_symlink() or not path.is_file():
                raise LauncherError("unsafe_source", "The bundled application source must contain regular files, not symbolic links.")
            yield path


def copy_seed_source(seed, destination):
    app = app_directory(seed)
    for _path in source_files(app):
        pass  # validate the entire selected source before copying
    def ignore_private(directory, names):
        ignored = set(names) & SOURCE_CACHES
        if Path(directory) == app:
            ignored |= {name for name in names if name in SOURCE_PRIVATE or name.startswith((".whab-", ".plugins-"))}
        return ignored
    shutil.copytree(app, destination / "whab/app", symlinks=False, ignore=ignore_private)


def process_identity(pid):
    if type(pid) is not int or pid < 2:
        return None
    try:
        # Kernel-provided creation time guards against PID reuse; argv is an
        # additional ownership check, never sufficient by itself.
        started = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart="],
                                 capture_output=True, text=True, check=True).stdout.strip()
        command = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True, check=True).stdout.strip()
        if not started or not command:
            return None
        return {"started": started, "command": command}
    except (OSError, subprocess.CalledProcessError):
        return None


def owned_process(record):
    if not record or not record.get("identity") or not record.get("instance"):
        return False
    identity = process_identity(record.get("pid"))
    return identity == record["identity"] and f"--instance {record['instance']}" in identity["command"]


def clean_environment(home, port, build, instance, smoke=False):
    environment = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG") if key in os.environ}
    environment.update({"WHAB_HOME": str(home), "PORT": str(port),
                        "WHAB_BUILD_COMMIT": build, "WHAB_INSTANCE_ID": instance,
                        "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1"})
    if smoke:
        environment.update({"WHAB_ENV": "sandbox", "PLAID_ENV": "sandbox", "ALLOW_LIVE_PLAID": "false", "WHAB_PLUGINS": "", "WHAB_UI": "web", "WHAB_STORAGE": "sqlite"})
    return environment


def runtime_references(home):
    values = environment_values(home)
    references = []
    for key in ("WHAB_TLS_CERT_FILE", "WHAB_TLS_KEY_FILE", "WHAB_BUDGET"):
        if values.get(key):
            path = Path(values[key]).expanduser()
            references.append(path if path.is_absolute() else home / path)
    references.extend(Path(value).expanduser() for value in values.get("WHAB_BUDGET_COPIES", "").split(os.pathsep) if value)
    if values.get("WHAB_STORAGE", "sqlite") not in LOCAL_STORAGE:
        raise LauncherError("external_runtime", "This runtime uses external storage. Back up that storage and update it manually.")
    budgets = home / "budgets"
    if budgets.is_symlink():
        raise LauncherError("external_runtime", "Runtime budget directories must not be symbolic links.")
    scopes = []
    for budget in sorted(budgets.iterdir()) if budgets.is_dir() else ():
        if budget.is_symlink():
            raise LauncherError("external_runtime", "Runtime budget directories must not be symbolic links.")
        if budget.is_dir():
            for environment in ("sandbox", "production"):
                scope = budget / environment
                if scope.is_symlink():
                    raise LauncherError("external_runtime", "Runtime budget directories must not be symbolic links.")
                if scope.is_dir():
                    scopes.append(scope)
    # Only host-owned records describe budget references. Package resources
    # inside a worker environment can also be named manifest.json/copies.json.
    for paths in (budgets.glob("index-*.json"), (scope / "copies.json" for scope in scopes)):
        for path in paths:
            if not path.exists() and not path.is_symlink():
                continue
            entries = read_json(path, [])
            if not isinstance(entries, list) or any(not isinstance(entry, dict) or not isinstance(entry.get("path"), str) for entry in entries):
                raise LauncherError("invalid_runtime", "A saved budget location record could not be validated for backup.")
            references.extend(Path(entry["path"]).expanduser() for entry in entries)
    for scope in scopes:
        path = scope / "manifest.json"
        if not path.exists() and not path.is_symlink():
            continue
        manifest = read_json(path, {})
        if (not isinstance(manifest, dict) or type(manifest.get("storage", "sqlite")) is not str
                or manifest.get("storage", "sqlite") not in LOCAL_STORAGE):
            raise LauncherError("external_runtime", "A budget uses external storage. Back up that storage and update it manually.")
    if any(not path.is_absolute() or not path.resolve().is_relative_to(home.resolve()) for path in references):
        raise LauncherError("external_runtime", "This runtime references budgets, copies, or TLS files outside its home. Move those files and settings into the runtime home, or make a complete backup and update manually.")
    return [path.parent.resolve() / path.name for path in references]



def _code_link_record(home, path, target):
    """Describe a standard venv alias without traversing or executing its target.

    Code archives require explicit rebuilding. A ready content-addressed manifest
    identifies the only directories in which interpreter aliases may occur.
    Financial/state paths never receive this exception.
    """
    relative = path.relative_to(home)
    parts = relative.parts
    offset = 0
    if parts[:1] == ("budgets",):
        if len(parts) < 4 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", parts[1]) or parts[2] not in {"sandbox", "production"}:
            raise LauncherError("external_runtime", "A runtime code alias is outside a supported budget scope.")
        offset = 3
    code = parts[offset:]
    if (len(code) not in {5, 6} or code[:2] != ("plugin-workers", "environments")
            or not re.fullmatch(r"[a-f0-9]{64}", code[2]) or code[3] != "environment"):
        raise LauncherError("external_runtime", "Runtime symbolic links require a separate complete backup plan.")
    for parent in path.parents:
        if parent == home:
            break
        if parent.is_symlink():
            raise LauncherError("external_runtime", "Runtime code directories must not be symbolic links.")
    ready = read_json(home.joinpath(*parts[:offset + 3]) / "ready.json", {})
    if (not isinstance(ready, dict) or ready.get("identifier") != code[2]
            or type(ready.get("installation_sha256")) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", ready["installation_sha256"])
            or type(ready.get("format")) is not int or ready["format"] not in {1, 2}
            or (ready["format"] == 1 and "family" in ready)
            or (ready["format"] == 2 and ready.get("family") != "storage")):
        raise LauncherError("invalid_runtime", "An immutable worker environment has no valid ready manifest. Preserve it and finish its stopped recovery before updating.")
    manifest = {key: value for key, value in ready.items() if key not in {"identifier", "installation_sha256"}}
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(raw).hexdigest() != code[2]:
        raise LauncherError("invalid_runtime", "An immutable worker environment ready manifest does not match its content address.")
    python = r"(?:python(?:3(?:\.(?:1[4-9]|[2-9][0-9]))?)?|𝜋thon)"
    interpreter = len(code) == 6 and code[4] == "bin" and re.fullmatch(python, code[5])
    internal = len(code) == 5 and code[4] == "lib64" and target == "lib"
    if (type(target) is not str or not 1 <= len(target) <= 4096 or "\x00" in target
            or not (internal or (interpreter and re.fullmatch(python, Path(target).name)
                    and (Path(target).is_absolute() or "/" not in target)))):
        raise LauncherError("external_runtime", "Only standard immutable worker interpreter aliases can be archived. Other symbolic links require a separate backup plan.")
    return {"path": relative.as_posix(), "target": target}


def runtime_code_links(home):
    links, size = [], 0
    for path in runtime_files(home):
        if path.is_symlink() and path.name != ".env":
            record = _code_link_record(home, path, os.readlink(path))
            size += len(json.dumps(record).encode()) + 2
            if len(links) >= 4096 or size > 1024 * 1024:
                raise LauncherError("invalid_runtime", "Runtime worker code aliases exceed the supported backup metadata limit.")
            links.append(record)
    return links


def runtime_files(home):
    """Runtime-owned paths only, even when explicitly adopting a source home."""
    if not home.exists():
        return
    roots = {path for path in home.iterdir() if path.name in RUNTIME_NAMES or path.name.startswith((".whab-", ".plugins-"))}
    roots.update(path for path in runtime_references(home) if path.exists() or path.is_symlink())
    seen = set()
    for root in sorted(roots):
        if root in seen:
            continue
        seen.add(root)
        if root.name == ".env" and root.is_symlink():
            environment_values(home)  # validate the one supported external link
            yield root
            continue
        if root.is_symlink() or not (root.is_file() or root.is_dir()):
            raise LauncherError("external_runtime", "Keep runtime files inside the runtime home before updating; symbolic links and special files need a separate backup plan.")
        yield root
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path in seen:
                    continue
                seen.add(path)
                if path.is_symlink():
                    _code_link_record(home, path, os.readlink(path))
                elif not (path.is_file() or path.is_dir()):
                    raise LauncherError("external_runtime", "Keep runtime files inside the runtime home before updating; symbolic links and special files need a separate backup plan.")
                yield path



@contextlib.contextmanager
def runtime_backup_locks(home):
    """A stopped child retaining a native/control lease cannot race the copy."""
    descriptors = []
    try:
        for path in runtime_files(home):
            if path.name.endswith(".lock") and not path.is_dir():
                if len(descriptors) >= 4096:
                    raise LauncherError("invalid_runtime", "Runtime lock ownership exceeds the supported backup limit.")
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                descriptors.append(descriptor)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise LauncherError("runtime_busy", "A stopped runtime owner still holds a private state lease. Preserve the runtime and finish its cleanup before updating.") from None
        yield
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _fingerprint(home, paths, archived_links=()):
    digest = hashlib.sha256(b"whab-runtime-backup-v2\x00")
    entries = {path.relative_to(home).as_posix(): path for path in paths
               if path.is_symlink() or path.is_file()}
    if type(archived_links) not in {list, tuple} or len(archived_links) > 4096:
        raise LauncherError("invalid_state", "Invalid archived worker code aliases.")
    archived = {}
    for record in archived_links:
        if type(record) is not dict or record.keys() != {"path", "target"} or type(record["path"]) is not str:
            raise LauncherError("invalid_state", "Invalid archived worker code aliases.")
        path = home / record["path"]
        if (Path(record["path"]).is_absolute() or not path.is_relative_to(home) or ".." in path.relative_to(home).parts
                or record["path"] in entries or record["path"] in archived):
            raise LauncherError("invalid_state", "Invalid archived worker code aliases.")
        canonical = _code_link_record(home, path, record["target"])
        if canonical != record:
            raise LauncherError("invalid_state", "Invalid archived worker code aliases.")
        archived[record["path"]] = canonical
    for relative in sorted(set(entries) | set(archived)):
        path = entries.get(relative)
        record = archived.get(relative)
        if record is None and path.is_symlink() and path.name != ".env":
            record = _code_link_record(home, path, os.readlink(path))
        raw_name = relative.encode()
        digest.update(len(raw_name).to_bytes(8, "big"))
        digest.update(raw_name)
        if record is not None:
            raw = record["target"].encode()
            digest.update(b"link\x00" + len(raw).to_bytes(8, "big") + raw)
        else:
            digest.update(b"file\x00" + path.stat().st_size.to_bytes(8, "big"))
            with path.open("rb") as incoming:
                while block := incoming.read(1024 * 1024):
                    digest.update(block)
    return digest.hexdigest()


def runtime_fingerprint(home, *, archived_links=()):
    return _fingerprint(home, runtime_files(home), archived_links)


def available_port(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
    except OSError:
        raise LauncherError("port_in_use", "The WHAB port is already in use. Stop the existing app through its original terminal or launcher, or configure another port. No process was stopped and the runtime was not opened.") from None


def ensure_backup_scope(home):
    runtime_references(home)
    runtime_fingerprint(home)


class Manager:
    def __init__(self, root, python=None, seed=None, wheelhouse=None):
        self.root = absolute(root)
        self.python_override = absolute(python) if python else None
        self.seed = absolute(seed) if seed else None
        self.wheelhouse = absolute(wheelhouse) if wheelhouse else None

    def config(self):
        config = read_json(self.root / "config.json")
        if config is None:
            raise LauncherError("unconfigured", "Choose a new runtime home or explicitly adopt your existing WHAB runtime first.")
        if config.get("schema") != 1 or config.get("channel") not in CHANNELS:
            raise LauncherError("invalid_configuration", "This launcher configuration is unsupported.")
        absolute(config["runtime_home"])
        if not 1024 <= config.get("port", 0) <= 65535:
            raise LauncherError("invalid_configuration", "Choose a port between 1024 and 65535.")
        return config

    def configure(self, runtime_home=None, channel=None, port=None):
        if self.running_record():
            raise LauncherError("running", "Stop WHAB before changing its runtime home, port, or update channel.")
        old = read_json(self.root / "config.json", {})
        selected_channel = channel if channel is not None else old.get("channel", "main")
        if selected_channel not in CHANNELS:
            raise LauncherError("invalid_channel", "Choose a supported WHAB update channel.")
        home = absolute(runtime_home or old.get("runtime_home") or self.root.parent / "runtime")
        if home == self.root or home.is_relative_to(self.root) or self.root.is_relative_to(home):
            raise LauncherError("invalid_runtime", "The runtime home and managed installation must be separate directories.")
        if home.exists() and (not home.is_dir() or home.stat().st_uid != os.getuid()):
            raise LauncherError("invalid_runtime", "Choose a runtime directory owned by your user.")
        values = environment_values(home)
        try:
            selected_port = int(port if port is not None else values.get("PORT", old.get("port", 5173)))
        except ValueError:
            raise LauncherError("invalid_port", "The runtime PORT must be between 1024 and 65535.") from None
        if not 1024 <= selected_port <= 65535:
            raise LauncherError("invalid_port", "Choose a port between 1024 and 65535.")
        if port is not None and "PORT" in values and selected_port != int(values["PORT"]):
            raise LauncherError("port_conflict", "The selected port differs from the adopted .env PORT. Change that local setting explicitly before configuring.")
        config = {"schema": 1, "runtime_home": str(home), "channel": selected_channel, "port": selected_port}
        if (home / ".env").is_symlink():
            config["environment_target"] = str((home / ".env").resolve())
        selected_python = self.python_override or old.get("python")
        if selected_python:
            config["python"] = str(selected_python)
        self.verify_python(config)
        write_json(self.root / "config.json", config)
        if old.get("channel", "main") != selected_channel:
            (self.root / "update.json").unlink(missing_ok=True)
        return self.status() | {"message": "WHAB runtime configured. Existing runtime settings are preserved."}

    def verify_python(self, config):
        executable = self.python_override or Path(config.get("python", sys.executable))
        try:
            result = subprocess.run([str(executable), "-I", "-B", "-c", "import sys; print(int(sys.version_info >= (3,14)))"], capture_output=True, text=True, timeout=10, check=True)
            if result.stdout.strip() != "1":
                raise ValueError()
        except (OSError, ValueError, subprocess.SubprocessError):
            raise LauncherError("python_unavailable", "WHAB requires a working Python 3.14 or newer. Rebuild the app with its bundled runtime or configure an absolute Python path.") from None
        return str(executable)

    def stable_python(self, config):
        """Own a durable standalone runtime before creating absolute-path venvs.

        A copied or updated .app must not invalidate already-installed versions.
        Framework/system Pythons are left at their explicitly configured path;
        only the known self-contained bin + lib layout is copied.
        """
        selected = self.verify_python(config)
        try:
            result = subprocess.run([selected, "-I", "-B", "-c", "import json,sys; print(json.dumps({'prefix':sys.base_prefix,'minor':f'{sys.version_info.major}.{sys.version_info.minor}','version':sys.version}))"], capture_output=True, text=True, timeout=10, check=True)
            details = json.loads(result.stdout)
            prefix = absolute(details["prefix"])
            minor = details["minor"]
            if not re.fullmatch(r"3\.\d+", minor):
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            raise LauncherError("python_unavailable", "The selected Python runtime could not identify its installation.") from None
        executable = prefix / f"bin/python{minor}"
        library = prefix / f"lib/libpython{minor}.dylib"
        stdlib = prefix / f"lib/python{minor}"
        if not (executable.is_file() and library.is_file() and stdlib.is_dir()):
            return selected
        digest = hashlib.sha256(details["version"].encode())
        for path in (executable, library):
            with path.open("rb") as incoming:
                while block := incoming.read(1024 * 1024):
                    digest.update(block)
        identifier = f"python{minor}-{digest.hexdigest()[:24]}"
        destination = self.root / "runtimes" / identifier
        cached = read_json(destination / "runtime.json")
        if cached:
            stable = str(destination / f"bin/python{minor}")
            write_json(self.root / "config.json", {**config, "python": stable})
            return stable
        private_directory(destination.parent)
        if destination.exists():
            shutil.rmtree(destination)
        private_directory(destination)
        try:
            for tree in (prefix / "bin", prefix / "lib"):
                for path in tree.rglob("*"):
                    if path.is_symlink() and not path.resolve().is_relative_to(prefix):
                        raise LauncherError("unsafe_python", "The standalone Python runtime contains an external symbolic link.")
            private_directory(destination / "bin")
            shutil.copy2(executable, destination / f"bin/python{minor}")
            shutil.copytree(prefix / "lib", destination / "lib", symlinks=False)
            relocated = destination / f"bin/python{minor}"
            result = subprocess.run([str(relocated), "-I", "-B", "-c", "import json,ssl,sys,venv; print(sys.base_prefix)"], capture_output=True, text=True, timeout=15, check=True)
            if Path(result.stdout.strip()).resolve() != destination.resolve():
                raise LauncherError("python_not_relocatable", "The bundled Python runtime is not self-contained after copying; the working installation was preserved.")
            write_json(destination / "runtime.json", {"version": details["version"], "python": str(relocated)})
            write_json(self.root / "config.json", {**config, "python": str(relocated)})
            return str(relocated)
        except (OSError, subprocess.SubprocessError):
            shutil.rmtree(destination, ignore_errors=True)
            raise LauncherError("python_not_relocatable", "The standalone Python runtime could not be copied and verified at its durable location.") from None
        except LauncherError:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def active(self):
        return read_json(self.root / "active.json")

    def running_record(self):
        record = read_json(self.root / "process.json")
        return record if owned_process(record) else None

    def status(self):
        config = read_json(self.root / "config.json", {})
        active = self.active() or {}
        record = self.running_record()
        state = "running" if record else ("stopped" if config else "unconfigured")
        home = Path(config["runtime_home"]) if config else self.root.parent / "runtime"
        update = read_json(self.root / "update.json", {})
        channel = config.get("channel", "main")
        target = update.get("target", {})
        if target.get("channel") != channel:
            target = {}
        warning = update.get("warning") if update.get("channel", channel) == channel else None
        return {"state": state, "status": state, "running": bool(record),
                "url": f"https://localhost:{config.get('port', 5173)}", "version": active.get("version"),
                "build": active.get("build"), "channel": channel,
                "active_channel": active.get("channel"), "tested": bool(active.get("tested")),
                "runtime_home": str(home), "log_file": str(self.root / "logs/whab.log"),
                "ca_certificate": str(home / "tls/ca.crt"),
                "update_available": bool(target and target.get("build") != active.get("build")),
                "latest_version": target.get("version"), "latest_build": target.get("build"), "update_warning": warning}

    def check_update(self):
        config = self.config()
        target = latest_target(config["channel"])
        write_json(self.root / "update.json", {"channel": config["channel"], "target": target, "checked_at": time.time()})
        return self.status()

    def log(self, name):
        private_directory(self.root / "logs")
        path = self.root / "logs" / name
        descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.chmod(path, 0o600)
        return os.fdopen(descriptor, "ab", buffering=0)

    def run(self, args, *, environment=None, cwd=None):
        with self.log("install.log") as log:
            try:
                subprocess.run([str(arg) for arg in args], stdout=log, stderr=log, env=environment, cwd=cwd, timeout=900, check=True)
            except (OSError, subprocess.SubprocessError):
                raise LauncherError("preparation_failed", "The candidate installation could not be prepared. The current app is preserved; see the private installation log.") from None

    def seed_target(self):
        if self.seed is None:
            raise LauncherError("no_installation", "No installed WHAB or bundled source seed is available.")
        app = app_directory(self.seed)
        metadata = read_json(self.seed / "build.json", {})
        build = metadata.get("commit", "development")
        # A source hash keeps local development snapshots distinct without git.
        if not re.fullmatch(r"[0-9a-f]{40}", build):
            digest = hashlib.sha256()
            for path in source_files(app):
                digest.update(str(path.relative_to(app)).encode())
                digest.update(path.read_bytes())
            build = "development-" + digest.hexdigest()[:24]
        return {"build": build, "version": source_version(app), "channel": "seed", "tested": bool(metadata.get("tested")), "seed": True}

    def prepare(self, target):
        config = self.config()
        build = target["build"]
        if not re.fullmatch(r"(?:[0-9a-f]{40}|development-[0-9a-f]{24})", build):
            raise LauncherError("invalid_source", "The installation revision is invalid.")
        directory = self.root / "versions" / build
        manifest = read_json(directory / "installation.json")
        if manifest:
            # The exact source may have first been installed through Alpha and
            # later passed CI. Only this newly resolved target supplies its
            # selected channel and CI provenance; a selector change alone does
            # not promote the active installation to a tested build.
            manifest = {**manifest, "channel": target.get("channel"), "tested": bool(target.get("tested"))}
            write_json(directory / "installation.json", manifest)
            return manifest
        if directory.exists():
            shutil.rmtree(directory)
        private_directory(directory)
        try:
            source = directory / "source"
            if target.get("seed"):
                copy_seed_source(self.seed, source)
            else:
                download_source(build, source)
            app = app_directory(source)
            host_python = self.stable_python(config)
            self.run([host_python, "-I", "-B", "-m", "venv", directory / "venv"])
            python = directory / "venv/bin/python"
            required = [app / "plugins" / name for name in PLUGINS]
            optional = sorted(project.parent for project in app.glob("plugins/*/*/pyproject.toml")
                              if project.parent.parent.name != "example" and project.parent not in required)
            packages = [app, *required, *optional]
            if not all((path / "pyproject.toml").is_file() for path in packages):
                raise LauncherError("unsupported_source", "This revision does not include the seven supported plugin packages.")
            options = ["--no-input", "--disable-pip-version-check"]
            # Bundled wheels apply only to their matching seed; a downloaded
            # revision must install its own host and plugin source together.
            if self.wheelhouse and target.get("seed"):
                options.extend(["--no-index", "--find-links", str(self.wheelhouse)])
            self.run([python, "-I", "-B", "-m", "pip", "install", *options, *packages], cwd=app)
            self.run([python, "-I", "-B", "-m", "pip", "check"], cwd=app)
            manifest = {"build": build, "version": source_version(app), "directory": str(directory), "app": str(app), "python": str(python), "channel": target.get("channel"), "tested": bool(target.get("tested"))}
            self.smoke(manifest)
            write_json(directory / "installation.json", manifest)
            return manifest
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def setup(self, installation, home, port, smoke=False):
        private_directory(home)
        environment_values(home)
        if not (home / ".env").exists():
            environment = clean_environment(home, port, installation["build"], str(uuid.uuid4()), smoke)
            self.run([installation["python"], "-I", "-B", str(Path(installation["app"]) / "scripts/setup.py")], environment=environment, cwd=home)
            # setup.py starts from its packaged defaults. Only this newly
            # created configuration may be aligned with the chosen launch port.
            path = home / ".env"
            text = path.read_text()
            text, count = re.subn(r"^PORT=.*$", f"PORT={port}", text, flags=re.MULTILINE)
            if not count:
                text += f"\nPORT={port}\n"
            path.write_text(text)
            path.chmod(0o600)
        # Existing configuration can predate mandatory HTTPS. Provision only
        # missing default TLS material; the TLS module preserves existing keys
        # and refuses incomplete/custom configurations. Never rewrite .env.
        provision = (
            "import os; from pathlib import Path; from dotenv import dotenv_values; "
            "from whab.tls import prepare_local_tls; "
            "home=Path(os.environ['WHAB_HOME']); v=dotenv_values(home/'.env'); "
            "prepare_local_tls(home,v.get('WHAB_TLS_CERT_FILE'),v.get('WHAB_TLS_KEY_FILE'))"
        )
        self.run([installation["python"], "-I", "-B", "-c", provision],
                 environment=clean_environment(home, port, installation["build"], str(uuid.uuid4()), smoke), cwd=home)

    def ssl_context(self, home):
        values = environment_values(home)
        certificate = Path(values.get("WHAB_TLS_CERT_FILE") or "tls/server.crt").expanduser()
        if not certificate.is_absolute():
            certificate = home / certificate
        # Explicitly trust this runtime's authority; never disable hostname or
        # chain verification and never modify the system trust store.
        authority = home / "tls/ca.crt"
        default_certificate = certificate.resolve() == (home / "tls/server.crt").resolve()
        trust = authority if authority.exists() and default_certificate else certificate
        try:
            context = ssl.create_default_context(cafile=str(trust))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            return context
        except (OSError, ssl.SSLError):
            raise LauncherError("tls_unavailable", "The runtime HTTPS certificate cannot be verified. Preserve its keys and repair the certificate configuration.") from None

    def health(self, record, home):
        url = f"https://localhost:{record['port']}/api/health"
        try:
            with urllib.request.urlopen(url, context=self.ssl_context(home), timeout=1) as response:
                value = json.loads(response.read(8193))
            return (value.get("application") == "whab" and value.get("ready") is True
                    and value.get("instance") == record["instance"] and value.get("build") == record["build"])
        except (OSError, ValueError, urllib.error.URLError, LauncherError):
            return False

    def launch(self, installation, home, port, smoke=False):
        available_port(port)
        self.setup(installation, home, port, smoke)
        instance = str(uuid.uuid4())
        server = Path(__file__).resolve().with_name("server.py")
        with self.log("smoke.log" if smoke else "whab.log") as log:
            process = subprocess.Popen([installation["python"], "-I", "-B", str(server), "--port", str(port), "--instance", instance],
                                       env=clean_environment(home, port, installation["build"], instance, smoke),
                                       cwd=home, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True, close_fds=True)
        identity = None
        for _ in range(25):
            identity = process_identity(process.pid)
            if identity and f"--instance {instance}" in identity["command"]:
                break
            if process.poll() is not None:
                break
            time.sleep(0.02)
        if not identity or f"--instance {instance}" not in identity["command"]:
            # Popen retains the known child handle, so cleanup here does not
            # depend on a discoverable process identity or any port owner.
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                raise LauncherError("shutdown_timeout", "The just-created child did not finish shutting down. No force kill was sent.") from None
            raise LauncherError("ownership_unavailable", "The launcher could not verify its new process identity. The child was stopped and no runtime process record was created.")
        record = {"pid": process.pid, "identity": identity, "instance": instance, "port": port, "build": installation["build"]}
        if not smoke:
            write_json(self.root / "process.json", record)
        for _ in range(100):
            if process.poll() is not None:
                break
            if self.health(record, home):
                return record
            time.sleep(0.15)
        self.terminate(record)
        if not smoke:
            (self.root / "process.json").unlink(missing_ok=True)
        raise LauncherError("startup_failed", "WHAB did not become healthy over verified HTTPS. The private log contains startup details; no other process was stopped.")

    def terminate(self, record):
        if not owned_process(record):
            return
        os.kill(record["pid"], signal.SIGTERM)
        for _ in range(150):
            if not owned_process(record):
                return
            time.sleep(0.1)
        raise LauncherError("shutdown_timeout", "WHAB is still shutting down. No force kill was sent; wait and try Stop again.")

    def smoke(self, installation):
        private_directory(self.root / "smoke")
        with tempfile.TemporaryDirectory(prefix="candidate-", dir=self.root / "smoke") as temporary:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            record = self.launch(installation, Path(temporary), port, smoke=True)
            self.terminate(record)

    def stop(self):
        record = self.running_record()
        if record:
            self.terminate(record)
        (self.root / "process.json").unlink(missing_ok=True)
        return self.status()

    def backup(self, home, previous):
        ensure_backup_scope(home)
        with runtime_backup_locks(home):
            return self._backup_files(home, previous)

    def _backup_files(self, home, previous):
        private_directory(self.root / "backups")
        directory = self.root / "backups" / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
        private_directory(directory)
        try:
            fingerprint = runtime_fingerprint(home)
            code_links = runtime_code_links(home)
            environments = [path.relative_to(home).as_posix() for path in runtime_files(home)
                            if path.name == "environment" and path.is_dir()
                            and path.parent.parent.name == "environments"
                            and path.parent.parent.parent.name == "plugin-workers"
                            and re.fullmatch(r"[a-f0-9]{64}", path.parent.name)]
            if len(environments) > 4096:
                raise LauncherError("invalid_runtime", "Too many immutable worker environments for a private backup.")
            private_directory(directory / "runtime")
            copied = []
            for path in runtime_files(home):
                target = directory / "runtime" / path.relative_to(home)
                if path.is_symlink() and path.name != ".env":
                    continue  # Literal aliases are archived as data, never followed.
                if path.is_dir():
                    private_directory(target)
                else:
                    private_directory(target.parent)
                    shutil.copyfile(path, target, follow_symlinks=True)
                    target.chmod(0o600)
                    copied.append(target)
            if (_fingerprint(directory / "runtime", copied, code_links) != fingerprint
                    or runtime_fingerprint(home) != fingerprint):
                raise LauncherError("invalid_runtime", "Runtime files changed while the private backup was being created. Preserve the stopped runtime and retry.")
            write_json(directory / "backup.json", {"format": BACKUP_FORMAT, "runtime_home": str(home), "environment_target": str((home / '.env').resolve()) if (home / '.env').is_symlink() else None, "build": previous.get("build") if previous else None, "fingerprint": fingerprint, "code_links": code_links, "environment_archives": environments, "requires_environment_rebuild": bool(environments), "created_at": time.time()})
            return {"format": BACKUP_FORMAT, "directory": str(directory), "fingerprint": fingerprint,
                    "requires_environment_rebuild": bool(environments)}
        except (LauncherError, OSError, ValueError, subprocess.SubprocessError):
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def switch(self, candidate):
        self.require_recovered()
        config = self.config()
        home = absolute(config["runtime_home"])
        previous = self.active()
        if previous and previous["build"] == candidate["build"]:
            if not self.running_record():
                self.launch(candidate, home, config["port"])
            write_json(self.root / "active.json", candidate)
            return self.status()
        # An unmanaged process may still own an explicitly adopted runtime.
        # Refuse before copying SQLite or writing any transition journal.
        if not self.running_record():
            available_port(config["port"])
        # Validate backup boundaries before stopping a working application.
        ensure_backup_scope(home)
        was_running = bool(self.running_record())
        self.stop()
        try:
            backup = self.backup(home, previous)
        except Exception:
            if was_running and previous:
                self.launch(previous, home, config["port"])
            raise
        # Journal BEFORE candidate startup: a crash leaves the backup and old
        # code discoverable, without assuming a safe database downgrade.
        journal = {"previous": previous, "candidate": candidate, "backup": backup, "state": "starting"}
        write_json(self.root / "transition.json", journal)
        try:
            self.launch(candidate, home, config["port"])
        except Exception:
            journal["state"] = "failed"
            write_json(self.root / "transition.json", journal)
            raise LauncherError("update_start_failed", "The candidate could not start. The stopped previous installation and full private runtime backup are preserved. Use Doctor for recovery details; no database downgrade or automatic restore was attempted.") from None
        write_json(self.root / "active.json", candidate)
        journal["state"] = "complete"
        write_json(self.root / "transition.json", journal)
        return self.status()

    def start(self, offline=False):
        config = self.config()
        if self.running_record():
            return self.status()
        self.require_recovered()
        available_port(config["port"])
        current = self.active()
        target = None
        warning = None
        if not offline:
            try:
                self.check_update()
                target = read_json(self.root / "update.json")["target"]
            except LauncherError as error:
                warning = str(error)
        if target:
            try:
                candidate = self.prepare(target)
            except LauncherError as error:
                if not current:
                    # Bundled seed is the offline first-install recovery path.
                    warning = str(error)
                else:
                    warning = str(error)
            else:
                return self.switch(candidate)
        if current is None:
            current = self.prepare(self.seed_target())
            result = self.switch(current)
        else:
            config = self.config()
            self.launch(current, absolute(config["runtime_home"]), config["port"])
            result = self.status()
        if warning:
            result["message"] = f"Started the available installation. {warning}"
            write_json(self.root / "update.json", {"channel": config["channel"], "warning": warning, "checked_at": time.time()})
        return result

    def update(self):
        self.require_recovered()
        self.check_update()
        target = read_json(self.root / "update.json")["target"]
        # Installation and smoke checks finish while the old server still runs.
        return self.switch(self.prepare(target))

    def restart(self):
        self.require_recovered()
        current = self.active()
        if not current:
            return self.start()
        self.stop()
        config = self.config()
        self.launch(current, absolute(config["runtime_home"]), config["port"])
        return self.status()

    def require_recovered(self):
        transition = read_json(self.root / "transition.json", {})
        if transition.get("state") in {"starting", "failed"}:
            raise LauncherError("recovery_required", "A previous update did not complete. Use Doctor to review its preserved installation and backup before starting, restarting, or updating.")

    def rollback(self):
        transition = read_json(self.root / "transition.json", {})
        previous = transition.get("previous")
        if not previous or not transition.get("backup"):
            raise LauncherError("no_rollback", "There is no previous installation recorded for rollback.")
        if self.running_record():
            raise LauncherError("running", "Stop WHAB before considering rollback.")
        home = absolute(self.config()["runtime_home"])
        backup = transition["backup"]
        if type(backup) is not dict or backup.get("format") != BACKUP_FORMAT or backup.get("requires_environment_rebuild") is not False:
            raise LauncherError("recovery_required", "This backup cannot prove code-only rollback under the complete runtime contract. Preserve the current home and recover a matching full backup separately; immutable worker environments require explicit reviewed rebuilding.")
        ensure_backup_scope(home)
        if runtime_code_links(home) or runtime_fingerprint(home) != backup["fingerprint"]:
            raise LauncherError("recovery_required", "Runtime files changed after the update. A code-only rollback cannot prove schema compatibility. Preserve the current runtime and manually recover the matching full backup into a separate directory before changing code.")
        write_json(self.root / "active.json", previous)
        transition["state"] = "rolled_back"
        write_json(self.root / "transition.json", transition)
        return self.status() | {"message": "Previous code selected; runtime files were unchanged and were not restored. Start WHAB when ready."}

    def doctor(self):
        status = self.status()
        config = self.config()
        python = self.verify_python(config)
        record = self.running_record()
        status["python"] = python
        status["healthy"] = self.health(record, absolute(config["runtime_home"])) if record else False
        transition = read_json(self.root / "transition.json", {})
        if transition:
            status["transition_state"] = transition.get("state")
            status["backup_directory"] = transition.get("backup", {}).get("directory")
            status["previous_version"] = (transition.get("previous") or {}).get("version")
        status["message"] = "Logs and backups are private. Recovery never overwrites runtime files automatically; preserve the current home and restore a matching full backup into a separate directory. HTTPS trust requires an explicit action in Keychain Access."
        return status


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", default=str(DEFAULT_ROOT))
    result.add_argument("--python")
    result.add_argument("--seed")
    result.add_argument("--wheelhouse")
    commands = result.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--runtime-home")
    configure.add_argument("--channel", choices=CHANNELS)
    configure.add_argument("--port", type=int)
    start = commands.add_parser("start")
    start.add_argument("--offline", action="store_true")
    for name in ("status", "stop", "restart", "update", "check-update", "doctor", "rollback"):
        commands.add_parser(name)
    return result


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        manager = Manager(args.root, args.python, args.seed, args.wheelhouse)
        with operation_lock(manager.root):
            if args.command == "configure":
                value = manager.configure(args.runtime_home, args.channel, args.port)
            elif args.command == "start":
                value = manager.start(args.offline)
            else:
                value = getattr(manager, args.command.replace("-", "_"))()
        print(json.dumps({"ok": True, "command": args.command, **value}))
        return 0
    except LauncherError as error:
        print(json.dumps({"ok": False, "command": args.command, "error": {"code": error.code, "message": str(error)}}))
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        # Deliberately omit exception values: filesystem and subprocess errors
        # can include private settings or paths that should stay in local logs.
        print(json.dumps({"ok": False, "command": args.command, "error": {"code": "local_error", "message": "The operation could not complete. Check private directory permissions and use Doctor for recovery details."}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
