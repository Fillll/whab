"""An opened budget owns its plugin setup; private runtime files stay on this device."""

import copy
import io
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from types import MappingProxyType

from whab.domain.errors import StoreError
from whab.plugins import API_VERSION, BudgetContext
from whab.plugins.sdk import valid_plugin_name

from .container import decode_container, encode_container, validate_file_name
from .credentials import encrypt_credentials
from .document import (
    MAX_BUNDLE_BYTES,
    MAX_MANIFEST_BYTES,
    atomic_write,
    digest,
    encode_bundle,
    new_manifest,
    portable_values,
    validate_manifest,
)


def content_digest(manifest, ledger, credential_blob=None):
    if type(ledger) is dict and all(type(value) is bytes for value in ledger.values()):
        ledger = {name: digest(value) for name, value in sorted(ledger.items())}
    content = [manifest, ledger]
    if credential_blob is not None:
        content.append({"credential": digest(credential_blob)})
    return digest(json.dumps(content, sort_keys=True, separators=(",", ":")).encode())


def container_digest(container):
    return content_digest(container.manifest, container.legacy_ledger if container.legacy_ledger is not None else container.files, container.credential_blob)


def _read_bytes(path, limit=MAX_BUNDLE_BYTES):
    # A maximum-size read preallocates 50 MiB even for a tiny saved file.
    # Read through EOF in bounded chunks, including a byte beyond the limit
    # so growth during the read cannot bypass the size check.
    with Path(path).open("rb") as source, io.BytesIO() as result:
        while chunk := source.read(min(64 * 1024, limit + 1 - result.tell())):
            result.write(chunk)
            if result.tell() > limit:
                raise StoreError("The saved budget record exceeds the supported size.")
        return result.getvalue()


def _read_source(path):
    if not path.exists():
        return None
    try:
        value = json.loads(_read_bytes(path, MAX_MANIFEST_BYTES))
        if (
            type(value) is not dict or "content" not in value
            or value.keys() - {"content", "pending", "restorePending"}
            or ("pending" in value and "restorePending" in value)
        ):
            raise ValueError()
        for key, fingerprint in value.items():
            if key == "content" and fingerprint is None:
                continue
            if (
                type(fingerprint) is not str or len(fingerprint) != 64
                or any(character not in "0123456789abcdef" for character in fingerprint)
            ):
                raise ValueError()
        return value
    except (ValueError, OSError):
        raise StoreError("The budget's local recovery record is unreadable.") from None


