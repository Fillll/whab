"""PR speedups must preserve complete regressions and reject incomplete evidence."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def checker(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("whab_pr_checks", scripts / "check_pr.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("paths", "application", "launcher"), [
    (["whab/docs/usage.md"], False, False),
    (["whab/app/plugins/interface/web/README.md"], False, False),
    (["whab/launchers/macos/Sources/Main.swift"], False, True),
    (["whab/app/src/whab/cli.py"], True, True),
    (["whab/app/pyproject.toml"], True, True),
    (["whab/app/plugins/interface/web/src/whab_plugin_web/static/styles.css"], True, False),
    (["whab/app/scripts/check_pr.py"], True, False),
    (["unknown/new-contract"], True, False),
    ([], True, False),
])
def test_only_known_boundaries_skip_source_regressions(checker, paths, application, launcher):
    assert checker.change_plan(paths) == {"application": application, "launcher": launcher}


def manifests(checker):
    from pr_shard import partition

    collected = [f"test_rules.py::test_rule_{n}" for n in range(90)]
    identity = {"revision": "a" * 40, "python": [3, 14]}
    rows = [{"index": index, "count": 3, "identity": identity, "collected": collected,
             "selected": partition(collected, index, 3)} for index in range(3)]
    for row in rows:
        row["executed"] = len(row["selected"])
    return rows, identity


def test_all_tests_are_accounted_for_exactly_once(checker):
    rows, identity = manifests(checker)
    assert checker.verify_partition(rows, 3, identity) == 90
    assert set.union(*(set(row["selected"]) for row in rows)) == set(rows[0]["collected"])
    assert sum(len(row["selected"]) for row in rows) == 90


@pytest.mark.parametrize("fault", ["missing", "duplicate", "collection", "source", "python", "omitted", "extra", "unexecuted", "count"])
def test_missing_mismatched_or_incomplete_shards_fail_closed(checker, fault):
    rows, identity = manifests(checker)
    rows = copy.deepcopy(rows)
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows[1] = rows[0]
    elif fault == "collection":
        rows[1]["collected"] = rows[1]["collected"][:-1]
    elif fault in {"source", "python"}:
        rows[1]["identity"]["revision" if fault == "source" else "python"] = "different"
    elif fault == "omitted":
        rows[1]["selected"].pop()
        rows[1]["executed"] -= 1
    elif fault == "extra":
        rows[1]["selected"].append("unselected.py::test_other")
        rows[1]["executed"] += 1
    elif fault == "unexecuted":
        rows[1]["executed"] -= 1
    else:
        rows[1]["count"] = 4
    with pytest.raises(ValueError):
        checker.verify_partition(rows, 3, identity)


def test_unmeasured_shard_cannot_pass_test_success(checker, tmp_path):
    rows, _ = manifests(checker)
    for row in rows:
        row["identity"] = checker.source_identity()
        target = tmp_path / str(row["index"])
        target.mkdir()
        (target / "manifest.json").write_text(json.dumps(row))
    with pytest.raises(ValueError, match="no coverage data"):
        checker.aggregate(tmp_path, 3, {})


@pytest.mark.parametrize("attribute", ["failures", "errors", "skipped"])
def test_passing_only_a_subset_or_skipping_regressions_fails(checker, tmp_path, attribute):
    xml = tmp_path / "tests.xml"
    xml.write_text(f'<testsuites><testsuite tests="2" {attribute}="1"/></testsuites>')
    with pytest.raises(ValueError, match="without omitted"):
        checker.test_counts(xml)


def test_real_pytest_collections_form_a_complete_partition(checker, tmp_path):
    source = tmp_path / "test_fictional.py"
    source.write_text("import pytest\n@pytest.mark.parametrize('n', range(12))\ndef test_money(n):\n    assert n >= 0\n")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    rows = []
    for index in range(3):
        output = tmp_path / str(index)
        result = subprocess.run([
            sys.executable, "-m", "pytest", "-c", str(config), str(source), "-q",
            "-p", "scripts.pr_shard", "--pr-shard-index", str(index), "--pr-shard-count", "3",
            "--pr-collection-dir", str(output), f"--junitxml={output / 'tests.xml'}",
        ], cwd=checker.ROOT, env=checker.check_environment(tmp_path), capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        row = json.loads((output / "main.json").read_text())
        rows.append({**row, "identity": {}, "executed": checker.test_counts(output / "tests.xml")})
    assert checker.verify_partition(rows, 3, {}) == 12


def test_local_iteration_does_not_substitute_for_authoritative_ci(checker):
    assert "tests/plugins/test_contracts.py" in checker.focused_paths(["whab/app/src/whab/plugins/contracts.py"])
    selected = checker.focused_paths(["whab/app/plugins/interface/web/src/whab_plugin_web/views.py"])
    assert "plugins/interface/web/tests" in selected and "tests/web" in selected


def test_parallel_workers_own_separate_default_runtime_homes(checker, monkeypatch, tmp_path):
    import pr_shard

    for worker in ("gw0", "gw1"):
        monkeypatch.setenv("WHAB_HOME", str(tmp_path))
        pr_shard.pytest_configure(SimpleNamespace(workerinput={"workerid": worker}))
        assert Path(pr_shard.os.environ["WHAB_HOME"]) == tmp_path / worker
        assert (tmp_path / worker).is_dir()


def test_certification_is_separate_and_existing_required_contexts_remain():
    root = Path(__file__).resolve().parents[3]
    ci = (root / ".github/workflows/ci.yml").read_text()
    certification = (root / ".github/workflows/certification.yml").read_text()
    assert "name: CI (Python ${{ matrix.python-version }})" in ci
    assert "if: always()" in ci and "coverage==7.16.2" in ci
    assert "check_pr.py aggregate --count 8" in ci and "include-hidden-files: true" in ci
    assert "check_plugin_generation.py" not in ci and "plugin-conformance.yml" not in ci
    assert "pull_request:" not in certification
    assert "schedule:" in certification and "workflow_dispatch:" in certification and "branches: [main]" in certification
    assert 'python scripts/check.py --evidence-dir' in certification
