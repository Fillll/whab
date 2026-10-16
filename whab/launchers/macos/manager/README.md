# Launcher manager protocol

`launcher.py` uses only the Python standard library and requires Python 3.14 or
newer. The native macOS app invokes it as a separate process:

```sh
python3.14 launcher.py \
  --root "$HOME/Library/Application Support/WHAB/launcher" \
  --python "/absolute/WHAB.app/Contents/Resources/python/bin/python3.14" \
  --seed "/absolute/WHAB.app/Contents/Resources/seed" \
  --wheelhouse "/absolute/WHAB.app/Contents/Resources/wheelhouse" \
  status
```

Global arguments precede the command. All paths must be absolute. The default
managed root is `~/Library/Application Support/WHAB/launcher`; the default
runtime is the separate sibling `runtime/`. No command discovers or reads an
existing private installation automatically. `configure` is required before
starting WHAB.

| Command | Behavior |
| --- | --- |
| `configure --runtime-home /absolute/home --channel main\|stable [--port 5173]` | Explicitly adopt an existing runtime or choose a new one. Preserve its `.env` and port. |
| `status` | Return owned-process status and UI paths. |
| `start [--offline]` | Check for the newest verified revision, prepare it, then start; offline or failed preparation uses the installed version or bundled seed. Repeated Start is idempotent. |
| `stop` | Send SIGTERM only to a process with matching PID, kernel creation time, full command, and instance marker. |
| `restart` | Restart the currently installed revision without checking updates. |
| `check-update` | Check for updates without changing application code or runtime data. |
| `update` | Prepare and smoke-test an update while the old app runs, then stop, back up, and start the candidate. |
| `doctor` | Return health, interpreter, transition, backup and log paths without exposing runtime settings or banking data. |
| `rollback` | Select previous code only when stopped and every backed-up runtime file remains byte-for-byte unchanged. Never restore data automatically. |

The single stdout line is a JSON object; success exits zero:

```json
{
  "ok": true,
  "command": "status",
  "state": "running",
  "status": "running",
  "running": true,
  "url": "https://localhost:5173",
  "version": "2.3.0",
  "build": "40-character-source-commit",
  "channel": "main",
  "runtime_home": "/absolute/runtime",
  "log_file": "/absolute/launcher/logs/whab.log",
  "ca_certificate": "/absolute/runtime/tls/ca.crt",
  "update_available": false,
  "latest_version": null,
  "update_warning": null
}
```

`state` is `unconfigured`, `stopped`, or `running`. Failure exits one and returns
`{"ok":false,"command":"start","error":{"code":"...","message":"..."}}`.
Errors and status never include `.env`, account, token, encryption-key or ledger
contents. Detailed subprocess output goes to private `logs/install.log`,
`logs/smoke.log`, or `logs/whab.log`. An exclusive per-root operation lock returns
`busy` during another operation.

## Installation and trust

The main channel selects the newest of the last ten main commits whose latest
GitHub Actions checks named `CI (Python 3.14)` and `CI (Python 3.15)` both succeeded.
The stable channel resolves the latest non-prerelease semantic version tag to an
exact commit and requires the same checks. If GitHub cannot verify an update,
Start retains a working local installation; no unchecked remote revision is
installed. A bundled seed is a separately identified local fallback, including a
development source hash when built from an uncommitted checkout.

Source comes only from the official `Fillll/whab` GitHub repository over verified
HTTPS at an exact commit. Archive extraction bounds file count and total size,
and rejects links, traversal, devices and other nonregular entries. Installation
directories contain a source snapshot and a fresh noneditable virtual environment
at its final absolute path. Host and all seven supported plugins are installed
together. A bundled seed uses its matching offline wheelhouse; downloaded updates
install matching source and its declared dependencies. Before creating any
virtual environment, a self-contained bundled Python is copied into a verified,
private runtime cache under the managed root. Moving or replacing `WHAB.app`
therefore preserves existing installed environments. Explicitly configured system
or framework Pythons retain their configured path. Every interpreter invocation
uses isolated mode and disables bytecode writes to preserve the signed bundle.
A candidate must pass
dependency checks and start in an isolated empty Sandbox runtime before touching
the selected runtime.

`server.py` runs WHAB in the foreground under a detached launcher-owned child.
Its HTTPS-only `/api/health` response contains application/version/build/instance
identity and readiness, and retains local Host/origin restrictions. No banking
endpoint is used for readiness. The manager verifies the runtime CA or configured
server certificate and hostname. It never disables TLS verification or changes OS
trust. The user explicitly trusts the local CA through Keychain Access when needed.

The runtime configuration is written only when `.env` does not exist. On every
start, safe TLS provisioning preserves existing keys and refuses incomplete or
invalid custom certificate settings. A busy local port fails before opening the
real runtime; the launcher never kills a process it did not start.

## Backup boundaries and recovery

After the previous owned process stops, an update copies the complete recognized
runtime state into a new private backup: `.env`, `plugins.json`, `data/`, `budgets/`,
`tls/`, `cache/`, root `.whab-*`/`.plugins-*` recovery files, and configured budget,
copy and certificate paths inside the runtime home. Source files, development
environments and build output in a mixed source/runtime home are excluded. Backup
files are mode 600 and directories mode 700. The backup includes encryption and
TLS keys; a portable `.whab` export is never used as its substitute.

An explicitly adopted `.env` symlink is supported only when it targets a private
regular file owned by the same user. The backup contains a regular copy and keeps
the original target path in private metadata. Other runtime symlinks, special
files, external mutable paths, and remote storage require a separate complete
backup plan; the automatic update refuses before stopping a working app.

The transition journal and previous installation remain available if startup
fails. **Start, Restart, and Update remain blocked after an incomplete transition**
so they cannot execute old code against possibly migrated data. Doctor and Stop
remain available. Code-only rollback is permitted only when a full runtime
fingerprint proves no files changed since backup. For any data or schema change:

1. Stop the candidate and preserve the current runtime and transition journal.
2. Copy the matching backup's `runtime/` into a **new, separate runtime directory**;
   preserve all encryption/TLS keys and fix any absolute paths to the new location.
3. Verify the recovered files privately with the matching previous application
   version. Choose a new launcher managed root and explicitly configure that
   recovered runtime; retain the original managed root as evidence.

There is deliberately no automatic database downgrade, restore-over-existing
operation, backup deletion or version pruning in this first implementation.

Run isolated manager tests from the repository root:

```sh
python3.14 -m unittest discover -s whab/launchers/macos/tests -p 'test_*.py'
```
