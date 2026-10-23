"""Contribution checks share CI behavior and never discover private runtime documents."""

import subprocess
import sys
from importlib import util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def checker(request):
    name = request.param
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = util.spec_from_file_location(f"whab_{name}", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("checker", ["check"], indirect=True)
def test_full_gate_runs_python_suite_once_and_quick_gate_skips_it(checker, monkeypatch, tmp_path):
    test = tmp_path / "plugins/interface/web/tests/navigation.test.cjs"
    test.parent.mkdir(parents=True)
    test.write_text("// fictional browser test\n")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    full = checker.commands(docs_only=False, quick=False, node="node")
    quick = checker.commands(docs_only=False, quick=True, node="node")
    assert sum("scripts/check_plugin_coverage.py" in command for _, command in full) == 1
    assert not any("pytest" in command for _, command in full)
    assert "scripts/check_distribution.py" in full[-1][1]
    assert all("coverage" not in " ".join(command) and "distribution" not in " ".join(command) for _, command in quick)
    assert quick[-1][1] == ["node", "--test", str(test.relative_to(tmp_path))]


@pytest.mark.parametrize("checker", ["check"], indirect=True)
def test_docs_mode_does_not_require_node_or_development_packages(checker, monkeypatch):
    monkeypatch.setattr(checker.shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None)
    monkeypatch.setattr(checker.importlib.util, "find_spec", lambda name: pytest.fail("must not inspect dev packages"))
    assert checker.preflight(docs_only=True, quick=False) is None
    assert checker.commands(docs_only=True, quick=False, node=None) == [
        ("Documentation", [sys.executable, "scripts/check_docs.py"]),
    ]


@pytest.mark.parametrize("checker", ["check"], indirect=True)
@pytest.mark.parametrize("version", [None, "v20.19.0", "not a version"])
def test_missing_or_unsupported_node_has_an_actionable_error(checker, monkeypatch, version):
    monkeypatch.setattr(checker.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(checker.shutil, "which", lambda name: None if name == "node" and version is None else name)
    monkeypatch.setattr(checker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=version))
    with pytest.raises(ValueError, match="Node.js 22 or newer.*browser tests"):
        checker.preflight(docs_only=False, quick=True)


@pytest.mark.parametrize("checker", ["check"], indirect=True)
def test_runner_stops_at_first_failure_and_forces_sandbox(checker, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "preflight", lambda **kwargs: "node")
    monkeypatch.setattr(checker, "commands", lambda **kwargs: [("First", ["first"]), ("Never", ["never"])])
    monkeypatch.setenv("ALLOW_LIVE_PLAID", "true")
    monkeypatch.setenv("WHAB_ENV", "production")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(checker.subprocess, "run", run)
    assert checker.main(["--quick"]) == 7
    assert len(calls) == 1
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[0][1]["env"]["ALLOW_LIVE_PLAID"] == "false"
    assert calls[0][1]["env"]["WHAB_ENV"] == "sandbox"
    assert "First failed (exit 7)" in capsys.readouterr().err


@pytest.mark.parametrize("checker", ["check"], indirect=True)
def test_runner_scrubs_private_runtime_and_credentials_from_every_child(checker, monkeypatch, tmp_path):
    private = tmp_path / "household"
    private.mkdir()
    hostile = {
        "WHAB_HOME": str(private), "WHAB_BUDGET": str(private / "private.whab"),
        "WHAB_BUDGET_PASSWORD_ENV": "HOUSEHOLD_PASSWORD", "WHAB_BUDGET_COPIES": str(private / "copy.whab"),
        "WHAB_PLUGINS": "plaid,server", "WHAB_STORAGE": "client", "WHAB_ENV": "production",
        "PLAID_ENV": "production", "PLAID_CLIENT_ID": "private-client", "PLAID_SECRET": "private-secret",
        "SERVER_TOKEN": "private-server-token", "CLIENT_TOKEN": "private-client-token",
        "TOKEN_ENCRYPTION_KEY": "private-encryption-key", "ALLOW_LIVE_PLAID": "true", "PORT": "9000",
        "PYTHONPATH": str(private),
    }
    for name, value in hostile.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("COVERAGE_FILE", "preserved-generic-tool-setting")
    children = []

    def inspect_environment(env):
        home = Path(env["WHAB_HOME"])
        assert home != private and home.is_dir() and not tuple(home.iterdir())
        assert env["WHAB_ENV"] == env["PLAID_ENV"] == "sandbox"
        assert env["ALLOW_LIVE_PLAID"] == "false"
        assert all(name not in env for name in hostile if name not in {"WHAB_HOME", "WHAB_ENV", "PLAID_ENV", "ALLOW_LIVE_PLAID"})
        assert env["COVERAGE_FILE"] == "preserved-generic-tool-setting"
        children.append(home)

    def preflight(**kwargs):
        inspect_environment(kwargs["env"])
        return "node"

    def run(command, **kwargs):
        inspect_environment(kwargs["env"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(checker, "preflight", preflight)
    monkeypatch.setattr(checker, "commands", lambda **kwargs: [("First", ["first"]), ("Second", ["second"])])
    monkeypatch.setattr(checker.subprocess, "run", run)
    assert checker.main(["--quick"]) == 0
    assert len(children) == 3 and len(set(children)) == 1
    assert not children[0].exists()


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
def test_local_links_validate_paths_anchors_and_references_but_ignore_examples(checker, tmp_path):
    source = tmp_path / "README.md"
    target = tmp_path / "guide.md"
    texts = {
        source: "# Start\n[works](guide.md#same-1)\n[reference][usage]\n\n[usage]: guide.md#same\n"
        "`[inline example](missing.md)`\n```md\n[example](missing.md)\n```\n",
        target: "# Same\n## Same\n",
    }
    source.write_text(texts[source])
    target.write_text(texts[target])
    assert checker.validate_document(source, tmp_path, texts) == []
    texts[source] += "[bad](guide.md#unknown)\n[absent](gone.md)\n"
    errors = checker.validate_document(source, tmp_path, texts)
    assert len(errors) == 2
    assert "missing Markdown anchor: guide.md#unknown" in errors[0]
    assert "missing local link target: gone.md" in errors[1]


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
def test_fence_requires_matching_marker_and_length(checker):
    text, errors = checker.prose("# Start\n````markdown\n```\n[example](missing.md)\n")
    assert errors == ["2: unclosed code fence"]
    assert "missing.md" not in text
    assert checker.prose("~~~md\n```\n~~~\n")[1] == []


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
def test_heading_anchors_and_undefined_reference_diagnostics(checker, tmp_path):
    assert checker.anchors("# `API` &amp; rules\n\nNamed section\n=============\n") == {"api--rules", "named-section"}
    source = tmp_path / "README.md"
    assert checker.validate_document(source, tmp_path, {source: "[missing][unknown]\n"}) == [
        "1: undefined Markdown link reference: unknown",
    ]


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
def test_links_cannot_follow_symlinks_outside_repository(checker, tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    source = root / "README.md"
    private = tmp_path / "private.md"
    private.write_bytes(b"\xff private material must never be read")
    (root / "escape.md").symlink_to(private)
    errors = checker.validate_document(source, root, {source: "[escape](escape.md#secret)\n"})
    assert errors == ["1: local link escapes the repository: escape.md#secret"]


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
def test_source_directory_symlink_is_rejected_before_reading(checker, monkeypatch, tmp_path, capsys):
    root = tmp_path / "repository"
    (root / "whab").mkdir(parents=True)
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret.md").write_bytes(b"\xff private material must never be read")
    (root / "whab/docs").symlink_to(private, target_is_directory=True)
    monkeypatch.setattr(checker, "ROOT", root)
    monkeypatch.setattr(checker, "documentation_paths", lambda _: (root / "whab/docs/secret.md",))
    assert checker.main() == 1
    assert "must remain inside the repository" in capsys.readouterr().err


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
def test_tracked_directory_symlink_cannot_read_an_in_repository_private_runtime(checker, monkeypatch, tmp_path, capsys):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    document = tmp_path / "whab/docs/sample.md"
    document.parent.mkdir(parents=True)
    document.write_text("# Source documentation\n")
    subprocess.run(["git", "add", "--", "whab/docs/sample.md"], cwd=tmp_path, check=True)
    document.unlink()
    document.parent.rmdir()
    private = tmp_path / "whab/app/budgets/private"
    private.mkdir(parents=True)
    (private / "sample.md").write_text("[SENSITIVE_INVALID_LINK](sensitive-household-path)\n")
    document.parent.symlink_to(private, target_is_directory=True)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert not path.resolve().is_relative_to(private), "Must not open private runtime material"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    assert checker.main() == 1
    output = capsys.readouterr().err
    assert "symlink components" in output
    assert "SENSITIVE_INVALID_LINK" not in output and "sensitive-household-path" not in output


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
def test_discovery_includes_new_source_docs_and_ignores_private_or_generated_files(checker, tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    names = [
        "README.md", "whab/docs/new.md", "whab/app/plugins/interface/web/docs/behavior.md",
        ".github/pull_request_template.md", "whab/launchers/macos/manager/README.md",
    ]
    excluded = ["branding/README.md", "whab/app/budgets/private.md", "whab/app/.venv/README.md", "whab/app/plugins/interface/web/src/generated.md"]
    for name in names + excluded:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Fictional\n")
    assert {path.relative_to(tmp_path).as_posix() for path in checker.documentation_paths(tmp_path)} == set(names)


def inventory_fixture(root):
    application = root / "whab/app"
    application.mkdir(parents=True)
    (application / "pyproject.toml").write_text(
        '[project.optional-dependencies]\nbundled = ["whab_plugin_web>=1,<2"]\n'
    )
    plugin = application / "plugins/interface/web"
    plugin.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname = "whab_plugin_web"\n[project.entry-points."whab.plugins"]\nweb = "web:plugin"\n'
    )
    inventory = application / "plugins/README.md"
    inventory.write_text(
        "# Plugins\n\n## Supported plugins\n\n| Plugin ID | Distribution | Default enablement |\n"
        "| --- | --- | --- |\n| `web` | `whab_plugin_web` | Selected |\n\n"
        "## Teaching example\n\n| `example` | `whab_plugin_example` | Developer only |\n"
    )
    return inventory


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
def test_inventory_compares_metadata_without_count_prose_or_teaching_example(checker, tmp_path):
    inventory = inventory_fixture(tmp_path)
    assert checker.validate_inventory(tmp_path) == []
    inventory.write_text(inventory.read_text().replace("`web` | `whab_plugin_web`", "`web` | `wrong_package`"))
    assert "differs from bundled metadata" in checker.validate_inventory(tmp_path)[0]


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
def test_inventory_rejects_bundled_package_without_local_metadata(checker, tmp_path):
    inventory_fixture(tmp_path)
    metadata = tmp_path / "whab/app/pyproject.toml"
    metadata.write_text('[project.optional-dependencies]\nbundled = ["whab_plugin_unknown>=1"]\n')
    assert "do not match available supported plugin metadata" in checker.validate_inventory(tmp_path)[0]


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
@pytest.mark.parametrize("kind", ["host-metadata", "plugin-directory", "inventory"])
def test_inventory_rejects_symlinked_metadata_or_docs_before_reading(checker, monkeypatch, tmp_path, kind):
    inventory = inventory_fixture(tmp_path)
    private = tmp_path / "whab/app/budgets/private"
    private.mkdir(parents=True)
    secret = private / ("README.md" if kind == "inventory" else "pyproject.toml")
    secret.write_text("SENSITIVE INVALID METADATA MUST NOT BE READ")
    if kind == "plugin-directory":
        plugin = tmp_path / "whab/app/plugins/interface/web"
        (plugin / "pyproject.toml").unlink()
        plugin.rmdir()
        plugin.symlink_to(private, target_is_directory=True)
    else:
        path = inventory if kind == "inventory" else tmp_path / "whab/app/pyproject.toml"
        path.unlink()
        path.symlink_to(secret)
    read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert not path.resolve().is_relative_to(private), "Must not open private metadata"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    with pytest.raises(ValueError, match="symlink components"):
        checker.validate_inventory(tmp_path)


@pytest.mark.parametrize("checker", ["check_docs"], indirect=True)
def test_untracked_private_documents_are_never_opened(checker, monkeypatch, tmp_path):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    inventory_fixture(tmp_path)
    (tmp_path / "README.md").write_text("# Source\n")
    private_names = [
        "whab/app/budgets/private.md", "branding/README.md",
        "whab/app/plugins/interface/web/src/generated.md",
    ]
    private_paths = []
    for name in private_names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff PRIVATE MATERIAL MUST NOT BE READ")
        private_paths.append(path)
    read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path not in private_paths, "Must not open private or generated material"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    assert checker.main() == 0
    with pytest.raises(ValueError, match="outside private/generated paths"):
        checker.read_source_text(private_paths[0], tmp_path)
