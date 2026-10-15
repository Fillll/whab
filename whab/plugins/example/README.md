# Example report and export plugin

This folder is an independently distributed plugin, with its own `pyproject.toml`, source and tests. `src/whab_example_plugin/` uses the public SDK to produce Insights and CSV exports from immutable snapshots.

From the application directory:

```sh
.venv/bin/python -m pip install -e plugins/example
.venv/bin/python -m pytest plugins/example/tests
```

Enable `example` in the plugin manager and restart WHAB to use it. See [plugin contribution rules](../README.md) and [the API guide](../../docs/plugins.md).
