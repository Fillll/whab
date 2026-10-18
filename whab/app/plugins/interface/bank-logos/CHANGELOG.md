# Public bank artwork plugin changelog

## 1.1.0 — 2026-10-18

PR: [#51](https://github.com/Fillll/whab/pull/51)

- Resolve explicitly saved public bank hostnames through the fixed Icon Horse artwork source, alongside curated institution aliases. Cache only bounded validated raster files and expose provider attribution for account settings.

## 1.0.1 — 2026-10-16

PR: [#40](https://github.com/Fillll/whab/pull/40)

- Rename the independent distribution and Python package to `whab_plugin_bank_logos` under the shared `whab_plugin_<name>` convention. Preserve the plugin ID and its existing saved data and settings.

## 1.0.0 — 2026-10-15

PR: [#30](https://github.com/Fillll/whab/pull/30)

- Introduce independent packaging and version ownership for the existing `bank-logos` plugin, compatible with WHAB 2.x. Earlier changes shipped inside the application distribution and were not independent plugin releases.
- Preserve its plugin ID, Python package, entry point and existing behavior.
