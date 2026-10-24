"""Owned processes inherit standard settings, never fictional caller secrets."""

import os
import subprocess

import pytest

from whab.plugins.process_environment import _STANDARD, process_environment
from whab.plugins.report_worker import ReportWorker
from whab.plugins.storage_worker import StorageWorker


def test_process_policy_keeps_only_named_standard_variables_and_excludes_password_alias(monkeypatch):
    standard = {name: "fictional-standard" for name in _STANDARD}
    private = {"EXAMPLE_REMOTE_CLIENT_URL": "https://fictional.example/ledger",
               "EXAMPLE_REMOTE_CLIENT_BUDGET_ID": "fictional-budget",
               "EXAMPLE_REMOTE_CLIENT_TOKEN": "fictional-token",
               "CUSTOM_UNLOCK": "fictional-password", "WHAB_BUDGET_PASSWORD_ENV": "CUSTOM_UNLOCK",
               "HOME": "/fictional/home", "HTTP_PROXY": "https://fictional.example/proxy",
               "PYTHONPATH": "/fictional/python", "PIP_INDEX_URL": "https://fictional.example/index",
               "LC_CUSTOM": "fictional-configuration"}
    with monkeypatch.context() as scoped:
        scoped.setattr(os, "environ", {**standard, **private})
        assert process_environment() == standard
        os.environ["WHAB_BUDGET_PASSWORD_ENV"] = "LANG"
        assert process_environment() == {name: value for name, value in standard.items() if name != "LANG"}


@pytest.mark.parametrize("worker_type,identifier", [(ReportWorker, "example"), (StorageWorker, "sqlite"), (StorageWorker, "json")])
def test_actual_workers_exclude_all_custom_configuration_and_reap_after_readiness(monkeypatch, tmp_path, worker_type, identifier):
    private = {
        "EXAMPLE_REMOTE_CLIENT_URL": "https://fictional.example/ledger",
        "EXAMPLE_REMOTE_CLIENT_BUDGET_ID": "fictional-budget",
        "EXAMPLE_REMOTE_CLIENT_TOKEN": "fictional-token",
        "WHAB_BUDGET_PASSWORD_ENV": "CUSTOM_UNLOCK", "CUSTOM_UNLOCK": "fictional-password",
        "HTTP_PROXY": "https://fictional.example/proxy", "SSL_CERT_FILE": str(tmp_path / "fictional-ca"),
        "HOME": str(tmp_path / "fictional-home"), "PYTHONPATH": str(tmp_path / "fictional-python"),
        "PIP_CONFIG_FILE": str(tmp_path / "fictional-pip"), "LC_CUSTOM": "fictional-configuration",
    }
    for name, value in private.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    original = subprocess.Popen
    observed = []

    def spawn(command, **options):
        environment = options["env"]
        assert not private.keys() & environment.keys()
        assert not set(private.values()) & set(environment.values())
        assert environment["LANG"] == environment["LC_ALL"] == "C"
        assert environment["TMPDIR"] == str(tmp_path)
        observed.append(command)
        return original(command, **options)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    worker = worker_type(identifier)
    try:
        assert worker.healthy and worker.info["plugin"] == identifier
        assert len(observed) == 1
    finally:
        worker.close()
    assert not worker.healthy and not worker.cleanup_pending
