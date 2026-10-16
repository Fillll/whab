"""Portable-budget CLI arguments configure real isolated data without opening a listener."""

import os
import sys

import pytest

from tests.budgets.helpers import decode_budget as decode_bundle
from whab import __main__ as cli
from whab.app import create_app
from whab.config import Settings


@pytest.fixture
def launch(monkeypatch, tmp_path):
    # main() deliberately updates these directly for the child server process.
    # Register their original values even when absent so teardown owns mutations.
    for key in ("WHAB_BUDGET", "WHAB_BUDGET_COPIES", "WHAB_BUDGET_PASSWORD_ENV", "PORT"):
        monkeypatch.setenv(key, os.environ.get(key, ""))
    for key in (
        "WHAB_BUDGET", "WHAB_BUDGET_COPIES", "PLAID_ENV", "PORT",
        "PLAID_CLIENT_ID", "PLAID_SECRET", "TOKEN_ENCRYPTION_KEY",
        "WHAB_BUDGET_PASSWORD_ENV", "FIXTURE_BUDGET_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WHAB_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("WHAB_ENV", "sandbox")
    monkeypatch.setenv("WHAB_STORAGE", "sqlite")
    monkeypatch.setenv("WHAB_UI", "")
    monkeypatch.setenv("WHAB_PLUGINS", "")
    monkeypatch.setenv("ALLOW_LIVE_PLAID", "false")
    calls = []

    def no_listener(target, **options):
        calls.append((target, options, Settings.load()))

    monkeypatch.setattr(cli.uvicorn, "run", no_listener)
    # These cases test budget argument behavior; real TLS validation has its own tests.
    monkeypatch.setattr(cli, "validate_tls", lambda *_: None)

    def invoke(*arguments):
        monkeypatch.setattr(sys, "argv", ["whab", *map(str, arguments)])
        cli.main()
        return calls

    return invoke, calls


def test_create_writes_named_data_bundle_and_launches_its_path(launch, tmp_path):
    invoke, calls = launch
    path = tmp_path / "Household.whab"
    invoke("--budget", path, "--create", "Household", "--port", "5174")
    manifest, ledger = decode_bundle(path.read_bytes())
    assert manifest["name"] == "Household"
    assert manifest["storage"] == "sqlite"
    assert ledger["accounts"] == [] and ledger["transactions"] == []
    target, options, settings = calls[0]
    assert target == "whab.app:create_app"
    assert options["factory"] is True and options["host"] == "127.0.0.1"
    assert options["port"] == 5174 and options["reload"] is False
    assert settings.budget_path == path.resolve()
    assert settings.plugins == () and settings.allow_live is False


def test_create_applies_explicit_port_before_loading_existing_environment(launch, tmp_path, monkeypatch):
    invoke, calls = launch
    monkeypatch.setenv("PORT", "80")
    invoke("--budget", tmp_path / "Budget.whab", "--create", "Budget", "--port", "5174")
    assert calls[-1][2].port == 5174


def test_open_and_repeatable_copy_options_save_the_same_budget(launch, tmp_path):
    invoke, calls = launch
    path = tmp_path / "Household.whab"
    invoke("--budget", path, "--create", "Household")
    first = tmp_path / "copy one.whab"
    second = tmp_path / "copy two.whab"
    invoke("--budget", path, "--copy", first, "--copy", second, "--reload")
    _, options, settings = calls[-1]
    assert options["reload"] is True
    assert settings.copy_paths == (first, second)
    assert os.environ["WHAB_BUDGET_COPIES"] == os.pathsep.join(map(str, (first, second)))
    app = create_app(settings=settings)
    try:
        original = decode_bundle(path.read_bytes())
        assert decode_bundle(first.read_bytes()) == original
        assert decode_bundle(second.read_bytes()) == original
        assert app.state.budget_session.name == "Household"
    finally:
        app.state.store.close()
    # A supervisor or ordinary repeated launch can keep the same --copy flags.
    reopened = create_app(settings=settings)
    try:
        assert len(reopened.state.budget_session.destinations) == 2
        assert decode_bundle(first.read_bytes()) == decode_bundle(path.read_bytes())
    finally:
        reopened.state.store.close()


def test_relative_budget_and_copy_paths_resolve_from_launch_directory(launch, tmp_path, monkeypatch):
    invoke, calls = launch
    monkeypatch.chdir(tmp_path)
    invoke("--budget", "Household.whab", "--copy", "copies/backup.whab")
    settings = calls[0][2]
    assert settings.budget_path == tmp_path / "Household.whab"
    assert settings.copy_paths == (tmp_path / "copies" / "backup.whab",)


@pytest.mark.parametrize("arguments", [("--create", "Household"), ("--copy", "backup.whab")])
def test_create_and_copy_require_an_explicit_budget(launch, arguments, capsys, tmp_path):
    invoke, calls = launch
    with pytest.raises(SystemExit) as error:
        invoke(*arguments)
    assert error.value.code == 2
    assert "Use --budget FILE" in capsys.readouterr().err
    assert not calls and not (tmp_path / "runtime").exists()


def test_create_never_overwrites_an_existing_file(launch, tmp_path, capsys):
    invoke, calls = launch
    path = tmp_path / "Household.whab"
    path.write_bytes(b"existing document")
    with pytest.raises(SystemExit) as error:
        invoke("--budget", path, "--create", "Replacement")
    assert error.value.code == 2
    assert "unused file location" in capsys.readouterr().err
    assert path.read_bytes() == b"existing document"
    assert not calls and not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("port", ["80", "65536"])
def test_invalid_port_does_not_create_a_budget(launch, tmp_path, capsys, port):
    invoke, calls = launch
    path = tmp_path / "Household.whab"
    with pytest.raises(SystemExit) as error:
        invoke("--budget", path, "--create", "Household", "--port", port)
    assert error.value.code == 2
    assert "Port must be between" in capsys.readouterr().err
    assert not path.exists() and not (tmp_path / "runtime").exists()
    assert not calls


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_create_name_fails_before_any_files_are_created(launch, tmp_path, capsys, name):
    invoke, calls = launch
    path = tmp_path / "Household.whab"
    with pytest.raises(SystemExit) as error:
        invoke("--budget", path, "--create", name)
    assert error.value.code == 2
    assert "nonempty name" in capsys.readouterr().err
    assert not path.exists() and not (tmp_path / "runtime").exists()
    assert not calls


def test_budget_password_comes_from_named_environment_variable_and_is_not_in_repr(launch, tmp_path, monkeypatch):
    invoke, calls = launch
    secret = "synthetic secret budget password"
    monkeypatch.setenv("FIXTURE_BUDGET_PASSWORD", secret)
    invoke("--budget", tmp_path / "Reference.whab", "--budget-password-env", "FIXTURE_BUDGET_PASSWORD")
    settings = calls[-1][2]
    assert settings.budget_password == secret
    assert secret not in repr(settings)
    assert os.environ["WHAB_BUDGET_PASSWORD_ENV"] == "FIXTURE_BUDGET_PASSWORD"


def test_budget_password_environment_is_not_read_without_explicit_selection(launch, monkeypatch):
    _, _calls = launch
    monkeypatch.setenv("FIXTURE_BUDGET_PASSWORD", "synthetic unused password")
    assert Settings.load().budget_password is None


@pytest.mark.parametrize("arguments", [
    ("--budget-password-env", "FIXTURE_BUDGET_PASSWORD"),
    ("--budget", "Reference.whab", "--budget-password-env", "FIXTURE_BUDGET_PASSWORD"),
])
def test_password_flag_requires_explicit_budget_and_existing_variable(launch, arguments):
    invoke, calls = launch
    with pytest.raises(SystemExit) as error:
        invoke(*arguments)
    assert error.value.code == 2 and calls == []


def test_missing_explicit_password_environment_variable_is_actionable(launch, monkeypatch):
    monkeypatch.setenv("WHAB_BUDGET_PASSWORD_ENV", "FIXTURE_BUDGET_PASSWORD")
    with pytest.raises(ValueError, match="not set"):
        Settings.load()
