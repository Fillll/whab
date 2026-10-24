"""Approved local wheels in immutable, versioned optional-worker environments.

This contains installation failures; installed Python remains trusted code, not
a security sandbox. The host never installs into a published environment.
"""

from __future__ import annotations

import base64
import configparser
import csv
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from email.parser import BytesParser
from pathlib import Path
from uuid import uuid4

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import parse_tag, sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from whab import __version__

from .report_worker import ReportWorker, _stop_process_group
from .sdk import API_VERSION, BudgetSnapshot, valid_plugin_name

MAX_WHEEL_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 300 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_MEMBERS = 20000
MAX_WHEELS = 128
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ENTRY = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z")
_OTHER_ENTRY = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)?(?:\s*\[[A-Za-z0-9_, -]+\])?\Z")


class EnvironmentPreparationError(ValueError):
    """Authored diagnostics exclude paths, subprocess output and plugin payloads."""

    def __init__(self, code, plugin_id=None):
        messages = {
            "invalid_wheel": "An approved wheel has invalid, unsupported or unsafe metadata.",
            "wheel_hash_mismatch": "A local wheel changed or does not match its approved SHA-256 hash.",
            "incompatible_python": "An approved wheel does not support this Python interpreter.",
            "invalid_closure": "Supply one approved local wheel for every required distribution and only the selected plugin.",
            "incompatible_host": "The worker environment must use the currently running WHAB host version.",
            "environment_timeout": "Plugin environment preparation exceeded its bounded deadline.",
            "environment_failed": "The candidate plugin environment failed installation or readiness checks. Existing environments are unchanged.",
            "environment_changed": "A prepared environment no longer matches its approved artifacts. Preserve it and prepare a new version.",
            "environment_cleanup_failed": "An incomplete candidate environment could not be cleaned up. Existing prepared environments are unchanged.",
            "unsupported_platform": "Versioned worker environments currently require macOS or Linux file locking.",
            "unsupported_lifecycle": "Only explicitly live plugins with supported versioned capability adapters can be prepared for live upgrades.",
        }
        super().__init__(messages[code])
        self.code, self.plugin_id = code, plugin_id


@dataclass(frozen=True, slots=True)
class ApprovedWheel:
    path: Path = field(repr=False)
    sha256: str

    def __post_init__(self):
        object.__setattr__(self, "path", Path(self.path))
        if type(self.sha256) is not str or _HASH.fullmatch(self.sha256) is None:
            raise ValueError("Approve a local wheel using its complete lowercase SHA-256 hash.")


@dataclass(frozen=True, slots=True)
class WheelMetadata:
    filename: str
    sha256: str
    distribution: str
    canonical_name: str
    version: str
    requires_python: str
    requirements: tuple[str, ...]
    entry_points: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class PreparedEnvironment:
    identifier: str
    plugin_id: str
    distribution: str
    version: str
    python: Path = field(repr=False)
    path: Path = field(repr=False)
    wheels: tuple[WheelMetadata, ...]


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _member_name(name):
    if type(name) is not str or not 1 <= len(name) <= 1024 or "\\" in name or ":" in name or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError()
    parts = name.rstrip("/").split("/")
    if len(parts) > 32 or any(part in {"", ".", ".."} for part in parts):
        raise ValueError()
    name.encode("utf-8")


def _single(metadata, name, *, required=True):
    values = metadata.get_all(name, [])
    if len(values) != (1 if required else min(1, len(values))):
        raise ValueError()
    value = values[0] if values else ""
    if len(value) > 2000 or "\x00" in value or any(ord(char) < 32 for char in value):
        raise ValueError()
    return value


