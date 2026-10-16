# Example report and export plugin

Distributed independently as `whab-example-plugin`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

This folder is an independently distributed plugin, with its own `pyproject.toml`, source and tests. `src/whab_example_plugin/` uses the public SDK to produce Insights and CSV exports from immutable snapshots.

The example is developer-only and hidden from ordinary Settings, including Options. From the application directory (`whab/`), install it alongside the host and supported plugins:

```sh
.venv/bin/python scripts/install.py --dev --example
.venv/bin/python -m pytest plugins/example/example/tests
```

Use a fresh isolated runtime with `WHAB_PLUGINS=example`, provision its TLS certificate and trust its local CA explicitly, or use the selection API for an existing saved budget. Follow the complete [working-example instructions](../../../docs/plugins.md#install-the-working-example); environment changes do not override saved `plugins.json` or portable manifests. Ordinary Settings saves preserve valid hidden example selections. See [plugin contribution rules](../../README.md).

Version 1.2.0 declares **`whab>=1.5.0,<3`** and SDK `api_version=1`. Insights use core's fixed money format for the snapshot's selected currency: `$1,234.56` for USD, `EUR 1,234.56` for EUR, `JPY 1,234` for JPY, and the corresponding ISO minor-unit precision for other supported currencies. The host supplies one currency per snapshot, so reports never combine or convert currencies.

The CSV export retains its existing `amount_cents` integer column for compatibility and appends `currency`. Despite the legacy column name, each amount is in that currency's minor units: `1234,JPY` means JPY 1,234, while `1234,USD` means $12.34. Dates retain core's `YYYY-MM-DD` format. There are no exchange rates or locale-dependent formatting settings.
