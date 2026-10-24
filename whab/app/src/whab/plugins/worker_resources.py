"""Capture bounded parser UI data; immutable generations never share mutable paths."""

import hashlib
import os
import stat
from pathlib import Path

from .sdk import BankUI, FileImport, PluginResources
from .wire import WireError


def capture_resources(parser):
    if parser.resources is not None:
        result = PluginResources(parser.resources.files)
    else:
        result = capture_resource_paths(parser.templates, parser.static)
    if result is not None:
        FileImport(parser.parse, resources=result)
    return result


def capture_bank_resources(ui):
    if ui is None:
        return None
    resources = ui.resources or capture_resource_paths(ui.templates, ui.static)
    BankUI(resources=resources)
    return resources


def capture_resource_paths(templates, static):
    """Read only bounded regular files; callers validate their required assets."""
    if templates is None and static is None:
        return None
    if templates is None or static is None:
        raise WireError()
    result, entries = {}, 0
    for prefix, root in (("templates", templates), ("static", static)):
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            raise WireError()
        pending = [root]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as children:
                for child in children:
                    entries += 1
                    if entries > 512 or child.is_symlink():
                        raise WireError()
                    path = Path(child.path)
                    if child.is_dir(follow_symlinks=False):
                        pending.append(path)
                        continue
                    if not stat.S_ISREG(child.stat(follow_symlinks=False).st_mode):
                        raise WireError()
                    if len(result) >= 256 or child.stat(follow_symlinks=False).st_size > 2 * 1024 * 1024:
                        raise WireError()
                    with path.open("rb") as source:
                        result[prefix + "/" + path.relative_to(root).as_posix()] = source.read(2 * 1024 * 1024 + 1)
                    if sum(map(len, result.values())) > 4 * 1024 * 1024:
                        raise WireError()
    return PluginResources(result)


def resource_bundle(resources):
    if resources is None:
        return {"files": None}, b""
    resources = PluginResources(resources.files)
    records, payload = [], bytearray()
    for name, raw in sorted(resources.files.items()):
        records.append({"name": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        payload.extend(raw)
    return {"files": records}, bytes(payload)


def read_resource_bundle(value, payload):
    if type(value) is not dict or set(value) != {"files"} or type(payload) is not bytes:
        raise WireError()
    if value["files"] is None:
        if payload:
            raise WireError()
        return None
    if type(value["files"]) is not list or not 1 <= len(value["files"]) <= 256 or len(payload) > 4 * 1024 * 1024:
        raise WireError()
    files, offset = {}, 0
    for record in value["files"]:
        if type(record) is not dict or set(record) != {"name", "size", "sha256"} or type(record["size"]) is not int or not 0 <= record["size"] <= 2 * 1024 * 1024:
            raise WireError()
        name, size = record["name"], record["size"]
        if type(name) is not str or name in files:
            raise WireError()
        raw = payload[offset:offset + size]
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise WireError()
        offset += size
        files[name] = raw
    if offset != len(payload):
        raise WireError()
    return PluginResources(files)
