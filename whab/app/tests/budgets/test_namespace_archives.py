"""Namespace copies reject oversized input before reading or encoding all files."""

import base64
import json
from types import SimpleNamespace

import pytest

from whab.budgets.document import new_manifest
from whab.budgets.storage_replicas import NamespaceArchive
from whab.plugins import PluginError, StoreError
from whab.services.categorization import local_context


def archive(tmp_path, *, named=False):
    session = SimpleNamespace(manifest=new_manifest("Fictional", storage="sqlite"), files={}) if named else None
    storage = SimpleNamespace(
        locations=SimpleNamespace(directory=tmp_path / "storage-authority", data={"instances": {}}),
        runtime=SimpleNamespace(budget_id=session.manifest["id"] if named else "default"),
        settings=SimpleNamespace(home=tmp_path, plaid_env="sandbox", plugins=()),
        manager=None, session=session,
    )
    return NamespaceArchive(storage)


@pytest.mark.parametrize("named", [False, True])
def test_namespace_file_count_is_bounded_before_complete_map_or_walk(tmp_path, monkeypatch, named):
    monkeypatch.setattr("whab.budgets.storage_replicas.MAX_CONTAINER_FILES", 2)
    target = archive(tmp_path, named=named)
    if named:
        class ManyFiles:
            visited = 0
            def items(self):
                for number in range(10000):
                    self.visited += 1
                    yield f"plugins/unavailable/{number}.bin", b"x"
        files = ManyFiles()
        target.storage.session.files = files
    else:
        directory = tmp_path / "data" / "plugin-state" / "sandbox"
        directory.mkdir(parents=True)
        for number in range(10):
            (directory / f"{number}.bin").write_bytes(b"x")
    with pytest.raises(PluginError, match="file count|filesystem entries"):
        target._payload()
    if named:
        assert files.visited == 3


@pytest.mark.parametrize("named", [False, True])
def test_namespace_aggregate_budget_is_checked_before_base64_expansion(tmp_path, monkeypatch, named):
    import base64
    monkeypatch.setattr("whab.budgets.storage_replicas.MAX_BUNDLE_BYTES", 2048)
    monkeypatch.setattr("whab.budgets.storage_replicas.MAX_MANIFEST_BYTES", 256)
    target = archive(tmp_path, named=named)
    if named:
        target.storage.session.files = {f"plugins/unavailable/{number}.bin": b"x" * 600 for number in range(10)}
    else:
        directory = tmp_path / "data" / "plugin-state" / "sandbox"
        directory.mkdir(parents=True)
        for number in range(10):
            (directory / f"{number}.bin").write_bytes(b"x" * 600)
    encode = base64.b64encode
    encoded = []
    def observed(raw):
        encoded.append(len(raw))
        return encode(raw)
    monkeypatch.setattr("whab.budgets.storage_replicas.base64.b64encode", observed)
    with pytest.raises((PluginError, ValueError)):
        target._payload()
    assert encoded == [600]


def test_invalid_local_namespace_write_does_not_delete_existing_file(tmp_path):
    context = local_context(tmp_path, "default", "fictional")
    context.write_file("data.bin", b"preserved-model")
    with pytest.raises(StoreError, match="bytes"):
        context.write_file("data.bin", None)
    assert context.read_file("data.bin") == b"preserved-model"


def test_default_namespace_and_host_selection_paths_cannot_collide(tmp_path):
    target = archive(tmp_path)
    directory = tmp_path / "data" / "plugin-state" / "sandbox" / "host"
    directory.mkdir(parents=True)
    (directory / "plugin-selection.json").write_bytes(b"opaque-plugin-file")
    (tmp_path / "plugins.json").write_bytes(b"host-selection-file")
    files = json.loads(target._payload())["files"]
    assert base64.b64decode(files["plugin-state/host/plugin-selection.json"]) == b"opaque-plugin-file"
    assert base64.b64decode(files["host/plugin-selection.json"]) == b"host-selection-file"
