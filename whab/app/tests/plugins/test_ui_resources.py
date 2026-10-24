from dataclasses import replace
from pathlib import Path

import pytest

from whab.plugins import PluginError, PluginResources, UserInterface
from whab.plugins.ui_render import render_page
from whab.plugins.ui_resources import UIResourceArchive, VisualBundle, capture_visual


def interface(label=b"one"):
    resources = PluginResources({"templates/base.html": b"{{ greeting }}", "static/app.js": label})
    return UserInterface(None, None, lambda *args, **kw: {"greeting": "Fictional"}, resources=resources)


def test_visual_generation_binds_code_resources_and_projection_flags_and_survives_reopen(tmp_path):
    ui = interface()
    one = capture_visual("fictional", ui, "a" * 64)
    two = capture_visual("fictional", ui, "b" * 64)
    three = capture_visual("fictional", replace(ui, transaction_page_size=20), "b" * 64)
    assert len({one.generation, two.generation, three.generation}) == 3
    archive = UIResourceArchive(tmp_path)
    for bundle in (one, two, three):
        assert archive.store(bundle).generation == bundle.generation
    reopened = UIResourceArchive(tmp_path)
    assert reopened.load(one.generation).encoded() == one.encoded()
    assert reopened.load(two.generation).resources.files["static/app.js"] == b"one"
    assert archive.store(one).encoded() == one.encoded()
    assert all(path.stat().st_mode & 0o077 == 0 for path in archive.directory.glob("*.bundle"))
    with pytest.raises(TypeError):
        one.flags["budget_months"] = 12
    # Input maps and files cannot mutate the retained immutable bundle.
    with pytest.raises(TypeError):
        one.resources.files["static/app.js"] = b"changed"


def test_visual_capture_reads_legacy_paths_once_without_later_mixing(tmp_path):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    (templates / "base.html").write_text("old HTML")
    (static / "app.js").write_bytes(b"old JS")
    ui = UserInterface(templates, static, lambda *args, **kw: {})
    old = capture_visual("fictional", ui, "a" * 64)
    (templates / "base.html").write_text("new HTML")
    (static / "app.js").write_bytes(b"new JS")
    new = capture_visual("fictional", ui, "a" * 64)
    assert old.generation != new.generation
    assert old.resources.files["templates/base.html"] == b"old HTML"
    assert old.resources.files["static/app.js"] == b"old JS"


@pytest.mark.parametrize("corrupt", [lambda raw: raw[:-1], lambda raw: raw + b"x", lambda raw: b"BAD!" + raw[4:],
                                    lambda raw: raw.replace(b'"format":1', b'"format":2'),
                                    lambda raw: raw.replace(b'"budget_months":1', b'"budget_months":true')])
def test_damaged_generation_is_never_repaired_or_served_as_current(tmp_path, corrupt):
    bundle = capture_visual("fictional", interface(), "a" * 64)
    archive = UIResourceArchive(tmp_path)
    archive.store(bundle)
    target = archive.directory / (bundle.generation + ".bundle")
    raw = corrupt(target.read_bytes())
    target.write_bytes(raw)
    with pytest.raises(PluginError):
        archive.load(bundle.generation)
    with pytest.raises(PluginError):
        archive.store(bundle)
    assert target.read_bytes() == raw


def test_symlinks_and_incomplete_bundles_are_refused_without_reading_private_targets(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / "visual-ui").symlink_to(private, target_is_directory=True)
    bundle = capture_visual("fictional", interface(), "a" * 64)
    with pytest.raises(PluginError):
        UIResourceArchive(home).store(bundle)
    assert list(private.iterdir()) == []
    with pytest.raises(PluginError):
        VisualBundle("fictional", "a" * 64, PluginResources({"static/app.js": b"no template"}), dict(bundle.flags))
    with pytest.raises(ValueError):
        replace(interface(), templates=Path("templates"))
    with pytest.raises(ValueError):
        replace(interface(), render="callable required")


def test_worker_visual_renderer_accepts_a_resource_bundle_without_filesystem_paths():
    arguments = {"input": {"data": {}, "status": {}, "items": [], "workspace": "personal", "page": "budget", "month": "2026-10", "selected": None},
                 "context": {}, "routing": {"key": "home", "personal_key": "home", "workspace": "personal", "page": "budget", "month_token": "current-month", "currency": "USD", "account_slugs": {}},
                 "request": {"path": "/", "query": [], "headers": {}}, "templates": {}}
    assert render_page(interface(), arguments) == b"Fictional"


def test_private_temporary_cleanup_failure_cannot_override_durable_store_or_authored_error(tmp_path, monkeypatch):
    bundle = capture_visual("fictional", interface(), "a" * 64)
    archive = UIResourceArchive(tmp_path)
    original = Path.unlink
    def fail_cleanup(path, *args, **kwargs):
        if path.name.startswith(".visual-"):
            raise PermissionError(f"private filesystem path: {path}")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    assert archive.store(bundle).generation == bundle.generation
    assert archive.load(bundle.generation).encoded() == bundle.encoded()
    failing = UIResourceArchive(tmp_path / "other")
    def fail_publication(path):
        raise PermissionError(f"private filesystem path: {path}")
    monkeypatch.setattr(failing, "_sync", fail_publication)
    with pytest.raises(PluginError) as raised:
        failing.store(bundle)
    assert str(tmp_path) not in str(raised.value) and "private filesystem" not in str(raised.value)