def inspect_wheel(approval: ApprovedWheel) -> WheelMetadata:
    """Read approved bounded wheel metadata without importing or installing code."""
    if type(approval) is not ApprovedWheel:
        raise ValueError("Static wheel inspection requires an ApprovedWheel.")
    path = approval.path
    try:
        if path.suffix != ".whl" or path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_WHEEL_BYTES:
            raise ValueError()
        if _sha256(path) != approval.sha256:
            raise EnvironmentPreparationError("wheel_hash_mismatch")
        filename_name, filename_version, _, tags = parse_wheel_filename(path.name)
        if not tags & set(sys_tags()):
            raise EnvironmentPreparationError("incompatible_python")
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = set()
            portable_names = set()
            if not 1 <= len(entries) <= MAX_MEMBERS or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise ValueError()
            for entry in entries:
                _member_name(entry.filename)
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if entry.filename.casefold() in portable_names or entry.orig_filename != entry.filename or entry.flag_bits & 1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED} or mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ValueError()
                names.add(entry.filename)
                portable_names.add(entry.filename.casefold())
                if ".data/" in entry.filename and not any(f".data/{kind}/" in entry.filename for kind in ("purelib", "platlib")):
                    raise ValueError()
            metadata_name, = (name for name in names if name.endswith(".dist-info/METADATA"))
            prefix = metadata_name.removesuffix("METADATA")
            distribution_prefix, version_prefix = prefix.removesuffix(".dist-info/").rsplit("-", 1)
            if canonicalize_name(distribution_prefix) != filename_name or Version(version_prefix) != filename_version:
                raise ValueError()
            if any(".dist-info/" in name and not name.startswith(prefix) for name in names):
                raise ValueError()
            for name in (metadata_name, prefix + "WHEEL", prefix + "RECORD"):
                if archive.getinfo(name).file_size > MAX_METADATA_BYTES:
                    raise ValueError()
            metadata = BytesParser().parsebytes(archive.read(metadata_name))
            distribution, version = _single(metadata, "Name"), _single(metadata, "Version")
            if canonicalize_name(distribution) != filename_name or Version(version) != filename_version:
                raise ValueError()
            python = _single(metadata, "Requires-Python", required=False)
            if python and not SpecifierSet(python).contains(platform.python_version(), prereleases=True):
                raise EnvironmentPreparationError("incompatible_python")
            requirements = tuple(metadata.get_all("Requires-Dist", []))
            if len(requirements) > 1000:
                raise ValueError()
            for value in requirements:
                if len(value) > 2000 or Requirement(value).url:
                    raise ValueError()
            wheel = BytesParser().parsebytes(archive.read(prefix + "WHEEL"))
            if _single(wheel, "Wheel-Version") != "1.0":
                raise ValueError()
            if _single(wheel, "Root-Is-Purelib") not in {"true", "false"} or set().union(*(parse_tag(value) for value in wheel.get_all("Tag", []))) != tags:
                raise ValueError()
            records = {}
            for row in csv.reader(io.StringIO(archive.read(prefix + "RECORD").decode("utf-8"))):
                if len(row) != 3 or row[0] in records:
                    raise ValueError()
                records[row[0]] = row[1:]
            files = {entry.filename for entry in entries if not entry.is_dir()}
            if set(records) != files:
                raise ValueError()
            for name, (digest, size) in records.items():
                if name == prefix + "RECORD":
                    if digest or size:
                        raise ValueError()
                    continue
                if not digest.startswith("sha256=") or not size.isdecimal() or int(size) != archive.getinfo(name).file_size:
                    raise ValueError()
                actual = base64.urlsafe_b64encode(hashlib.sha256(archive.read(name)).digest()).rstrip(b"=").decode("ascii")
                if digest != "sha256=" + actual:
                    raise ValueError()
            entry_points = []
            entrypoint_file = prefix + "entry_points.txt"
            if entrypoint_file in names:
                if archive.getinfo(entrypoint_file).file_size > MAX_METADATA_BYTES:
                    raise ValueError()
                configuration = configparser.ConfigParser(interpolation=None, strict=True)
                configuration.optionxform = str
                configuration.read_string(archive.read(entrypoint_file).decode("utf-8"))
                for group in configuration.sections():
                    for name, target in configuration[group].items():
                        pattern = _ENTRY if group == "whab.plugins" else _OTHER_ENTRY
                        if len(group) > 100 or len(name) > 200 or len(target) > 500 or pattern.fullmatch(target) is None:
                            raise ValueError()
                        if group == "whab.plugins" and not valid_plugin_name(name):
                            raise ValueError()
                        entry_points.append((group, name, target))
            return WheelMetadata(path.name, approval.sha256, distribution, str(filename_name), str(filename_version), python,
                                 requirements, tuple(sorted(entry_points)))
    except EnvironmentPreparationError:
        raise
    except Exception:
        raise EnvironmentPreparationError("invalid_wheel") from None


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(path, value):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as target:
            target.write(_json(value))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _installation_hash(path, *, durable=False):
    """Detect changed, missing and unexpected files before executing a reused env."""
    records = []
    directories = []
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path).as_posix()
        if relative == "ready.json":
            continue
        if item.is_symlink():
            records.append((relative, "link", hashlib.sha256(os.readlink(item).encode()).hexdigest()))
        elif item.is_file():
            records.append((relative, "file", _sha256(item)))
            if durable:
                with item.open("rb") as source:
                    os.fsync(source.fileno())
        elif item.is_dir():
            directories.append(item)
            records.append((relative, "directory", ""))
        else:
            raise EnvironmentPreparationError("environment_changed")
    if durable:
        for directory in reversed(directories):
            _sync_directory(directory)
        _sync_directory(path)
    return hashlib.sha256(_json(records)).hexdigest()


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EnvironmentPreparationError("environment_timeout")
    return remaining