class BudgetSession:
    def __init__(self, home, environment, manifest, *, path=None, ledger=None, files=None,
                 credentials=None, credential_blob=None, password=None):
        self.home = Path(home)
        self.environment = environment
        self.manifest = validate_manifest(manifest)
        self.path = Path(path).expanduser().resolve() if path else None
        self.ledger = ledger
        self.files = dict(files or {})
        self.credentials = credentials or {}
        self.credential_blob = credential_blob
        self.password = password
        self.storage_config = {}
        self._has_payload = files is not None
        self.storage_home = self.home / "budgets" / self.id / environment
        self.lock = RLock()
        self.store = None
        self.missing_plugins = []
        self.destinations = []
        self.status = "saved"
        self._file_digest = digest(_read_bytes(self.path)) if self.path and self.path.exists() else None
        self._source = _read_source(self.storage_home / "source.json")
        if (self.ledger is not None or self._has_payload) and self._source is not None:
            incoming = content_digest(self.manifest, self.ledger if self.ledger is not None else self.files, self.credential_blob)
            if incoming not in (self._source["content"], self._source.get("pending")):
                raise StoreError("This budget differs from the copy already opened on this device. Export the recovered budget before opening a different revision.")
            saved_manifest = self.storage_home / "manifest.json"
            if "restorePending" not in self._source and saved_manifest.exists():
                try:
                    recovered = validate_manifest(json.loads(_read_bytes(saved_manifest, MAX_MANIFEST_BYTES)))
                    if recovered["id"] != self.id or recovered["storage"] != self.manifest["storage"]:
                        raise ValueError()
                    self.manifest = recovered
                except (ValueError, OSError):
                    raise StoreError("The budget's saved plugin setup is unreadable.") from None
            saved_files = self.storage_home / "plugin-data.whab"
            if "restorePending" not in self._source and saved_files.exists():
                recovered = decode_container(_read_bytes(saved_files))
                if recovered.manifest["id"] != self.id or recovered.manifest["storage"] != self.manifest["storage"]:
                    raise StoreError("The budget's saved plugin files are unreadable.")
                self.files = recovered.files
        self._load_copies()

    @classmethod
    def open(cls, home, environment, path, password=None):
        try:
            raw = _read_bytes(Path(path).expanduser())
            container = decode_container(raw, password=password)
        except OSError:
            raise StoreError("The budget file could not be opened. Check its location and permissions.") from None
        return cls(home, environment, container.manifest, path=path, ledger=container.legacy_ledger,
                   files=container.files if container.legacy_ledger is None else None,
                   credentials=container.credentials, credential_blob=container.credential_blob, password=password)

    @classmethod
    def create(cls, home, environment, name, plugins=(), storage="sqlite", path=None):
        session = cls(home, environment, new_manifest(name, plugins, storage), path=path)
        # Creation grants no right to replace a file already present at this path,
        # including a file that appeared after the caller checked availability.
        session._file_digest = None
        return session

    @property
    def id(self):
        return self.manifest["id"]

    @property
    def name(self):
        return self.manifest["name"]

    @property
    def selection(self):
        return tuple(p["id"] for p in self.manifest["plugins"] if p["enabled"] and p["id"] != self.manifest["storage"])

    def _load_copies(self):
        path = self.storage_home / "copies.json"
        if not path.exists():
            return
        try:
            data = json.loads(_read_bytes(path, MAX_MANIFEST_BYTES))
            if type(data) is not list or len(data) > 20:
                raise ValueError()
            destinations = []
            paths = set()
            for entry in data:
                if (
                    type(entry) is not dict or not {"name", "path"} <= entry.keys()
                    or entry.keys() - {"name", "path", "content", "pending"}
                    or type(entry["name"]) is not str or not entry["name"].strip()
                    or len(entry["name"]) > 100 or "\x00" in entry["name"]
                    or type(entry["path"]) is not str or len(entry["path"]) > 4096
                    or "\x00" in entry["path"] or not Path(entry["path"]).is_absolute()
                ):
                    raise ValueError()
                target = Path(entry["path"]).resolve()
                if target == self.path or target in paths:
                    raise ValueError()
                for key in ("content", "pending"):
                    fingerprint = entry.get(key)
                    if key == "content" and fingerprint is None:
                        continue
                    if key in entry and (
                        type(fingerprint) is not str or len(fingerprint) != 64
                        or any(character not in "0123456789abcdef" for character in fingerprint)
                    ):
                        raise ValueError()
                paths.add(target)
                destinations.append({**entry, "path": str(target), "content": entry.get("content"), "status": "pending"})
            self.destinations = destinations
        except (ValueError, OSError):
            raise StoreError("Saved budget copy destinations are invalid.") from None

    def bind(self, store):
        """Restore only into a new isolated backend; never replace an existing ledger."""
        self.store = store
        if self.ledger is None and self._has_payload:
            prepare = getattr(store, "prepare_budget_files", None)
            if not callable(prepare):
                raise StoreError("This storage plugin cannot open its saved budget files.")
            existing = self._source is not None and "restorePending" not in self._source
            if not existing:
                incoming = content_digest(self.manifest, self.files, self.credential_blob)
                initial = {"content": incoming, "restorePending": incoming}
                try:
                    atomic_write(self.storage_home / "source.json", json.dumps(initial).encode())
                except OSError:
                    raise StoreError("The budget's recovery record could not be saved; its files were not restored.") from None
                self._source = initial
            prepare("personal", self.files_for(self.manifest["storage"]), existing=existing)
        if self.ledger is not None and (self._source is None or "restorePending" in self._source):
            incoming = content_digest(self.manifest, self.ledger)
            restore = getattr(store, "restore_budget", None)
            # The restore contract detaches external provider balance snapshots.
            expected = copy.deepcopy(self.ledger)
            if callable(restore):
                for account in expected["accounts"]:
                    if account["source"] not in {"manual", "demo"}:
                        account["stale"] = True
            expected_digest = content_digest(None, expected)
            if self._source is None:
                initial = {"content": incoming, "restorePending": expected_digest}
                try:
                    atomic_write(self.storage_home / "source.json", json.dumps(initial).encode())
                except OSError:
                    raise StoreError("The budget's recovery record could not be saved; its ledger was not restored.") from None
                self._source = initial
            export = getattr(store, "export_budget", None)
            current = export("personal") if callable(export) else None
            if content_digest(None, current) != self._source["restorePending"]:
                if not callable(restore):
                    raise StoreError("This storage plugin cannot restore a portable budget, and its ledger differs from this file.")
                restore("personal", self.ledger)
        elif self.ledger is not None:
            export = getattr(store, "export_budget", None)
            current = export("personal") if callable(export) else None
            # A recovery marker can survive deletion of the local backend. Rehydrate
            # a pristine replacement instead of overwriting the portable original.
            if (
                current is not None
                and not any(current[key] for key in ("accounts", "transactions", "assignments"))
                and content_digest(self.manifest, current) not in (self._source["content"], self._source.get("pending"))
            ):
                restore = getattr(store, "restore_budget", None)
                if not callable(restore):
                    raise StoreError("The local ledger is empty and cannot restore this saved budget.")
                restore("personal", self.ledger)
        self.ledger = None
        if self.path is None:
            self.path = self.storage_home / "budget.whab"
        self.checkpoint()

    def plugin_record(self, plugin_id):
        if not valid_plugin_name(plugin_id):
            raise StoreError("Invalid plugin ID.")
        return next((p for p in self.manifest["plugins"] if p["id"] == plugin_id), None)

    def save_selection(self, selection):
        with self.lock:
            if type(selection) not in {tuple, list} or any(not valid_plugin_name(name) for name in selection):
                raise StoreError("Choose valid plugin IDs for this budget.")
            updated = copy.deepcopy(self.manifest)
            current = {p["id"]: p for p in updated["plugins"]}
            for plugin in current.values():
                plugin["enabled"] = plugin["id"] in selection or plugin["id"] == self.manifest["storage"]
            for plugin_id in selection:
                if plugin_id not in current:
                    current[plugin_id] = {"id": plugin_id, "enabled": True, "apiVersion": API_VERSION, "settings": {}, "state": {}}
            updated["plugins"] = list(current.values())
            self._save_manifest(updated)

    def save_plugin_data(self, plugin_id, settings=None, state=None):
        with self.lock:
            if self.plugin_record(plugin_id) is None:
                raise StoreError("This plugin is not part of the budget.")
            updated = copy.deepcopy(self.manifest)
            record = next(p for p in updated["plugins"] if p["id"] == plugin_id)
            for key, value in (("settings", settings), ("state", state)):
                if value is not None:
                    if type(value) is not dict:
                        raise StoreError("Plugin settings and state must be objects.")
                    record[key] = portable_values(value)
            self._save_manifest(updated)

    def _save_manifest(self, value):
        validated = validate_manifest(value)
        try:
            atomic_write(self.storage_home / "manifest.json", json.dumps(validated).encode())
        except OSError:
            raise StoreError("The budget's plugin setup could not be saved; its previous settings are unchanged.") from None
        self.manifest = validated
        self.checkpoint()

    def context(self, plugin_id):
        record = self.plugin_record(plugin_id) or {"settings": {}, "state": {}}
        return BudgetContext(
            self.id,
            MappingProxyType(copy.deepcopy(record["settings"])),
            MappingProxyType(copy.deepcopy(record["state"])),
            lambda value: self.save_plugin_data(plugin_id, state=value),
            lambda name: self.read_file(plugin_id, name),
            lambda name, raw: self.write_file(plugin_id, name, raw),
            lambda name: self.delete_file(plugin_id, name),
        )

    def files_for(self, plugin_id):
        if not valid_plugin_name(plugin_id):
            raise StoreError("Invalid plugin ID.")
        prefix = f"plugins/{plugin_id}/"
        return {key[len(prefix):]: value for key, value in self.files.items() if key.startswith(prefix)}

    def _file_key(self, plugin_id, name):
        if self.plugin_record(plugin_id) is None or type(name) is not str or name in {"settings.json", "state.json"}:
            raise StoreError("Choose a data file within this plugin's budget namespace.")
        return validate_file_name(f"plugins/{plugin_id}/{name}")

    def read_file(self, plugin_id, name):
        with self.lock:
            return self.files.get(self._file_key(plugin_id, name))

    def _save_files(self, files):
        raw = encode_container(self.manifest, files)
        try:
            atomic_write(self.storage_home / "plugin-data.whab", raw)
        except OSError:
            raise StoreError("The plugin file could not be saved; its previous contents are unchanged.") from None
        self.files = files
        self.checkpoint()

    def write_file(self, plugin_id, name, raw):
        with self.lock:
            if plugin_id == self.manifest["storage"]:
                raise StoreError("Storage payload files are managed by the storage backend.")
            if type(raw) is not bytes:
                raise StoreError("Plugin files must contain bytes.")
            key = self._file_key(plugin_id, name)
            self._save_files({**self.files, key: raw})

    def delete_file(self, plugin_id, name):
        with self.lock:
            if plugin_id == self.manifest["storage"]:
                raise StoreError("Storage payload files are managed by the storage backend.")
            key = self._file_key(plugin_id, name)
            self._save_files({name: raw for name, raw in self.files.items() if name != key})

    def bundle(self, *, include_credentials=False, password=None, preserve_credentials=False):
        export_files = getattr(self.store, "export_budget_files", None)
        if callable(export_files):
            with self.lock:
                plugin_id = self.manifest["storage"]
                prefix = f"plugins/{plugin_id}/"
                files = {key: value for key, value in self.files.items() if not key.startswith(prefix)}
                payload = export_files("personal")
                if type(payload) is not dict:
                    raise StoreError("This storage plugin returned invalid budget files.")
                files.update({prefix + name: raw for name, raw in payload.items()})
                blob = self.credential_blob if preserve_credentials else None
                if include_credentials:
                    if plugin_id != "client":
                        raise StoreError("Only remote client credentials can be included in this budget file.")
                    profile = {key: value for key, value in self.storage_config.items()
                               if key in {"CLIENT_URL", "CLIENT_TOKEN", "CLIENT_BUDGET_ID"}}
                    blob = encrypt_credentials(self.id, {plugin_id: profile}, password)
                raw = encode_container(self.manifest, files, credential_blob=blob)
                self.files = files
                return raw
        export = getattr(self.store, "export_budget", None)
        if not callable(export):
            raise StoreError("This storage plugin cannot export a portable budget.")
        if include_credentials or self.files:
            raise StoreError("This storage plugin does not support plugin-owned budget files.")
        return encode_bundle(self.manifest, export("personal"))

    def checkpoint(self):
        if self.store is None:
            return
        with self.lock:
            try:
                raw = self.bundle(preserve_credentials=True)
                container = decode_container(raw, password=self.password)
                candidate = container_digest(container)
                atomic_write(self.storage_home / "manifest.json", json.dumps(self.manifest).encode())
                if container.legacy_ledger is None:
                    # The ordinary archive is already validated and contains no
                    # credentials. Encrypted remote credentials stay source-only.
                    local_raw = raw if container.credential_blob is None else encode_container(self.manifest, self.files)
                    atomic_write(self.storage_home / "plugin-data.whab", local_raw)
                source = {"content": self._source["content"] if self._source else None, "pending": candidate}
                # Journal before replacing files, so either revision can reopen after interruption.
                atomic_write(self.storage_home / "source.json", json.dumps(source).encode())
                self._source = source
            except Exception:
                self.status = "pending"
                for destination in self.destinations:
                    destination["status"] = "pending"
                return
            self.status = "saved"
            try:
                existing = _read_bytes(self.path) if self.path.exists() else None
                if self._file_digest is None and existing is not None:
                    if container_digest(decode_container(existing, password=self.password)) != candidate:
                        raise StoreError("Another revision occupies this budget file location.")
                    # An interrupted first save can leave an identical file without
                    # a known byte digest. Adopt it without replacing its contents.
                    self._file_digest = digest(existing)
                else:
                    if existing is not None and digest(existing) != self._file_digest:
                        raise OSError("Budget file changed outside this session")
                    self._write_copy(self.path, raw, self._file_digest if existing is not None else None)
                    self._file_digest = digest(raw)
                complete = {"content": candidate}
                atomic_write(self.storage_home / "source.json", json.dumps(complete).encode())
                self._source = complete
            except (OSError, StoreError):
                self.status = "pending"
            for destination in self.destinations:
                try:
                    path = Path(destination["path"])
                    current = self._check_copy_content(destination, candidate)
                    if current is not None and current == destination.get("pending"):
                        self._copy_marker(destination, content=current)
                    # Keep the previous and candidate content until the replacement is
                    # durably recorded. A restarted session can recognize either copy.
                    self._copy_marker(destination, pending=candidate)
                    current = self._check_copy_content(destination, candidate)
                    if current != candidate:
                        self._write_copy(path, raw, current)
                    self._copy_marker(destination, content=candidate)
                    destination["status"] = "saved"
                except (OSError, StoreError):
                    destination["status"] = "pending"

    def _check_copy_content(self, destination, candidate):
        path = Path(destination["path"])
        if not path.exists():
            return None
        container = decode_container(_read_bytes(path), password=self.password)
        if container.manifest["id"] != self.id:
            raise StoreError("Another budget occupies this destination.")
        current = container_digest(container)
        if current not in (destination.get("content"), destination.get("pending"), candidate):
            raise StoreError("This budget copy changed elsewhere. Preserve both revisions before replacing it.")
        return current

    @staticmethod
    def _write_copy(path, raw, current):
        if current is not None:
            atomic_write(path, raw)
            return
        # Publishing a new destination must never replace a file that appears
        # after the existence check. A same-filesystem hard link is exclusive.
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix=".whab-copy-", dir=path.parent) as staging:
            prepared = Path(staging) / "budget.whab"
            atomic_write(prepared, raw)
            os.link(prepared, path)

    def _persist_copies(self, destinations):
        values = [
            {key: item[key] for key in ("name", "path", "content", "pending") if key in item}
            for item in destinations
        ]
        atomic_write(self.storage_home / "copies.json", json.dumps(values).encode())

    def _copy_marker(self, destination, *, content=None, pending=None):
        staged = copy.deepcopy(self.destinations)
        updated = staged[self.destinations.index(destination)]
        if pending is not None:
            updated["pending"] = pending
        else:
            updated["content"] = content
            updated.pop("pending", None)
        self._persist_copies(staged)
        if pending is not None:
            destination["pending"] = pending
        else:
            destination["content"] = content
            destination.pop("pending", None)

    def add_copy(self, name, path):
        if type(name) is not str or not name.strip() or len(name) > 100 or "\x00" in name or type(path) is not str or len(path) > 4096 or "\x00" in path or not Path(path).is_absolute():
            raise StoreError("Give the copy a name and an absolute file location.")
        target = Path(path).expanduser().resolve()
        if target == self.path or any(Path(item["path"]) == target for item in self.destinations) or len(self.destinations) >= 20:
            raise StoreError("Choose a new budget copy location (at most 20).")
        if target.exists():
            raise StoreError("Choose an unused file location for this budget copy.")
        destinations = [*self.destinations, {"name": name.strip(), "path": str(target), "content": None, "status": "pending"}]
        try:
            self._persist_copies(destinations)
        except OSError:
            raise StoreError("The budget copy destination could not be saved.") from None
        self.destinations = destinations
        self.checkpoint()

    def projection(self):
        return {
            "id": self.id, "name": self.name, "portable": True,
            "storage": self.manifest["storage"],
            "copies": [{"name": "Budget file", "status": self.status}, *[{"name": item["name"], "status": item["status"]} for item in self.destinations]],
            "plugins": copy.deepcopy(self.manifest["plugins"]), "missingPlugins": list(self.missing_plugins),
        }


class TrackedStorage:
    """Checkpoint portable files after committed writes, including bank synchronization."""

    MUTATIONS = frozenset({"add_account", "update_account", "add_category", "add_transaction", "edit_transaction", "review_transactions", "assign", "apply_sync", "import_workspace", "save_item", "set_item_status", "disconnect_item", "save_receipt", "delete_receipt"})

    def __init__(self, store, session=None):
        self.store = store
        self.session = session
        self.lock = session.lock if session else RLock()
        self.on_mutation = None
        self.after_ingestion = None

    def __getattr__(self, name):
        member = getattr(self.store, name)
        if name not in self.MUTATIONS or not callable(member):
            return member

        def mutate(*args, **kwargs):
            with self.lock:
                result = member(*args, **kwargs)
                if self.on_mutation:
                    self.on_mutation()
                if self.session:
                    self.session.checkpoint()
                if name in {"apply_sync", "import_workspace"} and self.after_ingestion:
                    self.after_ingestion()
                return result

        return mutate
