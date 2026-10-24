"""One scoped host decision and fail-closed remote command outcome metadata.

No credential, endpoint, remote budget identifier or financial body is stored.
The journal is device-private; it is never a portable plugin payload or IPC DTO.
"""

import json
import re
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from packaging.version import Version

from .effect_store import EffectError, EffectStore, digest, encode, invalid
from .remote_client_adapter import read_client_record
from .sdk import valid_plugin_name

_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _hash(value):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        invalid()


def _identifier(value):
    if type(value) is not str or not 1 <= len(value) <= 200 or "\x00" in value:
        invalid()
    value.encode("utf-8")


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        invalid()


def _choice(value):
    if type(value) is not dict or value.keys() != {"plugin", "adapter", "enabled", "worker"}:
        invalid()
    if not valid_plugin_name(value["plugin"]) or type(value["enabled"]) is not bool:
        invalid()
    adapter = read_client_record(value["adapter"])
    prefix = value["plugin"].upper().replace("-", "_") + "_"
    if any(not getattr(adapter, name).startswith(prefix) for name in ("url_setting", "token_setting", "budget_setting")):
        invalid()
    worker = value["worker"]
    if worker is not None:
        if type(worker) is not dict or worker.keys() != {"environment", "distribution", "version"}:
            invalid()
        _hash(worker["environment"])
        if (type(worker["distribution"]) is not str or not 1 <= len(worker["distribution"]) <= 120
                or re.fullmatch(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*", worker["distribution"]) is None
                or type(worker["version"]) is not str or not 1 <= len(worker["version"]) <= 80):
            invalid()
        Version(worker["version"])


def _pending(value, binding):
    if type(value) is not dict or value.keys() != {"request_id", "fingerprint", "expected_revision", "binding"}:
        invalid()
    _uuid(value["request_id"])
    _hash(value["fingerprint"])
    _identifier(value["expected_revision"])
    if value["binding"] != binding:
        invalid()


def _decision(value):
    if type(value) is not dict or value.keys() != {"id", "expected_revision", "choice"}:
        invalid()
    _uuid(value["id"])
    _hash(value["expected_revision"])
    _choice(value["choice"])


def _validate(value):
    if (type(value) is not dict or value.keys() != {"format", "binding", "origin_plugin", "original_source", "choice", "decisions", "pending", "acknowledged"}
            or type(value["format"]) is not int or value["format"] != 1):
        invalid()
    _hash(value["binding"])
    if not valid_plugin_name(value["origin_plugin"]):
        invalid()
    if value["original_source"] is not None:
        _hash(value["original_source"])
    _choice(value["choice"])
    if type(value["decisions"]) is not list or len(value["decisions"]) > 32:
        invalid()
    for decision in value["decisions"]:
        _decision(decision)
    if len({row["id"] for row in value["decisions"]}) != len(value["decisions"]):
        invalid()
    if value["decisions"] and value["decisions"][-1]["choice"] != value["choice"]:
        invalid()
    if value["pending"] is not None:
        _pending(value["pending"], value["binding"])
    if value["acknowledged"] is not None:
        row = value["acknowledged"]
        if type(row) is not dict or row.keys() != {"request", "response_revision", "response_sha256"}:
            invalid()
        _pending(row["request"], value["binding"])
        _identifier(row["response_revision"])
        _hash(row["response_sha256"])


def command_fingerprint(body):
    """Match the public server's canonical validated command and original CAS."""
    raw = json.dumps([body["operation"], body["workspace"], body["payload"], body["expected_revision"]],
                     sort_keys=True, allow_nan=False).encode("utf-8")
    if len(raw) > 8 * 1024 * 1024:
        invalid()
    return sha256(raw).hexdigest()


class RemoteSelectionStore:
    """Lifetime owner for one budget/environment's remote decision and pending ID."""

    def __init__(self, home, budget_id, environment, initial=None, *, fault=None):
        directory = Path(home) / "remote-client"
        if directory.is_symlink():
            invalid()
        if initial is None and not (directory / "remote-client.json").is_file():
            raise EffectError("effect_unavailable")
        self.store = EffectStore(directory, "remote-client.json",
            {"owner": "remote-client", "budget_id": budget_id, "environment": environment},
            initial, _validate, limit=128 * 1024, fault=fault)

    def snapshot(self):
        return self.store.snapshot()

    @staticmethod
    def revision(value):
        _validate(value)
        # Financial outcomes share this durable owner, but do not reorder or
        # stale an admitted selection change while its active write drains.
        decision = {name: value[name] for name in ("format", "binding", "origin_plugin", "original_source", "choice", "decisions")}
        return digest(encode(decision, 128 * 1024))

    def publish(self, decision, expected_revision):
        _decision(decision)
        _hash(expected_revision)
        with self.store.lock:
            current = self.snapshot()
            previous = next((row for row in current["decisions"] if row["id"] == decision["id"]), None)
            if previous is not None:
                if previous != decision:
                    raise EffectError("effect_conflict")
                return deepcopy(previous)
            if self.revision(current) != expected_revision or decision["expected_revision"] != expected_revision:
                raise EffectError("effect_conflict")
            self.store.commit({**current, "choice": deepcopy(decision["choice"]),
                               "decisions": [*current["decisions"], deepcopy(decision)][-32:]})
            return deepcopy(decision)

    def begin(self, body):
        with self.store.lock:
            current = self.snapshot()
            if current["pending"] is not None:
                raise EffectError("remote_unknown")
            pending = {"request_id": body["request_id"], "fingerprint": command_fingerprint(body),
                       "expected_revision": body["expected_revision"], "binding": current["binding"]}
            _pending(pending, current["binding"])
            self.store.commit({**current, "pending": pending})

    def acknowledge(self, body, revision, result):
        with self.store.lock:
            current = self.snapshot()
            pending = current["pending"]
            if (pending is None or pending["request_id"] != body["request_id"]
                    or pending["fingerprint"] != command_fingerprint(body)):
                raise EffectError("effect_conflict")
            response = encode({"revision": revision, "data": result}, 8 * 1024 * 1024)
            acknowledged = {"request": pending, "response_revision": revision, "response_sha256": digest(response)}
            self.store.commit({**current, "pending": None, "acknowledged": acknowledged})

    def recover(self):
        return self.store.recover()

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
