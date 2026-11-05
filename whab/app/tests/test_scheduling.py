"""Generate private launchd tasks without registering a real OS job in tests."""

import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from whab import scheduling


def test_launchd_job_is_hourly_private_and_reversible(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduling.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls = []
    def run(arguments, **kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(scheduling.subprocess, "run", run)
    home = tmp_path / "runtime with spaces"
    assert scheduling.configure_schedule(home, "sandbox", enabled=True)["enabled"]
    path, = (tmp_path / "Library/LaunchAgents").iterdir()
    job = plistlib.loads(path.read_bytes())
    assert job["StartCalendarInterval"] == {"Minute": 0}
    assert job["ProgramArguments"][-1] == "--run-jobs"
    assert str(home) in job["ProgramArguments"]
    assert "KeepAlive" not in job and path.stat().st_mode & 0o077 == 0
    assert scheduling.configure_schedule(home, "sandbox", enabled=False)["enabled"] is False
    assert not path.exists()
    assert len(calls) == 3


def test_launchd_failure_cannot_report_success(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduling.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(scheduling.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(OSError, match="could not be registered"):
        scheduling.configure_schedule(tmp_path / "runtime", "sandbox", enabled=True)
    assert not list(tmp_path.glob("runtime/run/*/schedule.json"))


def test_unsupported_platform_has_explicit_manual_entry_point(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduling.sys, "platform", "linux")
    with pytest.raises(OSError, match="--run-jobs"):
        scheduling.configure_schedule(tmp_path, "sandbox", enabled=True)
