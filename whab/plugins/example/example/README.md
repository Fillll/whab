# Example report and export plugin

This folder is an independently distributed plugin, with its own `pyproject.toml`, source and tests. `src/whab_example_plugin/` uses the public SDK to produce Insights and CSV exports from immutable snapshots.

From the application directory:

```sh
.venv/bin/python -m pip install -e plugins/example/example
.venv/bin/python -m pytest plugins/example/example/tests
```

Enable `example` in the plugin manager and restart WHAB to use it. See [plugin contribution rules](../../README.md) and [the API guide](../../../docs/plugins.md).

Version 1.1.0 requires WHAB 1.5.0 or newer. Insights use core's fixed money format for the snapshot's selected currency: `$1,234.56` for USD, `EUR 1,234.56` for EUR, `JPY 1,234` for JPY, and the corresponding ISO minor-unit precision for other supported currencies. The host supplies one currency per snapshot, so reports never combine or convert currencies.

The CSV export retains its existing `amount_cents` integer column for compatibility and appends `currency`. Despite the legacy column name, each amount is in that currency's minor units: `1234,JPY` means JPY 1,234, while `1234,USD` means $12.34. Dates retain core's `YYYY-MM-DD` format. There are no exchange rates or locale-dependent formatting settings.