@contextmanager
def _lock(path, deadline):
    if os.name != "posix":
        raise EnvironmentPreparationError("unsupported_platform")
    import fcntl

    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.05, _remaining(deadline)))
        yield
    finally:
        os.close(descriptor)


@contextmanager
def _candidate_cleanup(created, plugin_id, probes=()):
    # This context exits before the preparation lock, so another preparer can
    # never observe or replace a directory while failure cleanup removes it.
    try:
        yield
    except BaseException as error:
        if any(probe.cleanup_pending for probe in probes):
            # A retained child may still execute these files. Its owner must
            # finish cleanup before another preparation can replace them.
            created.clear()
            if not isinstance(error, Exception):
                raise
            raise EnvironmentPreparationError("environment_cleanup_failed", plugin_id) from None
        if created:
            try:
                shutil.rmtree(created.pop())
            except OSError:
                raise EnvironmentPreparationError("environment_cleanup_failed", plugin_id) from None
        raise


def _environment():
    password = os.environ.get("WHAB_BUDGET_PASSWORD_ENV")
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("WHAB_", "PLAID_", "SERVER_", "CLIENT_", "PYTHON", "PIP_"))
            and key not in {password, "TOKEN_ENCRYPTION_KEY", "ALLOW_LIVE_PLAID", "VIRTUAL_ENV"}}


