"""Explicit pytest plugin: partition the complete collection without dropping tests."""

import hashlib
import json
from pathlib import Path

import pytest


def partition(nodeids, index, count):
    if count < 1 or not 0 <= index < count:
        raise ValueError("Shard index must be within a positive shard count.")
    return [nodeid for nodeid in nodeids
            if int.from_bytes(hashlib.sha256(nodeid.encode()).digest()[:8]) % count == index]


def pytest_addoption(parser):
    group = parser.getgroup("whab-pr")
    group.addoption("--pr-shard-index", type=int, default=0)
    group.addoption("--pr-shard-count", type=int, default=1)
    group.addoption("--pr-collection-dir", type=Path)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    directory = config.getoption("--pr-collection-dir")
    if directory is None:
        raise pytest.UsageError("PR sharding requires a collection evidence directory.")
    index, count = (config.getoption(name) for name in ("--pr-shard-index", "--pr-shard-count"))
    nodeids = sorted(item.nodeid for item in items)
    if len(set(nodeids)) != len(nodeids):
        raise pytest.UsageError("Duplicate test IDs cannot be certified as a complete partition.")
    try:
        selected = partition(nodeids, index, count)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
    selected_set = set(selected)
    retained = [item for item in items if item.nodeid in selected_set]
    deselected = [item for item in items if item.nodeid not in selected_set]
    items[:] = retained
    config.hook.pytest_deselected(items=deselected)
    directory.mkdir(parents=True, exist_ok=True)
    worker = getattr(config, "workerinput", {}).get("workerid", "main")
    (directory / f"{worker}.json").write_text(json.dumps({
        "index": index, "count": count, "collected": nodeids, "selected": selected,
    }) + "\n")
