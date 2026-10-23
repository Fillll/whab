"""Immutable visual resources retained across changes and process restarts."""

import hashlib
import json
import os
import re
import stat
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .runtime import PluginError
from .sdk import PluginResources, UserInterface, valid_plugin_name
from .worker_resources import capture_resource_paths, read_resource_bundle, resource_bundle

_HEADER = struct.Struct("!4sII")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FLAGS = ("budget_months", "lightweight_transactions", "lightweight_accounts", "transaction_page_size")
_MAX_METADATA = 128 * 1024
_MAX_PAYLOAD = 4 * 1024 * 1024


def _invalid(identifier=None):
    return PluginError("The visual interface resource generation is unavailable or invalid. Preserve its files and prepare a compatible replacement.",
                       code="ui_resources_unavailable", plugin_id=identifier)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _pairs(values):
    result = {}
    for name, value in values:
        if name in result:
            raise ValueError()
        result[name] = value
    return result


def _flags(value):
    if type(value) is not dict or value.keys() != set(_FLAGS):
        raise ValueError()
    # Public SDK validation defines projection bounds and strict boolean types.
    UserInterface(Path("templates"), Path("static"), lambda *args, **kw: {}, **value)
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class VisualBundle:
    plugin_id: str
    code_identity: str
    resources: PluginResources = field(repr=False)
    flags: dict = field(repr=False)
    generation: str = field(init=False)

    def __post_init__(self):
        if not valid_plugin_name(self.plugin_id) or type(self.code_identity) is not str or _HASH.fullmatch(self.code_identity) is None:
            raise _invalid(self.plugin_id)
        try:
            resources = PluginResources(self.resources.files)
            if "templates/base.html" not in resources.files or not any(name.startswith("static/") for name in resources.files):
                raise ValueError()
            flags = _flags(dict(self.flags))
            metadata, payload = resource_bundle(resources)
            identity = {"plugin": self.plugin_id, "code": self.code_identity, "flags": dict(flags), "resources": metadata}
            generation = hashlib.sha256(_json(identity) + payload).hexdigest()
        except Exception:
            raise _invalid(self.plugin_id) from None
        object.__setattr__(self, "resources", resources)
        object.__setattr__(self, "flags", flags)
        object.__setattr__(self, "generation", generation)

    def encoded(self):
        resources, payload = resource_bundle(self.resources)
        metadata = _json({"format": 1, "plugin": self.plugin_id, "code": self.code_identity,
                          "generation": self.generation, "flags": dict(self.flags), "resources": resources})
        if len(metadata) > _MAX_METADATA or len(payload) > _MAX_PAYLOAD:
            raise _invalid(self.plugin_id)
        return _HEADER.pack(b"UIR1", len(metadata), len(payload)) + metadata + payload

    @classmethod
    def decode(cls, raw):
        try:
            if type(raw) is not bytes or len(raw) < _HEADER.size:
                raise ValueError()
            magic, metadata_size, payload_size = _HEADER.unpack(raw[:_HEADER.size])
            if (magic != b"UIR1" or not 2 <= metadata_size <= _MAX_METADATA or payload_size > _MAX_PAYLOAD
                    or len(raw) != _HEADER.size + metadata_size + payload_size):
                raise ValueError()
            metadata = json.loads(raw[_HEADER.size:_HEADER.size + metadata_size], object_pairs_hook=_pairs)
            if type(metadata) is not dict or metadata.keys() != {"format", "plugin", "code", "generation", "flags", "resources"}:
                raise ValueError()
            if type(metadata["format"]) is not int or metadata["format"] != 1:
                raise ValueError()
            resources = read_resource_bundle(metadata["resources"], raw[_HEADER.size + metadata_size:])
            bundle = cls(metadata["plugin"], metadata["code"], resources, metadata["flags"])
            if bundle.generation != metadata["generation"] or bundle.encoded() != raw:
                raise ValueError()
            return bundle
        except Exception:
            raise _invalid() from None


def capture_visual(plugin_id, ui, code_identity):
    try:
        if type(ui) is not UserInterface:
            raise ValueError()
        resources = ui.resources if ui.resources is not None else capture_resource_paths(ui.templates, ui.static)
        return VisualBundle(plugin_id, code_identity, resources, {name: getattr(ui, name) for name in _FLAGS})
    except Exception:
        raise _invalid(plugin_id) from None


class UIResourceArchive:
    """Private cache, never executable archives; old generations are not deleted."""

    def __init__(self, home):
        self.home = Path(home).resolve()
        self.directory = self.home / "visual-ui" / "resources"

    def _directory(self):
        current = self.home
        for part in ("visual-ui", "resources"):
            current /= part
            if current.is_symlink() or current.exists() and not current.is_dir():
                raise _invalid()
            current.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._sync(current.parent)

    @staticmethod
    def _sync(path):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def load(self, generation):
        if type(generation) is not str or _HASH.fullmatch(generation) is None:
            raise _invalid()
        try:
            for path in (self.home / "visual-ui", self.directory):
                if path.is_symlink() or not path.is_dir():
                    raise ValueError()
            path = self.directory / (generation + ".bundle")
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                raise ValueError()
            with path.open("rb") as source:
                raw = source.read(_HEADER.size + _MAX_METADATA + _MAX_PAYLOAD + 1)
            bundle = VisualBundle.decode(raw)
            if bundle.generation != generation:
                raise ValueError()
            return bundle
        except Exception:
            raise _invalid() from None

    def store(self, bundle):
        if type(bundle) is not VisualBundle:
            raise _invalid()
        temporary = None
        try:
            self._directory()
            raw = bundle.encoded()
            target = self.directory / (bundle.generation + ".bundle")
            if target.exists() or target.is_symlink():
                if self.load(bundle.generation).encoded() != raw:
                    raise ValueError()
                self._sync(self.directory)
                return bundle
            descriptor, temporary = tempfile.mkstemp(prefix=".visual-", dir=self.directory)
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if self.load(bundle.generation).encoded() != raw:
                    raise ValueError()
            self._sync(self.directory)
            return bundle
        except Exception:
            raise _invalid(bundle.plugin_id) from None
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    # A private orphan does not undo a durable immutable bundle
                    # or replace its fixed diagnostic with a filesystem path.
                    pass