def _run(command, deadline):
    remaining = _remaining(deadline)
    process = subprocess.Popen(command, env=_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        status = process.wait(timeout=remaining)
        if status:
            raise subprocess.CalledProcessError(status, command)
    except BaseException as error:
        try:
            _stop_process_group(process)
        except (OSError, subprocess.TimeoutExpired):
            if isinstance(error, Exception):
                raise EnvironmentPreparationError("environment_cleanup_failed") from None
        raise
    else:
        try:
            _stop_process_group(process)
        except (OSError, subprocess.TimeoutExpired):
            raise EnvironmentPreparationError("environment_cleanup_failed") from None


_VERIFY = """
import hashlib, importlib.metadata as metadata, json, pathlib, re, sys, zipfile
root = pathlib.Path(sys.argv[1]).resolve()
expected = json.loads(pathlib.Path(sys.argv[2]).read_text())
installed = {re.sub('[-_.]+','-',item.metadata['Name']).lower(): item for item in metadata.distributions()}
assert set(installed) - {'pip'} == {item['canonical_name'] for item in expected}
for item in expected:
    distribution = installed[item['canonical_name']]
    assert distribution.version == item['version']
    assert pathlib.Path(distribution.locate_file('')).resolve().is_relative_to(root)
    with zipfile.ZipFile(root / 'wheels' / item['filename']) as wheel:
        for info in wheel.infolist():
            if info.is_dir() or info.filename.endswith('.dist-info/RECORD'):
                continue
            relative = info.filename
            if '.data/' in relative:
                _, scheme, relative = relative.split('/',2)
                assert scheme in {'purelib','platlib'}
            path = pathlib.Path(distribution.locate_file(relative)).resolve()
            assert path.is_relative_to(root)
            assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(wheel.read(info)).digest()
assert not any(pathlib.Path(entry).resolve().is_relative_to(pathlib.Path(sys.argv[3]).resolve()) for entry in sys.path)
"""


def _verify(path, python, manifest, deadline):
    expected = path / "approved.json"
    if expected.read_bytes() != _json(manifest["wheels"]):
        raise EnvironmentPreparationError("environment_changed")
    for record in manifest["wheels"]:
        inspected = inspect_wheel(ApprovedWheel(path / "wheels" / record["filename"], record["sha256"]))
        if json.loads(_json(asdict(inspected))) != record:
            raise EnvironmentPreparationError("environment_changed")
    _run([str(python), "-I", "-B", "-m", "pip", "check"], deadline)
    _run([str(python), "-I", "-B", "-c", _VERIFY, str(path), str(expected), str(Path(__file__).resolve().parents[2])], deadline)


def prepare_environment(root, plugin_id, wheels: tuple[ApprovedWheel, ...], *, timeout=120, family="optional",
                        worker_owner=None, worker_retired=None) -> PreparedEnvironment:
    """Prepare one approved optional-plugin closure; publication grants no activation.

    A venv is created at its final content address because Python environments
    cannot safely be relocated. The lock and durable preparation receipt fence
    that incomplete directory; only ``ready.json`` publishes a usable result.
    This adapter uses the current Python/host, one selected optional plugin, and
    wheel 1.0 purelib/platlib layouts. Dependency extras and other wheel data
    schemes require an expanded installer contract and are rejected explicitly.
    """
    if not valid_plugin_name(plugin_id) or type(wheels) is not tuple or not 1 <= len(wheels) <= MAX_WHEELS or any(type(wheel) is not ApprovedWheel for wheel in wheels):
        raise ValueError("Choose a plugin ID and a bounded tuple of approved local wheels.")
    if type(timeout) not in {int, float} or not 0 < timeout <= 600:
        raise ValueError("Environment preparation requires a timeout within 600 seconds.")
    if family not in {"optional", "storage"}:
        raise ValueError("Choose a supported worker protocol family.")
    deadline = time.monotonic() + timeout
    created = []
    probes = []

    def own_probe(worker):
        probes.append(worker)
        if worker_owner is not None:
            worker_owner(worker)
    try:
        root = Path(root).resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        locks = root / "locks"
        locks.mkdir(mode=0o700, exist_ok=True)
        if locks.is_symlink():
            raise EnvironmentPreparationError("environment_changed", plugin_id)
        # Copies are private and hash checked after copying, closing the source
        # approval/install TOCTOU gap without changing the approved input files.
        with tempfile.TemporaryDirectory(prefix="inputs-", dir=root) as staging:
            staging = Path(staging)
            records = []
            for approval in wheels:
                target = staging / approval.path.name
                if target.exists():
                    raise EnvironmentPreparationError("invalid_closure", plugin_id)
                if approval.path.is_symlink():
                    raise EnvironmentPreparationError("invalid_wheel", plugin_id)
                if not approval.path.is_file() or approval.path.stat().st_size > MAX_WHEEL_BYTES:
                    raise EnvironmentPreparationError("invalid_wheel", plugin_id)
                shutil.copyfile(approval.path, target)
                records.append(inspect_wheel(ApprovedWheel(target, approval.sha256)))
                _remaining(deadline)
            records.sort(key=lambda item: item.canonical_name)
            by_name = {record.canonical_name: record for record in records}
            if len(by_name) != len(records) or "pip" in by_name:
                raise EnvironmentPreparationError("invalid_closure", plugin_id)
            entries = [(record, name) for record in records for group, name, _ in record.entry_points if group == "whab.plugins"]
            if len(entries) != 1 or entries[0][1] != plugin_id:
                raise EnvironmentPreparationError("invalid_closure", plugin_id)
            plugin = entries[0][0]
            if "whab" not in by_name or by_name["whab"].version != __version__:
                raise EnvironmentPreparationError("incompatible_host", plugin_id)
            for record in records:
                for value in record.requirements:
                    requirement = Requirement(value)
                    if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                        continue
                    required = by_name.get(canonicalize_name(requirement.name))
                    if required is None or not requirement.specifier.contains(required.version, prereleases=True) or requirement.extras:
                        raise EnvironmentPreparationError("invalid_closure", plugin_id)
            manifest = {"format": 1, "plugin": plugin_id, "host_sdk": API_VERSION,
                        "python": {"version": platform.python_version(), "cache_tag": sys.implementation.cache_tag,
                                   "sha256": _sha256(Path(sys.executable))},
                        "wheels": [asdict(record) for record in records]}
            # V1 remains exact for existing optional environments. Storage's
            # distinct private protocol has an explicitly versioned receipt.
            if family == "storage":
                manifest.update(format=2, family="storage")
            manifest = json.loads(_json(manifest))
            if len(_json(manifest)) > MAX_METADATA_BYTES - 256:
                raise EnvironmentPreparationError("invalid_closure", plugin_id)
            identifier = hashlib.sha256(_json(manifest)).hexdigest()
            manifest["identifier"] = identifier
            destination = root / identifier
            python = destination / "environment/bin/python"
            receipt = {"format": 1, "identifier": identifier}
            with _lock(locks / (identifier + ".lock"), deadline), _candidate_cleanup(created, plugin_id, probes):
                if destination.is_symlink():
                    raise EnvironmentPreparationError("environment_changed", plugin_id)
                ready = destination / "ready.json"
                if ready.exists() or ready.is_symlink():
                    if ready.is_symlink() or ready.stat().st_size > MAX_METADATA_BYTES:
                        raise EnvironmentPreparationError("environment_changed", plugin_id)
                    try:
                        raw = ready.read_bytes()
                        saved = json.loads(raw)
                        installation_hash = saved.pop("installation_sha256")
                        if saved != manifest or type(installation_hash) is not str or _HASH.fullmatch(installation_hash) is None:
                            raise ValueError()
                        if raw != _json({**saved, "installation_sha256": installation_hash}) or _installation_hash(destination) != installation_hash:
                            raise ValueError()
                        _verify(destination, python, manifest, deadline)
                    except Exception:
                        raise EnvironmentPreparationError("environment_changed", plugin_id) from None
                else:
                    if destination.exists():
                        marker = destination / "preparing.json"
                        if not marker.is_file() or marker.read_bytes() != _json(receipt):
                            raise EnvironmentPreparationError("environment_changed", plugin_id)
                        shutil.rmtree(destination)
                    destination.mkdir(mode=0o700)
                    created.append(destination)
                    _publish(destination / "preparing.json", receipt)
                    shutil.copytree(staging, destination / "wheels")
                    _publish(destination / "approved.json", manifest["wheels"])
                    _run([sys.executable, "-I", "-m", "venv", str(destination / "environment")], deadline)
                    _run([str(python), "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                          "--no-index", "--no-deps", *(str(destination / "wheels" / item.filename) for item in records)], deadline)
                    _verify(destination, python, manifest, deadline)
                    if family == "storage":
                        from .storage_worker import StorageWorker
                        probe = StorageWorker(plugin_id, python=python, timeout=min(60, _remaining(deadline)),
                                              expected_distribution=plugin.distribution, expected_version=plugin.version,
                                              on_create=own_probe)
                        # Its strict hello is the data-only engine readiness probe.
                        probe.timeout = max(0.001, min(60, deadline - time.monotonic()))
                        probe.close()
                        if probe.cleanup_pending:
                            raise EnvironmentPreparationError("environment_cleanup_failed", plugin_id)
                        if worker_retired is not None:
                            worker_retired(probe)
                    else:
                        probe = ReportWorker(plugin_id, python=python, timeout=min(60, _remaining(deadline)),
                                             expected_distribution=plugin.distribution, expected_version=plugin.version,
                                             on_create=own_probe)
                        try:
                            if probe.info["host"] != __version__:
                                raise EnvironmentPreparationError("incompatible_host", plugin_id)
                            if not probe.info["live"]:
                                raise EnvironmentPreparationError("unsupported_lifecycle", plugin_id)
                            adapters = probe.info["adapters"]
                            if adapters["interface"] is not None:
                                probe.interface()
                            if adapters["remote_client"] is not None:
                                probe.remote_client()
                            if adapters["ui"] is not None:
                                from .ui_worker import readiness_arguments

                                probe.visual_resources()
                                for page in ("budget", "transactions", "accounts", "insights"):
                                    probe.timeout = min(60, _remaining(deadline))
                                    probe.render_ui(readiness_arguments(page))
                            if adapters["bank_logos"] is not None:
                                if adapters["bank_logos"]["cache"]:
                                    probe.configure_cache(destination / "probe-cache")
                                probe.resolve("Unknown fictional institution")
                            if adapters["settings"] is not None:
                                probe.settings()
                            if adapters["file_import"] is not None:
                                resources = probe.resources()
                                if (resources is not None) != adapters["file_import"]["ui"]:
                                    raise EnvironmentPreparationError("environment_failed", plugin_id)
                            if adapters["categorization"]:
                                from .namespace_effects import NamespaceSnapshot

                                namespace = NamespaceSnapshot({}, {})
                                for workspace in ("personal", "demo"):
                                    engine, _, _ = probe.category({"engine": None, "command": "open", "values": {},
                                        "context": {"budget": "readiness", "workspace": workspace, "settings": {}}}, namespace)
                                    _, index, observed = probe.category({"engine": engine, "command": "index", "values": {}, "context": None}, namespace)
                                    if type(index) is not dict or type(index.get("models")) is not list or observed.digest != namespace.digest:
                                        raise EnvironmentPreparationError("environment_failed", plugin_id)
                                    probe.category({"engine": engine, "command": "close", "values": {}, "context": None}, namespace)
                            snapshot = BudgetSnapshot("personal", "2000-01", (), (), ())
                            if probe.info["insights"]:
                                probe.timeout = min(60, _remaining(deadline))
                                probe.insights(snapshot)
                            if probe.info["export"]:
                                probe.timeout = min(60, _remaining(deadline))
                                probe.export(snapshot)
                        finally:
                            probe.timeout = max(0.001, min(60, deadline - time.monotonic()))
                            probe.close()
                            if probe.cleanup_pending:
                                raise EnvironmentPreparationError("environment_cleanup_failed", plugin_id)
                            if worker_retired is not None:
                                worker_retired(probe)
                    if (destination / "probe-cache").exists():
                        shutil.rmtree(destination / "probe-cache")
                    _remaining(deadline)
                    # Once publication becomes possible, the directory may no
                    # longer be treated as a disposable interrupted install.
                    (destination / "preparing.json").unlink()
                    installation_hash = _installation_hash(destination, durable=True)
                    _remaining(deadline)
                    _publish(ready, {**manifest, "installation_sha256": installation_hash})
                    _sync_directory(root)
                    created.clear()
            return PreparedEnvironment(identifier, plugin_id, plugin.distribution, plugin.version, python, destination, tuple(records))
    except Exception as error:
        if isinstance(error, EnvironmentPreparationError):
            if error.plugin_id is None:
                error.plugin_id = plugin_id
            raise
        code = "environment_timeout" if isinstance(error, subprocess.TimeoutExpired) else "environment_failed"
        raise EnvironmentPreparationError(code, plugin_id) from None
