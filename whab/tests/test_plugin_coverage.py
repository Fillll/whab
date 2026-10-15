"""Coverage policy rejects unmeasured plugins and gaps hidden by summary rounding."""

from importlib import util
from pathlib import Path

import pytest


@pytest.fixture
def gate(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_plugin_coverage.py"
    spec = util.spec_from_file_location("whab_coverage_gate", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "plugins" / "new-plugin" / "src"
    root.mkdir(parents=True)
    (root / "implementation.py").write_text("VALUE = 1\n")
    return root


def report(*, missing_lines=0, missing_branches=0):
    return {
        "meta": {"branch_coverage": True},
        "files": {
            "plugins/new-plugin/src/implementation.py": {
                "summary": {"missing_lines": missing_lines, "missing_branches": missing_branches}
            }
        },
        "totals": {"percent_covered": 100},
    }


def test_complete_source_passes(gate, source):
    gate.validate_report(report(), (source,))


def test_an_unimported_new_file_cannot_disappear_from_the_gate(gate, source):
    (source / "unimported.py").write_text("def new_behavior():\n    return 42\n")
    with pytest.raises(ValueError, match="omitted.*unimported.py"):
        gate.validate_report(report(), (source,))


@pytest.mark.parametrize("gap", ["missing_lines", "missing_branches"])
def test_missing_code_fails_even_if_total_rounds_to_100(gate, source, gap):
    with pytest.raises(ValueError, match="below 100%.*implementation.py"):
        gate.validate_report(report(**{gap: 1}), (source,))


def test_statement_only_measurement_does_not_satisfy_the_policy(gate, source):
    measured = report()
    measured["meta"]["branch_coverage"] = False
    with pytest.raises(ValueError, match="must measure branches"):
        gate.validate_report(measured, (source,))


def test_empty_source_does_not_report_success(gate):
    with pytest.raises(ValueError, match="No plugin Python source"):
        gate.validate_report(report(), ())
