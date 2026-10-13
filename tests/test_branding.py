"""Rebranding must provide the WHAB launch command and preserve saved budgets."""

import subprocess
import sys

from whab import __version__, config
from whab.config import Settings
from whab.store import Store


def test_whab_launch_command():
    result = subprocess.run(
        [sys.executable, "-m", "whab", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == f"WHAB {__version__}"


def test_rebrand_opens_existing_budget(monkeypatch, tmp_path):
    legacy_path = tmp_path / "data" / "ihab-sandbox.sqlite"
    existing = Store(legacy_path, month="2026-10")
    existing.add_account(
        "personal", {"name": "Saved checking", "type": "depository", "balance": 12345}
    )
    existing.close()
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PORT", "5173")
    settings = Settings.load()
    reopened = Store(settings.database_path, month="2026-10")
    try:
        accounts = reopened.accounts("personal")
        assert len(accounts) == 1
        assert accounts[0]["name"] == "Saved checking"
        assert accounts[0]["balance"] == 12345
        assert not (tmp_path / "data" / "whab-sandbox.sqlite").exists()
    finally:
        reopened.close()
