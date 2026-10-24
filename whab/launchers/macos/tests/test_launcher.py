"""Isolated launcher safety tests. These never open a user's WHAB runtime."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

MANAGER_FILE = Path(__file__).resolve().parents[1] / "manager/launcher.py"
SPEC = importlib.util.spec_from_file_location("whab_launcher", MANAGER_FILE)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)
SHA = "a" * 40
OTHER_SHA = "b" * 40
AVAILABLE_PORT = launcher.available_port


class IsolatedManagerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.home = self.base / "runtime"
        self.root = self.base / "launcher"
        self.manager = launcher.Manager(self.root, python=sys.executable)
        self.port_check = patch.object(launcher, "available_port")
        self.port_check.start()
        self.addCleanup(self.port_check.stop)

    def config(self, text=None):
        self.home.mkdir(exist_ok=True, mode=0o700)
        if text is not None:
            path = self.home / ".env"
            path.write_text(text)
            path.chmod(0o600)
        with patch.object(self.manager, "verify_python", return_value=sys.executable):
            return self.manager.configure(runtime_home=self.home)

    def installation(self, sha=SHA, version="2.3.0"):
        return {"build": sha, "version": version, "python": sys.executable,
                "app": str(self.base / "app"), "directory": str(self.root / "versions" / sha)}

    def test_configuration_requires_explicit_first_action(self):
        self.assertEqual(self.manager.status()["state"], "unconfigured")
        with self.assertRaisesRegex(launcher.LauncherError, "Choose a new runtime"):
            self.manager.start(offline=True)
        self.assertFalse(self.home.exists())

    def test_adoption_preserves_env_and_port(self):
        original = "PORT=6412\nTOKEN_ENCRYPTION_KEY=private-test-secret\n"
        status = self.config(original)
        self.assertEqual(status["url"], "https://localhost:6412")
        self.assertEqual((self.home / ".env").read_text(), original)
        self.assertEqual((self.root / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("private-test-secret", json.dumps(status))

    def test_alpha_configuration_preserves_existing_default(self):
        self.config()
        self.assertEqual(self.manager.config()["channel"], "main")
        with patch.object(self.manager, "verify_python", return_value=sys.executable):
            status = self.manager.configure(channel="alpha")
            self.manager.configure()
        self.assertEqual(status["channel"], "alpha")
        self.assertEqual(self.manager.config()["channel"], "alpha")
        self.assertFalse(status["tested"])

    def test_invalid_channel_never_changes_existing_configuration(self):
        self.config()
        original = (self.root / "config.json").read_bytes()
        for channel in ("", "unchecked", "MAIN"):
            with self.subTest(channel=channel), self.assertRaisesRegex(launcher.LauncherError, "supported WHAB update channel"):
                self.manager.configure(channel=channel)
            self.assertEqual((self.root / "config.json").read_bytes(), original)

    def test_switching_channels_discards_previous_channel_update_target(self):
        self.config()
        for source, destination, tested in (("main", "alpha", True), ("alpha", "main", False)):
            launcher.write_json(self.root / "update.json", {
                "channel": source, "target": {"build": OTHER_SHA, "version": "new", "channel": source, "tested": tested},
            })
            with patch.object(self.manager, "verify_python", return_value=sys.executable):
                status = self.manager.configure(channel=destination)
            self.assertFalse((self.root / "update.json").exists())
            self.assertFalse(status["update_available"])
            self.assertIsNone(status["latest_build"])
            self.assertIsNone(status["latest_version"])

    def test_status_ignores_a_cached_target_from_another_channel(self):
        self.config()
        launcher.write_json(self.root / "update.json", {
            "channel": "alpha", "warning": "stale warning",
            "target": {"build": OTHER_SHA, "version": "new", "channel": "alpha", "tested": False},
        })
        status = self.manager.status()
        self.assertFalse(status["update_available"])
        self.assertIsNone(status["latest_build"])
        self.assertIsNone(status["latest_version"])
        self.assertIsNone(status["update_warning"])

    def test_channel_selection_does_not_relabel_an_installed_alpha_build_as_tested(self):
        self.config()
        installation = self.installation() | {"channel": "alpha", "tested": False}
        launcher.write_json(self.root / "active.json", installation)
        status = self.manager.status()
        self.assertEqual(status["channel"], "main")
        self.assertEqual(status["active_channel"], "alpha")
        self.assertFalse(status["tested"])

    def test_check_update_records_exact_alpha_head_without_installing(self):
        self.config()
        with patch.object(self.manager, "verify_python", return_value=sys.executable):
            self.manager.configure(channel="alpha")
        with patch.object(launcher, "request_json", return_value={"sha": OTHER_SHA}) as request, patch.object(self.manager, "prepare") as prepare:
            status = self.manager.check_update()
        request.assert_called_once_with(f"{launcher.API}/commits/main")
        prepare.assert_not_called()
        self.assertTrue(status["update_available"])
        self.assertEqual(status["latest_build"], OTHER_SHA)
        self.assertEqual(status["latest_version"], OTHER_SHA[:12])
        self.assertFalse(launcher.read_json(self.root / "update.json")["target"]["tested"])

    def test_cached_installation_refreshes_provenance_only_from_resolved_target(self):
        self.config()
        installation = self.installation() | {"channel": "alpha", "tested": False}
        manifest_path = Path(installation["directory"]) / "installation.json"
        launcher.write_json(manifest_path, installation)
        for channel, tested in (("main", True), ("alpha", False)):
            target = {"build": SHA, "channel": channel, "tested": tested}
            with self.subTest(channel=channel), patch.object(launcher, "download_source") as download, patch.object(self.manager, "smoke") as smoke:
                candidate = self.manager.prepare(target)
            self.assertEqual(candidate["channel"], channel)
            self.assertEqual(candidate["tested"], tested)
            self.assertEqual(launcher.read_json(manifest_path), candidate)
            download.assert_not_called()
            smoke.assert_not_called()

    def test_online_start_refreshes_same_revision_provenance_without_reinstalling(self):
        self.config()
        installation = self.installation() | {"channel": "alpha", "tested": False}
        launcher.write_json(self.root / "active.json", installation)
        launcher.write_json(Path(installation["directory"]) / "installation.json", installation)
        target = {"build": SHA, "version": SHA[:12], "channel": "main", "tested": True}
        with patch.object(launcher, "latest_target", return_value=target), patch.object(self.manager, "launch") as launch, patch.object(self.manager, "backup") as backup, patch.object(self.manager, "run") as run:
            status = self.manager.start()
        self.assertTrue(status["tested"])
        self.assertEqual(status["active_channel"], "main")
        self.assertEqual(self.manager.active()["build"], SHA)
        launch.assert_called_once()
        run.assert_not_called()
        backup.assert_not_called()

    def test_alpha_candidate_still_requires_local_installation_and_smoke_checks(self):
        self.config()
        target = {"build": SHA, "channel": "alpha", "tested": False}
        def source(_sha, destination):
            app = destination / "whab/app"
            (app / "src/whab").mkdir(parents=True)
            (app / "src/whab/__init__.py").write_text('__version__ = "2.7.0"\n')
            for package in [app, *(app / "plugins" / name for name in launcher.PLUGINS)]:
                package.mkdir(parents=True, exist_ok=True)
                (package / "pyproject.toml").write_text("[project]\n")
        with patch.object(launcher, "download_source", side_effect=source), patch.object(self.manager, "stable_python", return_value=sys.executable), patch.object(self.manager, "run") as run, patch.object(self.manager, "smoke") as smoke:
            installation = self.manager.prepare(target)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args.args[0][-2:], ["pip", "check"])
        smoke.assert_called_once_with(installation)
        self.assertEqual(installation["channel"], "alpha")
        self.assertFalse(installation["tested"])

    def test_new_maintained_plugins_are_installed_but_examples_are_excluded(self):
        self.config()
        target = {"build": SHA, "channel": "alpha", "tested": False}

        def source(_sha, destination):
            app = destination / "whab/app"
            (app / "src/whab").mkdir(parents=True)
            (app / "src/whab/__init__.py").write_text('__version__ = "2.19.0"\n')
            for package in [app, *(app / "plugins" / name for name in launcher.PLUGINS),
                            app / "plugins/automation/random-forest-categorization",
                            app / "plugins/example/teaching"]:
                package.mkdir(parents=True, exist_ok=True)
                (package / "pyproject.toml").write_text("[project]\n")

        with patch.object(launcher, "download_source", side_effect=source), patch.object(self.manager, "stable_python", return_value=sys.executable), patch.object(self.manager, "run") as run, patch.object(self.manager, "smoke"):
            self.manager.prepare(target)
        install = [str(item) for item in run.call_args_list[1].args[0]]
        self.assertTrue(any(item.endswith("plugins/automation/random-forest-categorization") for item in install))
        self.assertFalse(any("plugins/example/" in item for item in install))

    def test_failed_alpha_local_smoke_preserves_previous_installation(self):
        self.config()
        old = self.installation()
        launcher.write_json(self.root / "active.json", old)
        target = {"build": OTHER_SHA, "channel": "alpha", "tested": False}
        def source(_sha, destination):
            app = destination / "whab/app"
            (app / "src/whab").mkdir(parents=True)
            (app / "src/whab/__init__.py").write_text('__version__ = "2.7.0"\n')
            for package in [app, *(app / "plugins" / name for name in launcher.PLUGINS)]:
                package.mkdir(parents=True, exist_ok=True)
                (package / "pyproject.toml").write_text("[project]\n")
        with patch.object(self.manager, "check_update", side_effect=lambda: launcher.write_json(self.root / "update.json", {"target": target})), patch.object(launcher, "download_source", side_effect=source), patch.object(self.manager, "stable_python", return_value=sys.executable), patch.object(self.manager, "run"), patch.object(self.manager, "smoke", side_effect=launcher.LauncherError("smoke_failed", "candidate not ready")), patch.object(self.manager, "stop") as stop:
            with self.assertRaisesRegex(launcher.LauncherError, "candidate not ready"):
                self.manager.update()
        stop.assert_not_called()
        self.assertEqual(self.manager.active(), old)
        self.assertFalse((self.root / "versions" / OTHER_SHA).exists())

    def test_adoption_rejects_different_port_without_rewriting_env(self):
        self.config("PORT=6412\n")
        with self.assertRaisesRegex(launcher.LauncherError, "differs"):
            self.manager.configure(port=6413)
        self.assertEqual((self.home / ".env").read_text(), "PORT=6412\n")

    def test_runtime_and_installation_cannot_overlap(self):
        with self.assertRaisesRegex(launcher.LauncherError, "separate"):
            self.manager.configure(runtime_home=self.root / "runtime")

    def test_env_must_be_private(self):
        self.config("PORT=6412\n")
        (self.home / ".env").chmod(0o644)
        with self.assertRaisesRegex(launcher.LauncherError, "private regular file"):
            launcher.environment_values(self.home)

    def test_dangling_env_link_is_never_used_to_create_external_configuration(self):
        self.home.mkdir()
        external = self.base / "missing-private.env"
        (self.home / ".env").symlink_to(external)
        with self.assertRaisesRegex(launcher.LauncherError, "existing private regular file"):
            self.manager.configure(runtime_home=self.home)
        self.assertFalse(external.exists())

    def test_mixed_source_runtime_and_explicit_env_link_are_supported(self):
        self.home.mkdir()
        env = self.base / "explicitly-adopted.env"
        env.write_text("PORT=6412\nTOKEN_ENCRYPTION_KEY=do-not-lose\n")
        env.chmod(0o600)
        (self.home / ".env").symlink_to(env)
        (self.home / "app").mkdir()
        (self.home / "app/source.py").write_text("source only")
        (self.home / ".venv").symlink_to(self.base / "nonexistent-old-venv")
        (self.home / "data").mkdir()
        (self.home / "data/whab-sandbox.sqlite").write_bytes(b"private ledger")
        with patch.object(self.manager, "verify_python", return_value=sys.executable):
            self.manager.configure(runtime_home=self.home)
        backup = self.manager.backup(self.home, self.installation())
        copied = Path(backup["directory"]) / "runtime"
        self.assertFalse((copied / ".env").is_symlink())
        self.assertEqual((copied / ".env").read_text(), env.read_text())
        self.assertFalse((copied / "app").exists())
        self.assertFalse((copied / ".venv").exists())
        self.assertEqual((copied / "data/whab-sandbox.sqlite").read_bytes(), b"private ledger")
        self.assertEqual(launcher.read_json(Path(backup["directory"]) / "backup.json")["environment_target"], str(env))
        first = launcher.runtime_fingerprint(self.home)
        (self.home / "app/source.py").write_text("source change ignored")
        self.assertEqual(launcher.runtime_fingerprint(self.home), first)

    def test_operation_lock_excludes_other_process(self):
        with launcher.operation_lock(self.root):
            result = subprocess.run(
                [sys.executable, str(MANAGER_FILE), "--root", str(self.root), "status"],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "busy")

    def test_process_identity_requires_creation_time_and_token(self):
        identity = {"started": "one", "command": "python server.py --instance token"}
        record = {"pid": 123, "instance": "token", "identity": identity}
        with patch.object(launcher, "process_identity", return_value=identity):
            self.assertTrue(launcher.owned_process(record))
        with patch.object(launcher, "process_identity", return_value=identity | {"started": "two"}):
            self.assertFalse(launcher.owned_process(record))
        with patch.object(launcher, "process_identity", return_value=identity | {"command": "unrelated"}):
            self.assertFalse(launcher.owned_process(record))

    def test_stop_never_signals_reused_pid_or_port_owner(self):
        launcher.write_json(self.root / "process.json", {"pid": 999, "instance": "old", "identity": {"started": "old", "command": "server.py --instance old"}})
        with patch.object(launcher, "process_identity", return_value={"started": "new", "command": "other app"}), patch.object(os, "kill") as kill:
            self.manager.stop()
        kill.assert_not_called()

    def test_stop_sends_only_graceful_signal_to_matching_process(self):
        record = {"pid": 123, "instance": "token", "identity": {"started": "one", "command": "server.py --instance token"}}
        with patch.object(launcher, "owned_process", side_effect=[True, False]), patch.object(os, "kill") as kill:
            self.manager.terminate(record)
        kill.assert_called_once_with(123, signal.SIGTERM)

    def test_unmanaged_busy_port_is_rejected_before_runtime_setup(self):
        self.config()
        with patch.object(launcher, "available_port", side_effect=AVAILABLE_PORT), patch.object(socket, "socket") as make_socket:
            make_socket.return_value.__enter__.return_value.bind.side_effect = OSError("occupied")
            with patch.object(self.manager, "setup") as setup:
                with self.assertRaisesRegex(launcher.LauncherError, "already in use"):
                    self.manager.launch(self.installation(), self.home, 6412)
            setup.assert_not_called()
        self.assertFalse((self.home / ".env").exists())

    def test_first_switch_busy_unmanaged_port_never_backs_up_or_journals(self):
        self.config()
        with patch.object(launcher, "available_port", side_effect=launcher.LauncherError("port_in_use", "busy")), patch.object(self.manager, "backup") as backup, patch.object(self.manager, "launch") as launch:
            with self.assertRaisesRegex(launcher.LauncherError, "busy"):
                self.manager.switch(self.installation())
        backup.assert_not_called()
        launch.assert_not_called()
        self.assertFalse((self.root / "transition.json").exists())

    def test_start_busy_unmanaged_port_rejects_before_install_or_update_check(self):
        self.config()
        with patch.object(launcher, "available_port", side_effect=launcher.LauncherError("port_in_use", "busy")), patch.object(self.manager, "prepare") as prepare, patch.object(self.manager, "check_update") as check:
            with self.assertRaisesRegex(launcher.LauncherError, "busy"):
                self.manager.start()
        prepare.assert_not_called()
        check.assert_not_called()

    def test_failed_identity_capture_stops_known_child_without_orphan_record(self):
        self.config()
        child = Mock(pid=123)
        child.poll.return_value = None
        with patch.object(launcher, "available_port"), patch.object(self.manager, "setup"), patch.object(launcher, "process_identity", return_value=None), patch.object(subprocess, "Popen", return_value=child), patch.object(launcher.time, "sleep"):
            with self.assertRaisesRegex(launcher.LauncherError, "could not verify its new process identity"):
                self.manager.launch(self.installation(), self.home, 6412)
        child.terminate.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=15)
        self.assertFalse((self.root / "process.json").exists())

    def test_first_setup_does_not_rewrite_existing_configuration(self):
        self.config("PORT=6412\nTOKEN_ENCRYPTION_KEY=preserve\n")
        with patch.object(self.manager, "run") as run:
            self.manager.setup(self.installation(), self.home, 6412)
        self.assertEqual(run.call_count, 1)
        self.assertIn("prepare_local_tls", run.call_args.args[0][-1])
        self.assertEqual((self.home / ".env").read_text(), "PORT=6412\nTOKEN_ENCRYPTION_KEY=preserve\n")

    def test_first_generated_configuration_uses_selected_port(self):
        self.config()
        def generate_default(arguments, **_kwargs):
            if str(arguments[-1]).endswith("scripts/setup.py"):
                path = self.home / ".env"
                path.write_text("PORT=5173\nTOKEN_ENCRYPTION_KEY=new-test-key\n")
                path.chmod(0o600)
        with patch.object(self.manager, "run", side_effect=generate_default):
            self.manager.setup(self.installation(), self.home, 6412)
        self.assertEqual((self.home / ".env").read_text(), "PORT=6412\nTOKEN_ENCRYPTION_KEY=new-test-key\n")

    def test_child_environment_does_not_inherit_banking_or_storage_overrides(self):
        with patch.dict(os.environ, {"PLAID_SECRET": "hidden", "WHAB_BUDGET": "/external/private.whab", "TOKEN_ENCRYPTION_KEY": "hidden"}):
            environment = launcher.clean_environment(self.home, 6412, SHA, "token", smoke=True)
        self.assertNotIn("PLAID_SECRET", environment)
        self.assertNotIn("WHAB_BUDGET", environment)
        self.assertEqual(environment["ALLOW_LIVE_PLAID"], "false")
        self.assertEqual(environment["WHAB_PLUGINS"], "")

    def test_default_tls_uses_explicit_runtime_ca_and_verification(self):
        self.config("WHAB_TLS_CERT_FILE=tls/server.crt\nWHAB_TLS_KEY_FILE=tls/server.key\n")
        (self.home / "tls").mkdir()
        (self.home / "tls/ca.crt").write_text("test authority placeholder")
        context = ssl.create_default_context()
        with patch.object(ssl, "create_default_context", return_value=context) as create:
            result = self.manager.ssl_context(self.home)
        create.assert_called_once_with(cafile=str(self.home / "tls/ca.crt"))
        self.assertTrue(result.check_hostname)
        self.assertEqual(result.verify_mode, ssl.CERT_REQUIRED)

    def test_standalone_python_is_durable_after_original_app_moves(self):
        self.config()
        prefix = self.base / "Original.app/Contents/Resources/python"
        (prefix / "bin").mkdir(parents=True)
        (prefix / "lib/python3.14").mkdir(parents=True)
        (prefix / "bin/python3.14").write_bytes(b"relocatable executable")
        (prefix / "lib/libpython3.14.dylib").write_bytes(b"relocatable library")
        (prefix / "lib/python3.14/ssl.py").write_bytes(b"stdlib marker")
        def probe(arguments, **_kwargs):
            if "sys.version_info.major" in arguments[-1]:
                value = json.dumps({"prefix": str(prefix), "minor": "3.14", "version": "3.14.8-test"})
            else:
                value = str(Path(arguments[0]).parent.parent)
            return subprocess.CompletedProcess(arguments, 0, stdout=value)
        with patch.object(self.manager, "verify_python", return_value=str(prefix / "bin/python3.14")), patch.object(subprocess, "run", side_effect=probe):
            durable = Path(self.manager.stable_python(self.manager.config()))
        self.assertTrue(durable.is_relative_to(self.root))
        self.assertEqual(self.manager.config()["python"], str(durable))
        (self.base / "Original.app").rename(self.base / "Moved.app")
        self.assertTrue(durable.is_file())
        self.assertEqual((durable.parent.parent / "lib/python3.14/ssl.py").read_bytes(), b"stdlib marker")
        self.assertFalse((prefix / "bin/python3.14").exists())

    def test_health_rejects_another_instance_even_with_whab_body(self):
        response = Mock()
        response.read.return_value = json.dumps({"application": "whab", "ready": True, "build": SHA, "instance": "other"}).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(self.manager, "ssl_context", return_value=ssl.create_default_context()), patch.object(launcher.urllib.request, "urlopen", return_value=response):
            self.assertFalse(self.manager.health({"port": 6412, "build": SHA, "instance": "owned"}, self.home))

    def test_offline_start_uses_installed_version_without_update_request(self):
        self.config()
        installation = self.installation()
        launcher.write_json(self.root / "active.json", installation)
        with patch.object(self.manager, "check_update") as check, patch.object(self.manager, "launch") as launch:
            self.manager.start(offline=True)
        check.assert_not_called()
        launch.assert_called_once_with(installation, self.home, 5173)

    def test_offline_failure_keeps_last_working_installation(self):
        self.config()
        installation = self.installation()
        launcher.write_json(self.root / "active.json", installation)
        with patch.object(self.manager, "check_update", side_effect=launcher.LauncherError("offline", "Network unavailable")), patch.object(self.manager, "launch") as launch:
            result = self.manager.start()
        self.assertIn("available installation", result["message"])
        launch.assert_called_once_with(installation, self.home, 5173)
        self.assertEqual(self.manager.active(), installation)

    def test_candidate_install_failure_keeps_running_app_untouched(self):
        self.config()
        launcher.write_json(self.root / "update.json", {"target": {"build": SHA}})
        with patch.object(self.manager, "check_update"), patch.object(self.manager, "prepare", side_effect=launcher.LauncherError("failed", "install failed")), patch.object(self.manager, "stop") as stop:
            with self.assertRaisesRegex(launcher.LauncherError, "install failed"):
                self.manager.update()
        stop.assert_not_called()

    def test_switch_orders_stop_backup_start_and_keeps_previous(self):
        self.config("PORT=6412\n")
        old = self.installation()
        new = self.installation(OTHER_SHA, "2.4.0")
        launcher.write_json(self.root / "active.json", old)
        steps = []
        original_backup = self.manager.backup
        def backup(home, previous):
            steps.append("backup")
            return original_backup(home, previous)
        with patch.object(self.manager, "stop", side_effect=lambda: steps.append("stop")), patch.object(self.manager, "backup", side_effect=backup), patch.object(self.manager, "launch", side_effect=lambda *args: steps.append("start")):
            self.manager.switch(new)
        self.assertEqual(steps, ["stop", "backup", "start"])
        transition = launcher.read_json(self.root / "transition.json")
        self.assertEqual(transition["previous"], old)
        self.assertEqual(self.manager.active(), new)
        self.assertEqual(transition["state"], "complete")

    def test_failed_candidate_never_automatically_downgrades_data(self):
        self.config("PORT=6412\n")
        old = self.installation()
        launcher.write_json(self.root / "active.json", old)
        with patch.object(self.manager, "stop"), patch.object(self.manager, "launch", side_effect=launcher.LauncherError("startup", "failed")) as launch:
            with self.assertRaisesRegex(launcher.LauncherError, "no database downgrade"):
                self.manager.switch(self.installation(OTHER_SHA, "2.4.0"))
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(self.manager.active(), old)
        self.assertEqual(launcher.read_json(self.root / "transition.json")["state"], "failed")
        with self.assertRaisesRegex(launcher.LauncherError, "did not complete"):
            self.manager.start(offline=True)
        with patch.object(self.manager, "launch") as launch, patch.object(self.manager, "check_update") as check:
            for action in (self.manager.restart, self.manager.update):
                with self.assertRaisesRegex(launcher.LauncherError, "did not complete"):
                    action()
            with self.assertRaisesRegex(launcher.LauncherError, "did not complete"):
                self.manager.switch(self.installation(OTHER_SHA))
        launch.assert_not_called()
        check.assert_not_called()
        self.assertEqual(launcher.read_json(self.root / "transition.json")["state"], "failed")

    def test_full_backup_preserves_encryption_keys_ledger_and_tls_privately(self):
        self.config("TOKEN_ENCRYPTION_KEY=private-key\n")
        for relative, value in (("tls/server.key", "tls-private"), ("budgets/id/sandbox/data/ledger.sqlite", "ledger"), ("data/whab-sandbox.sqlite", "legacy"), ("plugins.json", "[]")):
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        backup = self.manager.backup(self.home, self.installation())
        destination = Path(backup["directory"]) / "runtime"
        self.assertEqual((destination / ".env").read_text(), "TOKEN_ENCRYPTION_KEY=private-key\n")
        self.assertEqual((destination / "tls/server.key").read_text(), "tls-private")
        self.assertEqual((destination / "budgets/id/sandbox/data/ledger.sqlite").read_text(), "ledger")
        for path in destination.rglob("*"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.assertEqual(backup["fingerprint"], launcher.runtime_fingerprint(destination))

    def code_environment(self, prefix="", *, family=None):
        manifest = {"format": 1, "plugin": "fictional", "host_sdk": 1,
                    "python": {"version": "3.14", "sha256": "a" * 64}, "wheels": []}
        if family is not None:
            manifest.update(format=2, family=family)
        identifier = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        root = self.home / prefix / "plugin-workers/environments" / identifier
        environment = root / "environment"
        environment.mkdir(parents=True)
        launcher.write_json(root / "ready.json", {**manifest, "identifier": identifier, "installation_sha256": "b" * 64})
        (root / "wheels").mkdir()
        (root / "wheels/reviewed.whl").write_bytes(b"fictional approved input marker")
        return root, environment

    def test_all_new_runtime_roots_are_copied_and_fingerprinted(self):
        self.config("PORT=6412\n")
        names = ("plugin-workers/storage/sqlite/storage-upgrade.json", "plugin-workers/storage-intents/requests.json",
                 "plugin-workers/bank-continuations/fixture.json", "plugin-workers/namespace-effects/model.json",
                 "visual-ui/visual-selection.json", "remote-client/remote-client.json",
                 "storage-authority/authority.json", "storage-authority/replicas.json",
                 "storage-instances/fictional/data/whab-sandbox.sqlite")
        for name in names:
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fictional native or private state")
        before = launcher.runtime_fingerprint(self.home)
        backup = self.manager.backup(self.home, self.installation())
        destination = Path(backup["directory"]) / "runtime"
        self.assertEqual(backup["format"], 2)
        for name in names:
            with self.subTest(name=name):
                self.assertEqual((destination / name).read_bytes(), (self.home / name).read_bytes())
                (self.home / name).write_bytes(b"changed state")
                self.assertNotEqual(launcher.runtime_fingerprint(self.home), before)
                (self.home / name).write_bytes(b"fictional native or private state")
        self.assertEqual(launcher.runtime_fingerprint(destination), before)

    def test_default_and_named_budget_code_aliases_are_archived_without_following(self):
        self.config("PORT=6412\n")
        external = self.base / "outside/python3.14"
        external.parent.mkdir()
        external.write_bytes(b"outside interpreter bytes must never be read or copied")
        expected = []
        for prefix in ("", "budgets/fictional/sandbox"):
            _root, environment = self.code_environment(prefix, family="storage" if prefix else None)
            (environment / "bin").mkdir()
            (environment / "lib").mkdir()
            (environment / "bin/python").symlink_to(external)
            (environment / "bin/python3").symlink_to("python")
            (environment / "lib64").symlink_to("lib", target_is_directory=True)
            (environment / "pyvenv.cfg").write_text("fictional code archive")
            expected.extend((environment / name).relative_to(self.home).as_posix()
                            for name in ("bin/python", "bin/python3", "lib64"))
        before = launcher.runtime_fingerprint(self.home)
        backup = self.manager.backup(self.home, self.installation())
        archive = Path(backup["directory"])
        data = launcher.read_json(archive / "backup.json")
        self.assertEqual({record["path"] for record in data["code_links"]}, set(expected))
        self.assertTrue(data["requires_environment_rebuild"] and backup["requires_environment_rebuild"])
        self.assertEqual(len(data["environment_archives"]), 2)
        for name in expected:
            self.assertFalse((archive / "runtime" / name).exists())
        self.assertEqual(launcher.runtime_fingerprint(archive / "runtime", archived_links=data["code_links"]), before)
        for path in archive.rglob("*"):
            self.assertFalse(path.is_symlink())
            if path.is_file():
                self.assertNotIn(external.read_bytes(), path.read_bytes())
        launcher.write_json(self.root / "transition.json", {"previous": self.installation(), "backup": backup, "state": "complete"})
        with self.assertRaisesRegex(launcher.LauncherError, "explicit reviewed rebuilding"):
            self.manager.rollback()

    def test_arbitrary_new_runtime_links_and_unready_code_links_still_refuse(self):
        self.config("PORT=6412\n")
        outside = self.base / "outside"
        outside.write_text("private marker")
        for name in ("plugin-workers/native-storage/link", "visual-ui/link", "remote-client/link",
                     "storage-authority/link", "storage-instances/link"):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(outside)
            with self.subTest(name=name), self.assertRaises(launcher.LauncherError):
                self.manager.backup(self.home, self.installation())
            path.unlink()
        root, environment = self.code_environment()
        (environment / "bin").mkdir()
        (environment / "bin/python").symlink_to("python3.14")
        (root / "ready.json").unlink()
        with self.assertRaisesRegex(launcher.LauncherError, "ready manifest"):
            self.manager.backup(self.home, self.installation())

    def test_code_link_targets_and_manifest_identity_changes_cannot_be_hidden(self):
        self.config("PORT=6412\n")
        root, environment = self.code_environment()
        (environment / "bin").mkdir()
        link = environment / "bin/python"
        link.symlink_to("python3.14")
        before = launcher.runtime_fingerprint(self.home)
        link.unlink()
        link.symlink_to("python3.15")
        self.assertNotEqual(launcher.runtime_fingerprint(self.home), before)
        link.unlink()
        link.symlink_to("../private/python3.14")
        with self.assertRaises(launcher.LauncherError):
            launcher.runtime_fingerprint(self.home)
        link.unlink()
        link.symlink_to("python3.14")
        ready = launcher.read_json(root / "ready.json")
        ready["plugin"] = "changed"
        launcher.write_json(root / "ready.json", ready)
        with self.assertRaisesRegex(launcher.LauncherError, "content address"):
            launcher.runtime_fingerprint(self.home)

    def test_native_bundle_probe_archives_a_real_stdlib_venv_in_a_disposable_home(self):
        self.config("PORT=6412\n")
        source = MANAGER_FILE.parent.parent / "scripts/check_bundle.py"
        spec = importlib.util.spec_from_file_location("bundle_backup_check", source)
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)
        result = subprocess.run([sys.executable, "-I", "-B", "-c", check._BACKUP_PROBE,
                                 str(MANAGER_FILE), str(self.root), str(self.home)],
                                capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["format"], 2)
        self.assertEqual(value["owned_roots"], 5)
        self.assertGreater(value["code_aliases"], 0)

    def test_changed_native_file_during_copy_keeps_source_and_discards_incomplete_backup(self):
        self.config("PORT=6412\n")
        native = self.home / "storage-instances/fictional/data/ledger.sqlite"
        native.parent.mkdir(parents=True)
        native.write_bytes(b"before")
        copy = launcher.shutil.copyfile
        def changed(source, target, **kwargs):
            result = copy(source, target, **kwargs)
            if source == native:
                native.write_bytes(b"changed during backup")
            return result
        with patch.object(launcher.shutil, "copyfile", side_effect=changed), self.assertRaisesRegex(launcher.LauncherError, "changed while"):
            self.manager.backup(self.home, self.installation())
        self.assertEqual(native.read_bytes(), b"changed during backup")
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_surviving_native_owner_refuses_backup_until_its_real_lock_releases(self):
        self.config("PORT=6412\n")
        lock = self.home / "plugin-workers/native-storage/sqlite/sandbox/storage-writer.json.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        program = "import fcntl,sys; f=open(sys.argv[1],'rb'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.readline()"
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", program, str(lock)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            self.assertEqual(child.stdout.readline(), b"ready\n")
            launcher.ensure_backup_scope(self.home)  # The pre-stop check permits a normal active owner.
            with self.assertRaisesRegex(launcher.LauncherError, "private state lease"):
                self.manager.backup(self.home, self.installation())
            self.assertFalse((self.root / "backups").exists())
        finally:
            child.communicate(input=b"finish\n", timeout=5)
        self.assertEqual(child.returncode, 0)
        backup = self.manager.backup(self.home, self.installation())
        self.assertTrue((Path(backup["directory"]) / "runtime" / lock.relative_to(self.home)).is_file())

    def test_legacy_fingerprints_cannot_authorize_new_contract_rollback(self):
        self.config("PORT=6412\n")
        backup = self.manager.backup(self.home, self.installation())
        backup.pop("format")
        launcher.write_json(self.root / "transition.json", {"previous": self.installation(), "backup": backup, "state": "complete"})
        with self.assertRaisesRegex(launcher.LauncherError, "complete runtime contract"):
            self.manager.rollback()

    def test_external_budget_copy_refuses_update_before_stop(self):
        self.config()
        copies = self.home / "budgets/id/sandbox/copies.json"
        copies.parent.mkdir(parents=True)
        copies.write_text(json.dumps([{"name": "external", "path": str(self.base / "external.whab")}]))
        with patch.object(self.manager, "stop") as stop:
            with self.assertRaisesRegex(launcher.LauncherError, "outside its home"):
                self.manager.switch(self.installation())
        stop.assert_not_called()

    def test_internal_custom_budget_copies_and_tls_are_in_backup_and_fingerprint(self):
        self.config("PORT=6412\nWHAB_BUDGET=personal.whab\nWHAB_TLS_CERT_FILE=certificates/server.crt\nWHAB_TLS_KEY_FILE=certificates/server.key\n")
        copies = self.home / "budgets/id/sandbox/copies.json"
        copies.parent.mkdir(parents=True)
        copies.write_text(json.dumps([{"name": "copy", "path": str(self.home / "copies/second.whab")}]))
        for name in ("personal.whab", "certificates/server.crt", "certificates/server.key", "copies/second.whab"):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        first = launcher.runtime_fingerprint(self.home)
        backup = self.manager.backup(self.home, self.installation())
        destination = Path(backup["directory"]) / "runtime"
        for name in ("personal.whab", "certificates/server.crt", "certificates/server.key", "copies/second.whab"):
            self.assertEqual((destination / name).read_text(), name)
        (self.home / "copies/second.whab").write_text("changed copy")
        self.assertNotEqual(first, launcher.runtime_fingerprint(self.home))

    def test_external_tls_and_runtime_symlinks_refuse_backup(self):
        self.config(f"WHAB_TLS_CERT_FILE={self.base}/cert.pem\nWHAB_TLS_KEY_FILE={self.base}/key.pem\n")
        with self.assertRaisesRegex(launcher.LauncherError, "outside its home"):
            self.manager.backup(self.home, self.installation())
        (self.home / ".env").write_text("PORT=6412\n")
        (self.home / "data").symlink_to(self.base / "external-data")
        with self.assertRaisesRegex(launcher.LauncherError, "symbolic links"):
            self.manager.backup(self.home, self.installation())

    def test_rollback_requires_unchanged_runtime(self):
        self.config("PORT=6412\n")
        old = self.installation()
        new = self.installation(OTHER_SHA, "2.4.0")
        backup = self.manager.backup(self.home, old)
        launcher.write_json(self.root / "transition.json", {"previous": old, "candidate": new, "backup": backup, "state": "complete"})
        launcher.write_json(self.root / "active.json", new)
        (self.home / "data").mkdir()
        (self.home / "data/ledger.sqlite").write_text("schema changed")
        with self.assertRaisesRegex(launcher.LauncherError, "cannot prove schema compatibility"):
            self.manager.rollback()
        self.assertEqual(self.manager.active(), new)
        (self.home / "data/ledger.sqlite").unlink()
        (self.home / "data").rmdir()
        self.manager.rollback()
        self.assertEqual(self.manager.active(), old)

    def test_cli_errors_are_json_and_do_not_echo_private_env(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = launcher.main(["--root", str(self.root), "start", "--offline"])
        self.assertEqual(result, 1)
        value = json.loads(output.getvalue())
        self.assertFalse(value["ok"])
        self.assertEqual(value["error"]["code"], "unconfigured")


class SourceVerificationTest(unittest.TestCase):
    def check(self, sha=SHA, name="CI (Python 3.14)", status="completed", conclusion="success", app="github-actions", identifier=1):
        return {"id": identifier, "head_sha": sha, "name": name, "status": status, "conclusion": conclusion, "app": {"slug": app}}

    def test_required_ci_checks_must_both_pass_for_exact_commit(self):
        checks = [self.check(), self.check(name="CI (Python 3.15)")]
        with patch.object(launcher, "request_json", return_value={"check_runs": checks}):
            self.assertTrue(launcher.successful_checks(SHA))
        for failed in (self.check(name="CI (Python 3.15)", conclusion="failure"), self.check(name="CI (Python 3.15)", sha=OTHER_SHA), self.check(name="CI (Python 3.15)", app="untrusted")):
            with patch.object(launcher, "request_json", return_value={"check_runs": [self.check(), failed]}):
                self.assertFalse(launcher.successful_checks(SHA))

    def test_newer_failed_rerun_invalidates_previous_success(self):
        checks = [self.check(), self.check(identifier=2, conclusion="failure"), self.check(name="CI (Python 3.15)")]
        with patch.object(launcher, "request_json", return_value={"check_runs": checks}):
            self.assertFalse(launcher.successful_checks(SHA))

    def test_main_selects_newest_commit_with_both_successful_checks(self):
        with patch.object(launcher, "request_json", return_value=[{"sha": SHA}, {"sha": OTHER_SHA}]), patch.object(launcher, "successful_checks", side_effect=[False, True]):
            self.assertEqual(launcher.latest_target("main")["build"], OTHER_SHA)

    def test_alpha_selects_exact_main_head_without_history_or_ci_queries(self):
        with patch.object(launcher, "request_json", return_value={"sha": SHA}) as request, patch.object(launcher, "successful_checks", side_effect=AssertionError("Alpha must not query CI")):
            target = launcher.latest_target("alpha")
        request.assert_called_once_with(f"{launcher.API}/commits/main")
        self.assertEqual(target, {"build": SHA, "version": SHA[:12], "channel": "alpha", "tested": False})

    def test_alpha_rejects_malformed_or_inexact_heads(self):
        for response in (None, [], {"sha": "main"}, {"sha": SHA[:12]}, {"sha": OTHER_SHA.upper()}, {}):
            with self.subTest(response=response), patch.object(launcher, "request_json", return_value=response), self.assertRaisesRegex(launcher.LauncherError, "exact source revision"):
                launcher.latest_target("alpha")

    def test_unknown_channel_never_falls_back_to_stable(self):
        with patch.object(launcher, "request_json") as request, self.assertRaisesRegex(launcher.LauncherError, "supported WHAB update channel"):
            launcher.latest_target("unchecked")
        request.assert_not_called()

    def test_cli_accepts_alpha_alongside_existing_channel_names(self):
        for channel in ("main", "alpha", "stable"):
            with self.subTest(channel=channel):
                parsed = launcher.parser().parse_args(["configure", "--channel", channel])
                self.assertEqual(parsed.channel, channel)

    def test_main_refuses_when_ci_is_not_ready(self):
        with patch.object(launcher, "request_json", return_value=[{"sha": SHA}]), patch.object(launcher, "successful_checks", return_value=False):
            with self.assertRaisesRegex(launcher.LauncherError, "passed both"):
                launcher.latest_target("main")

    def test_stable_dereferences_annotated_tag_and_requires_ci(self):
        responses = [{"tag_name": "v2.3.0"}, {"object": {"type": "tag", "sha": OTHER_SHA}}, {"object": {"type": "commit", "sha": SHA}}]
        with patch.object(launcher, "request_json", side_effect=responses), patch.object(launcher, "successful_checks", return_value=True):
            self.assertEqual(launcher.latest_target("stable"), {"build": SHA, "version": "2.3.0", "channel": "stable", "tested": True})

    def test_source_url_rejects_arbitrary_repository(self):
        with self.assertRaisesRegex(launcher.LauncherError, "official WHAB"):
            launcher.request_json("https://example.invalid/repository")

    def test_seed_copy_preserves_budgets_package_but_omits_root_private_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "seed/whab/app"
            (app / "src/whab/budgets").mkdir(parents=True)
            (app / "src/whab/__init__.py").write_text('__version__ = "2.3.0"\n')
            (app / "src/whab/budgets/session.py").write_text("PACKAGE = True\n")
            (app / "pyproject.toml").write_text('[project]\nname="whab"\n')
            private_directories = ("budgets", "data", "tls", ".venv", "cache", "plugin-workers", "visual-ui",
                                   "remote-client", "storage-authority", "storage-instances")
            for private in private_directories:
                (app / private).mkdir()
                (app / private / "private").write_text("private runtime")
            private_files = (".env", "plugins.json", ".whab-recovery", ".plugins-recovery")
            for private in private_files:
                (app / private).write_text("private configuration")
            launcher.copy_seed_source(root / "seed", root / "output")
            copied = root / "output/whab/app"
            self.assertEqual((copied / "src/whab/budgets/session.py").read_text(), "PACKAGE = True\n")
            for private in (*private_directories, *private_files):
                self.assertFalse((copied / private).exists())

    def archive(self, directory, members):
        path = directory / "source.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            root = tarfile.TarInfo("whab-commit")
            root.type = tarfile.DIRTYPE
            archive.addfile(root)
            for name, kind, value in members:
                member = tarfile.TarInfo(name)
                member.type = kind
                if kind == tarfile.REGTYPE:
                    member.size = len(value)
                    archive.addfile(member, io.BytesIO(value))
                else:
                    member.linkname = value
                    archive.addfile(member)
        return path

    def test_archive_extracts_regular_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.archive(root, [("whab-commit/whab/app/file.py", tarfile.REGTYPE, b"safe")])
            launcher.extract_archive(archive, root / "output")
            self.assertEqual((root / "output/whab/app/file.py").read_bytes(), b"safe")

    def test_archive_rejects_traversal_absolute_links_and_devices(self):
        cases = [("whab-commit/../../escape", tarfile.REGTYPE, b"bad"), ("/absolute", tarfile.REGTYPE, b"bad"), ("whab-commit/link", tarfile.SYMTYPE, "/outside"), ("whab-commit/link", tarfile.LNKTYPE, "whab-commit/a"), ("whab-commit/device", tarfile.CHRTYPE, "")]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = self.archive(root, [case])
                with self.assertRaises(launcher.LauncherError):
                    launcher.extract_archive(archive, root / "output")
                self.assertFalse((root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
