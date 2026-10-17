"""Changelog governance applies to all owners and does not borrow other entries' PRs."""

import subprocess
from importlib import util
from pathlib import Path

import pytest

PR = "[#37](https://github.com/Fillll/whab/pull/37)"


@pytest.fixture
def gate():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_changelog_prs.py"
    spec = util.spec_from_file_location("whab_changelog_gate", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repository(gate, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    return tmp_path


def track(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "--", name], cwd=root, check=True)
    return path


@pytest.mark.parametrize("body", [f"PR: {PR}\n\n- Add behavior.\n", f"- Add behavior. {PR}\n"])
def test_section_level_or_per_bullet_link_satisfies_policy(gate, body):
    assert gate.validate_changelog("# Changelog\n\n## 1.0.0 — 2026-10-16\n\n" + body) == []


@pytest.mark.parametrize("link", [
    "", "#37", "https://github.com/Fillll/whab/pull/37",
    "[#N](https://github.com/Fillll/whab/pull/N)",
    "[#0](https://github.com/Fillll/whab/pull/0)",
    "[#-1](https://github.com/Fillll/whab/pull/-1)",
    "[#37](https://github.com/Fillll/other/pull/37)",
    "[#37](https://github.com/someone/whab/pull/37)",
    "[#37](https://github.com/Fillll/whab/issues/37)",
    "[#37](http://github.com/Fillll/whab/pull/37)",
    "![#37](https://github.com/Fillll/whab/pull/37)",
])
def test_missing_placeholder_or_incorrect_reference_fails(gate, link):
    errors = gate.validate_changelog(f"# Changelog\n\n## 1.0.0\n\n- Add behavior. {link}\n")
    assert len(errors) == 1
    assert errors[0].startswith("3: 1.0.0: add an originating PR Markdown link")


def test_empty_unreleased_is_allowed(gate):
    assert gate.validate_changelog(f"## Unreleased\n\n## 1.0.0\n\nPR: {PR}\n") == []
    assert gate.validate_changelog("## Unreleased\n\n") == []


def test_populated_unreleased_requires_its_own_link(gate):
    assert len(gate.validate_changelog(f"## Unreleased\n\n- Fix behavior.\n\n## 1.0.0\n{PR}")) == 1
    assert gate.validate_changelog(f"## Unreleased\n\n- Fix behavior. {PR}\n") == []


def test_link_in_another_section_or_preamble_does_not_satisfy_version(gate):
    errors = gate.validate_changelog(
        f"# Changelog\n\nPR: {PR}\n\n## 2.0.0\n\n- Change behavior.\n\n"
        f"## 1.0.0\n\nPR: {PR}\n\n- Initial version.\n"
    )
    assert len(errors) == 1
    assert "2.0.0" in errors[0]


def test_empty_released_section_or_absent_sections_fails(gate):
    assert len(gate.validate_changelog("## 1.0.0\n\n")) == 1
    assert gate.validate_changelog("# Changelog\n") == [
        "1: no changelog sections found (expected ## headings)"
    ]


def test_discovery_includes_all_owners_and_future_plugins_but_not_runtime_files(gate, repository):
    names = [
        "CHANGELOG.md", "whab/app/CHANGELOG.md",
        "whab/app/plugins/example/example/CHANGELOG.md",
        "whab/app/plugins/future/new-plugin/CHANGELOG.md",
        "whab/launchers/future/CHANGELOG.md",
        "another-product/CHANGELOG.md",
    ]
    for name in names:
        track(repository, name, "## Unreleased\n")
    runtime = repository / "runtime" / "CHANGELOG.md"
    runtime.parent.mkdir()
    runtime.write_text("Private runtime content, not tracked.\n")
    track(repository, "whab/app/plugins/future/new-plugin/README.md", "Plugin\n")

    assert gate.tracked_changelogs(repository) == tuple(repository / name for name in sorted(names))


def test_main_reports_missing_provenance_and_passes_after_correction(gate, repository, capsys):
    path = track(repository, "whab/launchers/macos/CHANGELOG.md", "## 1.0.0\n\n- Add launcher.\n")
    assert gate.main() == 1
    assert "whab/launchers/macos/CHANGELOG.md:1: 1.0.0" in capsys.readouterr().err

    path.write_text(f"## 1.0.0\n\nPR: {PR}\n\n- Add launcher.\n")
    assert gate.main() == 0
    assert "All 1 tracked changelogs" in capsys.readouterr().out


def test_main_fails_when_no_changelogs_are_tracked(gate, repository, capsys):
    assert gate.main() == 1
    assert "No tracked CHANGELOG.md" in capsys.readouterr().err


def test_main_reports_unreadable_tracked_file(gate, repository, capsys):
    path = track(repository, "CHANGELOG.md", "## Unreleased\n")
    path.unlink()
    assert gate.main() == 1
    assert "Changelog PR check failed" in capsys.readouterr().err
