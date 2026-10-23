"""Bounded complete namespace data frames; no filesystem paths or callbacks."""

from .effect_store import digest, encode
from .namespace_effects import NamespaceSnapshot
from .wire import WireError, _value

MAX_WORKER_NAMESPACE = 48 * 1024 * 1024
MAX_NAMESPACE_METADATA = 3 * 1024 * 1024


def namespace_bundle(snapshot):
    if type(snapshot) is not NamespaceSnapshot:
        raise WireError()
    if len(snapshot.state_json) + sum(map(len, snapshot.files.values())) > MAX_WORKER_NAMESPACE:
        raise WireError()
    files = sorted(snapshot.files.items())
    metadata = {"state": snapshot.state, "files": [
        {"name": name, "size": len(raw), "sha256": digest(raw)} for name, raw in files
    ]}
    _value(metadata)
    encode(metadata, MAX_NAMESPACE_METADATA)
    return metadata, b"".join(raw for _, raw in files)


def read_namespace(value, payload):
    try:
        if (type(value) is not dict or value.keys() != {"state", "files"}
                or type(value["files"]) is not list or len(value["files"]) > 1000
                or type(payload) is not bytes or len(payload) > MAX_WORKER_NAMESPACE):
            raise WireError()
        _value(value)
        encode(value, MAX_NAMESPACE_METADATA)
        files, offset = {}, 0
        for row in value["files"]:
            if (type(row) is not dict or row.keys() != {"name", "size", "sha256"}
                    or type(row["name"]) is not str or row["name"] in files
                    or type(row["size"]) is not int or not 0 <= row["size"] <= len(payload) - offset):
                raise WireError()
            raw = payload[offset:offset + row["size"]]
            if digest(raw) != row["sha256"]:
                raise WireError()
            files[row["name"]] = raw
            offset += row["size"]
        if offset != len(payload):
            raise WireError()
        snapshot = NamespaceSnapshot(value["state"], files)
        namespace_bundle(snapshot)
        return snapshot
    except Exception:
        raise WireError() from None
