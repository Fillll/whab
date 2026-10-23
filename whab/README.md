# WHAB product

WHAB combines a Python budgeting application with platform launchers that make it easier to install, start, stop and update locally.

| Directory | Ownership |
| --- | --- |
| [`app/`](app/README.md) | Application, financial core, public plugin SDK and independently packaged plugins |
| [`launchers/`](launchers/README.md) | Platform companions for installation, process control and updates |
| [`docs/`](docs/usage.md) | Shared product usage, architecture and plugin contracts |

Start with the [repository overview](../README.md), [application setup](app/README.md), [development runbook](docs/development.md), or [macOS launcher](launchers/macos/README.md). Application development commands run from `whab/app/`; native launcher builds run from their own platform directory.

## Existing development checkouts

The source reorganization moves tracked application files into `app/`. Existing ignored runtime files and environments under `whab/` stay in place. Keep those budgets, credentials, encryption keys and TLS files together. Create a fresh virtual environment in `app/`, reinstall the local distributions, and set `WHAB_HOME` to the absolute path of the existing runtime directory before setup or launch:

```sh
cd whab/app
python3.14 -m venv .venv
.venv/bin/python scripts/install.py --dev
export WHAB_HOME="$(cd .. && pwd)"
.venv/bin/python scripts/setup.py
.venv/bin/python -m whab
```

Only use that `WHAB_HOME` value when the parent `whab/` directory is your existing runtime home. For a new development setup, choose a separate absolute runtime directory or omit the override for files under `whab/app/`. Existing virtual environments and editable registrations may contain old source paths; reinstall in the fresh environment after this move. See the [runtime and backup guidance](docs/usage.md#runtime-setup).
