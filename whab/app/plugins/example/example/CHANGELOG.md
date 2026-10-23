# Example report and export plugin changelog

## 1.4.0 — 2026-10-23

PR: [#80](https://github.com/Fillll/whab/pull/80)

- Declare the stateless report/export live lifecycle contract and require WHAB 2.24+. Document live enable/disable, generation status and saved selection without changing integer-currency reports or exports.

## 1.3.1 — 2026-10-16

PR: [#40](https://github.com/Fillll/whab/pull/40)

- Rename the independent distribution and Python package to `whab_plugin_example` under the shared `whab_plugin_<name>` convention. Preserve the plugin ID and its existing saved data and settings.

## 1.3.0 — 2026-10-16

PR: [#31](https://github.com/Fillll/whab/pull/31)

- Use the core account budget scope in spending and income insights while retaining off-budget activity in the transaction export.
- Require WHAB 2.1 or newer for the account-scope snapshot field, keeping SDK API v1 compatibility.

## 1.2.0 — 2026-10-15

PR: [#30](https://github.com/Fillll/whab/pull/30)

- Declare compatibility with WHAB 2.x while retaining support for WHAB 1.5 and newer 1.x releases.
- Begin keeping dated release notes in this plugin folder. Existing version 1.1.0 remains part of its independent distribution history; this entry does not renumber earlier releases.
