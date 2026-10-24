"""A candidate reads existing native data without competing for writer ownership."""

import pytest
from whab_plugin_json import Store

from whab.domain.errors import StoreError


def test_read_only_while_writer_owns_lock_preserves_native_bytes(tmp_path):
    path = tmp_path / "data" / "budget.json"
    with_writer = Store(path)
    with_writer.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 123})
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    reader = Store(path, read_only=True)
    try:
        assert reader.commit_evidence() == with_writer.commit_evidence()
        assert reader.accounts("personal") == with_writer.accounts("personal")
        with pytest.raises(StoreError, match="Read-only"):
            reader.add_account("personal", {"name": "Blocked", "type": "depository", "balance": 1})
        assert {p.name: p.read_bytes() for p in path.parent.iterdir()} == before
    finally:
        reader.close()
        with_writer.close()


def test_read_only_missing_never_creates_home_or_lock(tmp_path):
    path = tmp_path / "missing" / "budget.json"
    with pytest.raises(StoreError, match="existing"):
        Store(path, read_only=True)
    assert not path.parent.exists()


def test_read_only_refuses_legacy_native_schema_without_adoption(tmp_path):
    import json

    path = tmp_path / "budget.json"
    writer = Store(path)
    writer.close()
    value = json.loads(path.read_bytes())
    value["formatVersion"] = 2
    value["authority"].pop("replica")
    value["authority"].pop("promotionId")
    path.write_text(json.dumps(value))
    raw = path.read_bytes()
    with pytest.raises(StoreError, match="explicitly"):
        Store(path, read_only=True)
    assert path.read_bytes() == raw
    assert not list(tmp_path.glob("*.v2*"))


@pytest.mark.parametrize("value", [None, 0, "true"])
def test_read_only_mode_requires_boolean(tmp_path, value):
    with pytest.raises(StoreError, match="boolean"):
        Store(tmp_path / "budget.json", read_only=value)
    assert not (tmp_path / "budget.json.lock").exists()
