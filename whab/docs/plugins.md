# WHAB plugins — API v1

WHAB provides **user-interface**, **storage**, **data-ingestion**, **reporting**, **branding** and **categorization** plugin capabilities under API v1. WHAB 1.2 adds a host-owned manager for optional plugin selections; WHAB 1.3 adds optional bank-logo branding. Plugins are installed Python packages registered through `whab.plugins` entry points. The [supported plugins](../app/plugins/README.md#supported-plugins) are independent distributions using those same contracts. The teaching example is a separate developer package. Reports receive immutable snapshots; storage and bank factories receive separate privileged contexts.

The [budget and plugin model](target-architecture.md) groups capabilities by responsibility. This guide defines the executable API v1 contracts; category names do not add or rename SDK hooks.

The [per-budget runtime](plugin-runtime.md) documents generation leases, controlled
handover and the first explicit live reporting/export selection API. Other
families retain their documented restart requirement until their adapters exist.

The [approved report-worker adapter](plugin-workers.md) adds bounded framing,
immutable local wheel environments, compatible report upgrades and validated
recovery. Installed Python remains trusted executable code.

The [curated catalog](plugin-catalog.md) records package identity, compatibility,
static capabilities and exact verification evidence. Regenerate it with
`.venv/bin/python scripts/plugin_metadata.py --write` from `whab/app/`; the normal
documentation check rejects drift. Generation reads source declarations without
importing plugin modules. `whab.plugins.catalog.catalog_snapshot()` reads a
detached, validated copy of the packaged catalog without entry-point discovery,
network access or installation. It describes reviewed source artifacts rather
than the active or installed selection. Report vulnerabilities through the
[security policy](../../SECURITY.md).

Live optional adapters also cover explicit branding and pure file parsers. A
descriptor may set `branding_factory: Callable[[], Plugin]` to create independent
bank/merchant artwork and settings hooks for each budget. It receives no context
or credentials, must preserve identity and the effective contract, and must return
a descriptor without another factory, binder or non-branding capability. General
budget binders retain their separate restart/state contract.

`PluginResources(files: Mapping[str, bytes])` copies a bounded immutable UI bundle
under safe `templates/` and `static/` relative names. `FileImport` may use
`resources=PluginResources(...)` instead of filesystem paths; the forms are mutually
exclusive. Parser bundles require `templates/accounts.html` and `static/import.js`.
The host captures one resource version with each parser, renders its matching
templates and serves generation-specific asset URLs. See [worker bounds and
handover behavior](plugin-workers.md#recovery-and-limits).

File-import preview responses include `parserIdentity`. Commit clients must send
that value as `X-Import-Parser` for explicitly live parsers. Missing/retired tokens
return 409 before the replacement parser runs. A fresh preview preserves the
original file fingerprint; import operation IDs and completed retry receipts
remain unchanged.

## Trust and enablement

Application source paths in this guide are relative to `whab/app/`. Each concrete implementation lives in `plugins/<category>/<id>/` and owns its source package, assets, README, tests, `pyproject.toml` and dated `CHANGELOG.md`. The canonical [plugin inventory](../app/plugins/README.md#supported-plugins) identifies the supported set and the separately installed teaching example. The `bundled` extra selects that supported set when compatible distributions are available in the chosen package index or local wheelhouse; source checkouts use `.venv/bin/python scripts/install.py --dev` from `whab/app/`. The host wheel contains no concrete plugin package or entry point. A material plugin-only change bumps that plugin's `project.version` and local changelog; only host/core/SDK changes bump the app, with spanning changes updating each affected owner. Keep implementation tests beside their plugin and shared SDK/host contracts in `tests/`. See [folder ownership and test commands](../app/plugins/README.md). The public `whab.plugins` SDK stays in core under `src/whab/plugins/`.

Every plugin uses `whab_plugin_<id>` for both its Python package and declared distribution, with ID hyphens converted to underscores. For example, `sqlite` uses `whab_plugin_sqlite` and `bank-logos` uses `whab_plugin_bank_logos`; the teaching example uses `whab_plugin_example`. Runtime IDs and readable display names remain independent of package names. Packaging tools may normalize distribution underscores to hyphens when displaying metadata. When upgrading from the former `whab-<id>-plugin` names, use the source installer or follow the [wheel-environment migration instructions](../app/CONTRIBUTING.md#development-environment) to avoid duplicate entry points.

CI requires 100% Python statement and branch coverage for each plugin's entire source tree. The full application command `.venv/bin/python scripts/check.py` includes the coverage gate, which discovers all plugin source roots, includes unimported files, and measures the complete local/shared suite. Use `--quick` during iteration and `--docs` for documentation-only edits; see the [development runbook](development.md). New behavior needs regression tests in its owning plugin folder; do not suppress runtime coverage gaps.

**Installed Python plugins are trusted code running in WHAB's server process, not a security sandbox.** Reporting hooks receive snapshots rather than backend or credentials; budget binders can read their own portable settings and state. Bank factories receive scoped persistence/configuration and an encryption key. Storage factories own the full ledger and backend credentials; visual descriptors provide trusted templates, scripts and presentation code. Python can independently access files/network or import internals with WHAB's privileges. Select only reviewed code. These interfaces limit host-supplied capabilities and accidental mutation, not malicious code.

Installation alone does not select an additional package. For the original workspace and initial CLI budget creation, `WHAB_STORAGE` selects required storage (`sqlite` by default). An opened portable budget takes storage and optional selections from its manifest; Settings creates new SQLite budgets. The host also loads the installation's selected `WHAB_UI` descriptor (`web` by default). For the original local workspace, optional names are saved in the runtime home’s `plugins.json`. Without a saved selection, `WHAB_PLUGINS` supplies optional names (`plaid` when absent); `WHAB_PLUGINS=` disables optional banks/reports/branding while retaining storage and selected UI. `WHAB_UI=` is headless: APIs/storage remain without the root page or core visual assets. Restart after changing settings. Names are lowercase slugs of at most 64 characters.

Use `WHAB_STORAGE` for the original workspace/new CLI budget and `WHAB_UI` for the installation's visual interface; an opened portable budget's storage remains manifest-owned. One primary backend is required; at most one server-rendered visual interface may be selected, alongside trusted transport interfaces. Missing required storage/UI, incompatible capabilities and multiple enabled storage/UI descriptors fail with actionable errors. Missing portable optional packages are reported without blocking opening and their saved data is preserved. The host deduplicates a selected name also present in the optional list; repeated optional names remain invalid. A standalone `load_plugins(())` loads nothing, but app composition adds required storage and selected UI before discovery.

An available enabled name must match exactly one entry point and its descriptor ID. A duplicate setting name, duplicate installed entry point, invalid descriptor, or unsupported API version prevents startup with an actionable message. Required storage/interface packages must be installed. Portable budgets report unavailable optional packages while retaining their manifest entries; the original local selection continues to fail explicitly for missing selected packages. WHAB resolves all configured names before importing any plugin; it never silently chooses one of two installed packages claiming the same name. Ordinary import or hook exceptions are reported through fixed messages without their original exception text or traceback in WHAB's public responses/logging. Arbitrary plugin code can still produce its own logs or hang, so this is error handling rather than sandboxing.

## Budget ownership

Executable Python packages are installed in the WHAB environment. A portable budget owns its selected optional plugin IDs, API compatibility requirements, settings and saved plugin state. Opening that budget loads the available compatible capabilities; disabling one preserves its saved data. Missing optional packages are reported so a budget can be opened without silently installing code. Required storage must be available.

Version 2 `.whab` containers require `manifest.json`, which records budget identity/name, primary storage and plugin entries with `id`, `enabled` and `apiVersion`. Each plugin owns data files under `plugins/<id>/`. Its portable JSON settings and state use `settings.json` and `state.json` within that namespace. Other payload formats belong to their plugin: SQLite contributes `plugins/sqlite/budget.sqlite`; the remote client contributes `plugins/client/connection.json`. The host validates names and bounds and preserves unknown or disabled plugins’ files without interpreting them.

There is no mandatory `ledger.json` or automatic financial snapshot in version 2. The remote client contributes connection information rather than a financial snapshot; the complete container still carries its required manifest and namespaced settings/state or other retained plugin payloads. Existing version 1 manifest/ledger JSON bundles remain readable; their normalized ledger is validated for fields, references and currencies before restoration. Bank Item/token records and token encryption keys are not portable payloads.

A descriptor can supply `bind(context: BudgetContext) -> Plugin` to construct hooks for one opened budget. The host calls the binder once per runtime before selecting capabilities. Return a descriptor with the same ID and API version and no further binder; ordinary descriptors continue to work unchanged. Each budget gets its own closures:

```python
from whab.plugins import Insight, Plugin


def bind_budget(context):
    label = context.settings.get("label", "Transactions this month")

    def insights(snapshot):
        return (Insight(label, str(len(snapshot.transactions))),)

    return Plugin("month-summary", "Month summary", insights=insights)


plugin = Plugin("month-summary", "Month summary", bind=bind_budget)
```

`context.settings` and `context.state` are independent copies scoped to this plugin and budget. Use `context.save_state({...})` to persist portable JSON state. `context.read_file(name)`, `write_file(name, bytes)` and `delete_file(name)` access only that plugin’s namespaced payload files; names are relative to `plugins/<id>/`. The selected storage backend manages its payload through the export/prepare methods below; its context permits reading files and rejects direct writes/deletes. Runtime closures and Python objects are not serialized, and saving configuration or enabling/disabling plugins takes effect when the budget runtime restarts. Do not cache a budget context or its models, settings, suggestions or training sessions across budget identities. Follow the [isolation invariant and two-budget verification recipe](architecture.md#budget-isolation-verification) when changing binders, persistence, imports or routing.

Storage backends support containers through `export_budget_files(workspace="personal") -> dict[str, bytes]` and `prepare_budget_files(workspace, files, *, existing=False)`. The host adds/removes the owning plugin namespace; the backend receives relative filenames and always validates them. First opens and interrupted restores (`existing=False`) restore only into an untouched runtime, or accept an identical previous restore. A known runtime (`existing=True`) keeps committed live data authoritative and can restore a missing, untouched cache. SQLite also exposes `restore_budget_files(workspace, files)` for explicit restoration. Remote preparation validates connection information without fetching a ledger snapshot. Backends that only implement the legacy ledger helpers continue to produce version 1 bundles.

A storage descriptor can provide `budget_connection(files) -> Mapping[str, str]` to parse public connection information before its factory starts. The host uses an unlocked credential profile bound to those exact public fields, or requires matching device configuration; it never combines an imported server address with an unrelated device token.

Host endpoints support budget management:

- `GET /api/budget` describes the current budget and copy/plugin status.
- `GET /api/budget/export` downloads the current `.whab` container with credentials kept on the device. `?include_credentials=true` explicitly includes an encrypted remote client credential and requires a password header.
- `GET /api/budgets/{id}/export` downloads a known budget without changing the browser's selected budget. An unopened encrypted connection requires the password header. Both export routes accept `workspace=demo` to download only the demo ledger as an independent SQLite-compatible budget with a new identity; demo exports omit personal plugin data and reject credential inclusion. Browser downloads use the addressed budget scope: `/budgets/<key>/api/budgets/{id}/export` for a real budget and `/budgets/demo/api/budget/export` for the single demo destination.
- `POST /api/budget/open` accepts a raw `application/zip` bundle up to 50 MiB, validates it and activates it in the browser session. Supply the password header to unlock an included remote credential. `?as_copy=true` gives the imported contents a new budget identity, preserving a conflicting revision separately without merging.
- `POST /api/budget/new` accepts JSON `{"name": "Household"}` and activates a new budget.
- `GET /api/budgets` lists opened/recent budgets; `POST /api/budgets/{id}/activate` switches the active session, accepting the same password header when reopening an encrypted connection.
- `POST /api/budget/copies` adds a device-local destination with JSON `{"name": "Backup", "path": "/absolute/path/Backup.whab"}`. `POST /api/budget/save` retries saves.
- `GET /api/budget/plugins/{id}` reads that plugin’s selection and portable settings/state. `PUT` updates its `settings` and/or `state` as bounded JSON objects.

Password-bearing requests use `X-Budget-Password`. For Unicode passwords, encode UTF-8 bytes as base64 and send `X-Budget-Password-Encoding: base64`; raw ASCII is also accepted. Export passwords require at least 12 and at most 1,024 characters. The password is never a URL parameter. Only the optional remote client credential is encrypted, using AES-GCM with scrypt-derived keys; the rest of the container remains ordinary plugin data. Bank-provider tokens are never included.

The session cookie identifies the active budget for existing unscoped clients without carrying its financial data. Canonical browser pages, assets, downloads and mutations instead use the budget key in their scoped address, so an unrelated tab cannot retarget the operation by changing the cookie. These operations keep existing local access and request-validation guards. Data-only bundles never install packages or execute bundled code.

## Manage optional selections

The bundled Web UI's **Settings → Plugins → Installed** lists installed registrations alongside the running and configured selections. It defaults to **All categories**; a category filter narrows the installed list. Every visible row has a **Settings** page; teaching examples are hidden from ordinary Settings and have no visible Settings page. Each visible page contains package details and any controls supplied by the active plugin. Inactive registrations show enable/restart guidance without executing settings hooks. Discovery reads package metadata without importing disabled plugin code. Known inactive supported registrations use friendly names and categories from bundled registration metadata without importing code; unknown registrations fall back to their entry-point IDs. Inactive capability kinds remain unknown until their descriptors are explicitly loaded. Selected storage and UI plugins remain required. An unavailable or duplicate optional registration cannot be enabled, but a selected one can be unchecked to repair the selection for the next startup.

**Save plugin selection** loads and validates the proposed descriptors without calling storage/bank factories or reporting hooks. It rejects missing or ambiguous registrations, incompatible descriptors, and attempts to change the selected storage or interface. Saving leaves the running registry, provider services, connections and ledger unchanged; changes apply after restart. An imported bank's history and saved connections remain when its plugin is disabled.

For failures that prevent the full application from starting, the explicit
[`--recover-plugins` listener](plugin-recovery.md) reads package metadata without
loading plugin targets. It offers revision-checked optional-plugin removal,
archival of invalid global selections or saved report-worker references, and a
full startup retry on the same guarded HTTPS listener. It preserves opaque budget
namespaces and unopened financial payloads; it cannot disable required storage or
the selected interface or repair an invalid budget manifest by guessing.

For portable budgets, the host stores the optional selection in that budget’s manifest, preserving disabled entries and their settings/state. The original local workspace stores at most 100 unique optional IDs in an atomic `plugins.json` replacement in the runtime home (`WHAB_HOME`, or the launch directory). The file uses `{"version": 1, "plugins": ["plaid", "example"]}` and contains no credentials or ledger records. Its optional selection takes precedence over `WHAB_PLUGINS`, including an explicitly empty saved list. Removing this file restores environment defaults on the next start. Invalid or unreadable saved settings cause an actionable startup error; repair the file, or explicitly save a replacement while WHAB is still running.

Host-owned endpoints expose this behavior to custom interfaces and headless clients:

- `GET /api/plugins/management` returns `plugins`, optional-ID lists `active` and `configured`, `restart_required` and a safe optional `error`. Catalog rows include `id`, `display_name`, `category`, `kinds`, package `distribution`/`version`, `active`, `enabled`, `required`, `manageable` and a safe optional `reason`. A row's `active` describes the fixed running registry; `enabled` describes the configured selection plus required plugins; `manageable` indicates whether an optional registration is available for selection.
- `PUT /api/plugins/management` accepts an application/json body such as `{"plugins": ["plaid", "example"]}` containing only optional IDs. A successful save returns the same projection. Host/origin, request-size and strict body validation guards apply. These are host routes, not plugin-owned HTTP extensions.

`GET /api/plugins` continues to describe running plugins only. A pending selection does not change exports, bank routes or mounted resources until restart.

## Install the working example

The teaching example is deliberately hidden from ordinary Settings, including Plugin Settings. Run these developer commands from the application directory (`whab/app/`) in the server's virtual environment:

```sh
.venv/bin/python scripts/install.py --dev --example
whab_example_home="$(mktemp -d)"
WHAB_HOME="$whab_example_home" .venv/bin/python scripts/setup.py
WHAB_HOME="$whab_example_home" WHAB_PLUGINS=example .venv/bin/python -m whab
```

Trust the generated example home's CA explicitly using the [local HTTPS instructions](usage.md#local-https), then open https://127.0.0.1:5173 and choose **Insights** for fictional demo data. This fresh home has no saved `plugins.json` or portable manifest, so `WHAB_PLUGINS=example` is the effective selection; it does not reuse household data or bank credentials. Use another local port when a server is already running.

For an existing saved workspace or opened portable budget, installing the example or changing `WHAB_PLUGINS` does not override saved selections. Inspect `GET /api/plugins/management`, then use `PUT /api/plugins/management` with the complete desired optional list, including `example`, and restart. For example, with the default Plaid selection:

```sh
curl --cacert /absolute/path/to/runtime/tls/ca.crt \
  -H 'Content-Type: application/json' -X PUT \
  --data '{"plugins":["plaid","example"]}' \
  https://127.0.0.1:5173/api/plugins/management
```

Keep any other intended optional IDs in that request; it replaces the optional list. For an already opened portable budget, append `--cookie 'whab-budget=<budget-id>'` using its ID from `GET /api/budgets` so the request targets that budget; do not include private browser-session values in documentation or commits. Remove `example` through the same API and restart to disable it. Ordinary Settings saves preserve valid hidden example selections rather than presenting a new user-facing control.

The example shows cleared net spending and income. Pending transactions and transfers are excluded from these totals; categorized inflows count as refunds, while uncategorized inflows count as income. Imported income is analytics only and does not increase assignable cash. Its CSV export includes all selected-month transactions with pending/transfer flags and integer currency minor units plus a currency column. The example declares `whab>=2.1,<3` and `api_version=1`; its version belongs to its own distribution, not the host.

## Public SDK

Import public types from `whab.plugins`; concrete database, visual/transport internals and global configuration are outside the stable SDK. Descriptors may provide `ui`, `interface`, `storage`, `bank`, `file_import`, `insights`, `export`, `bank_logos` and `categorization`, or a budget-specific `bind` callback that supplies capabilities, plus optional `settings` (WHAB 1.6+) and bank-owned `merchant_logos` (WHAB 1.7+). Their computed `kinds` are `user_interface`, `storage`, `data_ingestion`, `reporting`, `branding` and `automation`; one descriptor may have multiple kinds. At least one supported capability or a budget binder is required; settings alone are insufficient. Data ingestion includes bank connection/sync and, from WHAB 1.4, bounded local file imports.

Frozen report records use immutable tuples and exact primitives. Every hook receives fresh records from a whitelisted snapshot, protecting against accidental mutation. Storage, visual and bank capabilities are described below.

```python
from whab.plugins import (
    AccountSnapshot, BudgetSnapshot, CategorySnapshot, TransactionSnapshot,
    ExportTable, Insight, Plugin,
)

AccountSnapshot(
    id: str, name: str, type: str, subtype: str, balance_cents: int,
    balance_unknown: bool = False, stale: bool = False, on_budget: bool | None = None,
)
CategorySnapshot(
    id: str, name: str, group: str,
    assigned_cents: int, activity_cents: int, available_cents: int,
)
TransactionSnapshot(
    id: str, date: str, payee: str, amount_cents: int,
    account_id: str, category_id: str | None,
    pending: bool = False, is_transfer: bool = False, reviewed: bool = False,
)
BudgetSnapshot(
    workspace: str, month: str,
    accounts: tuple[AccountSnapshot, ...],
    categories: tuple[CategorySnapshot, ...],
    transactions: tuple[TransactionSnapshot, ...],
    currency: str = "USD",
)
```

The block above documents constructor signatures; it is not executable Python. `workspace` is `demo` or `personal`, `month` is a valid `YYYY-MM`, and every transaction date belongs to that month. Transaction account/category IDs must refer to records in the same snapshot. The server selects the current Sandbox or production ledger before constructing it; the snapshot does not combine environments or workspaces. IDs are ledger references for joining these records, not Plaid access tokens or an account-access capability. Payees and names can be private financial data and must not be logged by plugins.

Amounts are **integer minor units of `snapshot.currency`**, never floats. The legacy `_cents` field names remain for API v1 compatibility; USD uses cents, JPY whole yen and KWD thousandths. Each snapshot contains one currency only, selected by the host. `CategorySnapshot.parent_id` identifies its organizational parent group; snapshot categories are assignable children only. Public helpers `format_money(amount, currency="USD")`, `format_date(value)`, `currency_code(value)`, `currency_minor_units(currency)` and `SUPPORTED_CURRENCIES` are exported by `whab.plugins`. Defaults are fixed: `YYYY-MM-DD` dates, `$1,234.56` USD and ISO-code-prefixed other currencies with their defined precision. UI/report plugins can provide alternative presentation without changing stored units or validation. Transactions use WHAB's signed convention: positive is an outflow, negative is an inflow. Category activity is net categorized cleared spending, so refunds reduce it. Available money may include rollover from earlier months, while account balances are the host's snapshots rather than a historical month-end reconstruction. Check `stale` and `balance_unknown` before reporting available account cash. Transaction notes, Plaid Items/tokens, raw provider responses, broader transaction history, and configuration are deliberately absent.

### Insights hook

`AccountSnapshot.on_budget` is a boolean after construction. Its compatible default is true for cash/credit accounts and false for other account types. Report hooks receive all accounts and transactions; spending and income reports should filter transaction account IDs to accounts with `on_budget=True`. Off-budget records remain available for asset and liability reports and complete exports. `ACCOUNT_TYPES`, `ACCOUNT_SUBTYPES`, `ON_BUDGET_ACCOUNT_TYPES`, `LIABILITY_ACCOUNT_TYPES`, `normalize_account`, `is_on_budget` and `is_liability` are public classification helpers in `whab.plugins`.

```python
def insights(snapshot: BudgetSnapshot) -> tuple[Insight, ...]:
    count = sum(not row.pending for row in snapshot.transactions)
    return (Insight("Cleared transactions", str(count), snapshot.month),)
```

`Insight(title: str, value: str, detail: str = "")` is plain text. Return a tuple of at most 50 insights. Maximum lengths are 120 characters for titles, 160 for values, and 2,000 for details. WHAB escapes the text in its HTML templates; plugins cannot return raw HTML, HTML-aware string subclasses, scripts, or templates. A plugin whose insight hook raises an ordinary exception or returns invalid data gets an error panel; other enabled insight plugins continue rendering.

### Export hook

```python
def export(snapshot: BudgetSnapshot) -> ExportTable:
    return ExportTable(
        columns=("date", "payee", "amount_cents"),
        rows=tuple(
            (row.date, row.payee, row.amount_cents)
            for row in snapshot.transactions
        ),
    )
```

`ExportTable(columns: tuple[str, ...], rows: tuple[tuple[str | int | bool | None, ...], ...])` has 1–50 text headings, at most 100,000 rows, and matching row widths. Headings are at most 200 characters, string cells at most 10,000. Cells may be exact plain strings, integers, booleans, or `None`; floats, dicts, paths, iterators, and custom objects are rejected. The host owns CSV serialization, spreadsheet-formula protection for headings and text cells, response headers, and the download filename. Return text as text, without implementing your own escaping. A plugin returns no output filesystem path and cannot customize content type or headers through this API. Plugin export failures return a sanitized error instead of a partial download.

Exports are limited to the selected workspace and month. `/api/plugins` reports enabled descriptors, and `/api/plugins/{id}/export?workspace=demo&month=2026-10` requests an enabled export through the same local access checks as other APIs. Insights hooks run when the Insights page is requested, not on every page or background synchronization. These routes do not provide a plugin route-registration API.

### Descriptor and package metadata

```python
plugin = Plugin(
    id="my-summary",
    display_name="My month summary",
    api_version=1,
    insights=insights,  # optional
    export=export,      # optional
)
```

A descriptor must supply at least one of `ui`, `interface`, `storage`, `bank`, `file_import`, `insights`, `export` or `bank_logos`, or a `bind` callback that returns budget-specific capabilities. `settings` alone is insufficient, and `merchant_logos` supplements a bank capability. The entry point resolves directly to the `Plugin` object, not a factory. Register it in the package's `pyproject.toml`:

```toml
[project]
name = "whab_plugin_my_summary"

[project.entry-points."whab.plugins"]
my-summary = "whab_plugin_my_summary:plugin"
```

After installing the package, select **plaid** and **my-summary** in Settings, save, and restart. With no saved selection, use `WHAB_PLUGINS=plaid,my-summary` instead. Multiple environment names use commas. From WHAB 2.22, capability dependencies run before consumers, with plugin ID breaking ties between independent plugins; discovery and selection order no longer change insight or branding precedence. Declare a compatible WHAB dependency, for example `whab>=1.1.0,<3` for the original API. The capability metadata below requires `whab>=2.22,<3`.

### Versioned capabilities and saved state

The independently packaged [plugin developer toolkit](../app/devtools/plugin-devkit/README.md)
provides initial public storage conformance suites. External projects install
only their host, toolkit and candidate/reference packages; optional advertised
features fail explicitly when broken and unadvertised features report their
applicability. The kit is development infrastructure outside runtime discovery.

SDK API v1 remains supported. WHAB 2.22 adds immutable runtime metadata separately
from Python distribution dependency resolution:

```python
from whab.plugins import Capability, Plugin, PluginContract, Requirement

plugin = Plugin(
    id="my-summary", display_name="My month summary", insights=insights,
    contract=PluginContract(
        provides=(Capability("my-summary.dataset", version=1),),
        requires=(Requirement("ledger.read", min_version=1, max_version=1),),
        lifecycle="restart", state_version=1,
    ),
)
```

Versions are positive integers through 65,535, and requirement bounds are
inclusive. Each capability composes as `additive` or `singleton`. Core hook
capabilities are inferred even with explicit metadata: a storage hook cannot hide
its singleton `ledger.mutate` writer, and a visual hook cannot hide its singleton
`interface.ui`. Other names include `ledger.read`, `provider.state`,
`provider.sync`, `interface.api`, `reporting.insights`, `reporting.export`,
`ingestion.bank`, `ingestion.file`, `branding.bank`, `branding.merchant`, and
`categorization`. Storage may additionally advertise optional contracts such as
`receipts`, `transfer.neutral`, and `transfer.prepare`; declarations alone are not
conformance evidence. Existing public protocols define their method shapes.

`requires` orders consumers after every selected provider of that capability;
every provider must satisfy the accepted version range. `conflicts` names
capabilities that cannot coexist with this plugin. The host rejects missing or
incompatible requirements, composition disagreements, singleton conflicts and
dependency cycles before invoking binders or factories. `load_descriptors` loads
validated descriptors for assembling a complete candidate; `PluginRegistry` and
`load_plugins` resolve that complete candidate.

`authority` is `local`, `remote`, or `unknown`. Remote storage receives
host-enforced bank-ingestion and local-training restrictions independently of its
ID. Old descriptors without `contract` receive an explicit legacy adapter with
restart-only lifecycle; a legacy `budget_connection` hook identifies remote
authority. Other legacy storage authority is unknown, and inferred provider
capabilities preserve API v1 compatibility without certifying optional behavior.
Active management projections expose effective capabilities, requirements,
authority, lifecycle declaration, saved-state version and legacy status. Disabled
packages stay unimported and have no inferred compatibility claim.

`state_version` names the current saved-state schema. `state_upgrades` contains
directed `(source, target)` edges increasing toward it. A declared path is
compatibility evidence, not permission to overwrite a budget. An optional
`migrate_state(context, source, target)` synchronous callback prepares one edge
using a detached `BudgetContext`: its own state and data files only, with settings
read-only. Callbacks expire after that edge. The host validates every path before
running any migrator, bounds the resulting container, and returns a candidate
without changing the active namespace. Startup refuses an upgrade until a
transition explicitly commits it; newer state or a missing path reports an
actionable error. Disabled and unavailable state remains opaque.

Existing manifest v1 and container v2 retain their exact schemas and readers.
The explicit v3 upgrader adds `stateVersion` to each plugin record; old state
starts at version 1. V3 supports both legacy neutral ledger bundles and opaque
plugin-file containers. Reading old budgets never silently rewrites them. Live
activation and recovery controls for committing prepared upgrades are tracked in
the [platform execution checklist](plugin-platform.md).

## Storage backends

WHAB 2.23 adds the independently packaged [JSON backend](../app/plugins/storage/json/README.md)
alongside SQLite. It passes the public storage conformance suites and stores its
native ledger, receipts and encrypted provider records in one versioned file.
Keys remain outside that file. Use its sanitized portable export for transfer;
the native JSON file contains private device state. Native full-file limits,
uncertain commits, writer locking and measured performance are documented in its
owning guide. Selecting JSON does not migrate SQLite data or promote a copy.

The bundled SQLite descriptor is registered as `sqlite = "whab_plugin_sqlite.plugin:plugin"`. Host code imports `StorageBackend` rather than a concrete store or database driver. The SQLite descriptor opens its backend only when the selected factory runs.

```python
from whab.plugins import Plugin, StorageBackend, StorageContext

def create_storage(context: StorageContext) -> StorageBackend:
    return MyLedgerBackend(context)

plugin = Plugin(id="my-storage", display_name="My storage", storage=create_storage)
```

Register the descriptor in its own distribution's `whab.plugins` entry points and set `WHAB_STORAGE=my-storage` for the original workspace or initial CLI budget creation. Existing portable budgets instead use the required storage ID in their manifest; Settings creates new SQLite budgets. Its synchronous factory receives `StorageContext(home: Path, environment: str, config: Mapping[str, str])`: resolved runtime home, `sandbox`/`production`, and only backend-prefixed environment keys. The context is frozen; configuration is copied read-only and excluded from its representation. It supplies no bank credentials or token-decryption key.

`StorageBackend` is a synchronous structural protocol. The signatures below omit `self`:

```python
close() -> None
accounts(workspace: str) -> list[dict]
categories(workspace: str) -> list[dict]
transactions(workspace: str, month: str | None = None) -> list[dict]
workspace(workspace: str, month: str | None = None) -> dict
add_account(workspace: str, account: dict) -> str
add_category(workspace: str, category: dict) -> str
add_transaction(workspace: str, transaction: dict, source: str = "manual") -> str
edit_transaction(workspace: str, transaction_id: str,
                 category_id=UNSET, reviewed: bool | None = None) -> None
assign(workspace: str, category_id: str, month: str, assigned: int) -> None
items() -> list[dict]
get_item(item_id: str) -> dict | None
save_item(item_id: str, encrypted_token: str, institution_name: str,
          *, provider_id: str = "plaid") -> None
set_item_status(item_id: str, status: str, error: str | None) -> None
disconnect_item(item_id: str) -> None
apply_sync(item_id: str, changes: SyncChanges, cursor: str | None,
           accounts: list[ImportedAccount] | None = None,
           historical_complete: bool = True, *, expected_cursor=UNSET) -> dict
```

Use the public `StoreError` and single `UNSET` sentinel exported from `whab.plugins`. `UNSET` preserves omitted-category and optional-cursor-guard semantics; a category of `None` explicitly clears it. Raise only authored, safe `StoreError` messages. Unexpected factory failures are sanitized and never cause silent fallback to another backend.

Public account/category/transaction/workspace results must preserve documented HTTP shapes and omit credentials/internal state. `items()` is sanitized; `get_item()` is server-private and may include an encrypted token. Raw state, database handles, schema operations and migration methods are outside this protocol. Bank factories receive a narrower provider-scoped facade, never the full backend.

Optional `ReceiptStorage` is separate from the required backend contract: `receipt_ids(workspace)`, `get_receipt(workspace, transaction_id)`, `save_receipt(workspace, transaction_id, image)` and `delete_receipt(workspace, transaction_id)`. The host bounds and validates JPEG/PNG/WebP bytes and scopes local receipt routes to the addressed budget. Storage owns persistence and portable image data; unavailable adapters report unsupported. Receipts do not enter public ledger projections or the remote command protocol. SQLite includes them in its sanitized portable database.

From WHAB 2.15, optional `ReceiptTransactionLookup.receipt_transaction_exists(workspace, transaction_id)` avoids loading complete public transaction history before a receipt operation. Validate the requested workspace and return a strict boolean: active rows in that workspace are present, deleted rows are absent, and pending rows remain eligible. The host invokes this lookup under the existing budget lock, rejects malformed results, and retains its public `transactions` fallback when the method is absent or noncallable. Receipt operations must still check their transaction when reading or writing, so deletion between the preliminary lookup and the operation cannot grant access. The capability adds no public ledger fields, remote method, or requirement to `StorageBackend` or `ReceiptStorage`; extensions importing its protocol require `whab>=2.15,<3`.

From WHAB 2.17, optional `ReceiptSelectionIndex.receipt_ids_for(workspace, transaction_ids)` returns sorted receipt markers only for a bounded selection. Public `validate_receipt_selection` accepts a list or tuple of one to 100 distinct plain-string IDs, each one to 200 characters, without NUL or invalid UTF-8, and returns an immutable tuple without normalization. Return only requested active identities in the addressed workspace; pending/history rows remain eligible, while missing/deleted/foreign identities are omitted. Read no image payloads. SQLite preserves the full index's workspace and absent-metadata semantics. The host invokes native lookups under its shared lock, validates bounded distinct output, and otherwise intersects the legacy full index on the server. Invalid native output fails without a fallback. The local `POST /api/receipts/index` accepts `{workspace, transactionIds}` and returns the existing `{supported, transactionIds}` shape; `receiptIndexScoped` in interface context advertises this route even when a provider uses the legacy fallback. The full `GET /api/receipts` remains unchanged. This adds no mutation, checkpoint, public ledger field or remote method; extensions importing the capability or validator require `whab>=2.17,<3`.

Account records include `onBudget: bool` and `subtype: str | None`. Validate classification with the shared core helper, exclude off-budget records from envelope funding/activity and preserve saved classification during bank refreshes. The optional `update_account(workspace: str, account_id: str, changes: dict) -> None` operation changes `type`, `subtype`, `onBudget`, `name`, `bankWebsite` and `currency`; use core `domain.commands.update_account` for its rules. It is intentionally outside the required `StorageBackend` method set, so existing plugins still open. `LedgerService` returns an explicit capability error if an older backend lacks it. Current SQLite and remote client plugins support it. From WHAB 2.11, user name overrides survive synchronization, bank websites normalize to public HTTPS origins, and currency changes require no transaction history, including tombstones. Optional transaction `note` updates use an omitted-versus-empty distinction and preserve user memos across provider refreshes.

Implementations must preserve integer currency minor units and separate totals/allocations by currency, workspace/environment isolation, conservative budgets, budget start, valid category references and annotation-preserving pending matches. Imports, account snapshots and cursors commit atomically, with expected-cursor checks and the latest annotations read inside the mutation. Validation/commit failures must leave every ledger field and cursor unchanged; reads must be coherent. Test these guarantees with fictional fixtures.

The host closes owned backends after bank cleanup on shutdown/startup failure; tests own injected backends. SQLite owns ledger selection, additive migrations and seeds. It reopens a single `home/data/*-{environment}.sqlite` file in place, validating the base schema for a noncanonical filename. With no matching file, it creates `home/data/whab-{environment}.sqlite`; multiple matches fail safely, even if one has the canonical filename. Other backends own settings/schema and compatible reopen behavior. Changing `WHAB_STORAGE` does not copy an existing ledger or migrate credentials/history. Configure existing or new data deliberately; cross-backend migration is outside API v1.

## User interfaces

Canonical local page GETs accept `X-WHAB-Page-Fragment: 1` for a selected UI to render only its main-view fragment. Responses acknowledge the marker, remain uncached and vary on the fragment selectors; existing budget/register fragments remain available. The Web interface validates scope, route, query intent and the loaded asset manifest before replacing content. It retains the shell and shared dialogs within one budget/currency, and remounts view controllers after a successful replacement. Optional browser integrations can bind new controls idempotently on `whab:page-ready` and cancel the cancelable `whab:before-navigate` document event while an operation must retain its current view. These DOM events carry no banking data and add no ledger or remote API operation. Cross-budget/currency navigation uses a new document.

The bundled visual descriptor is `web = "whab_plugin_web.plugin:plugin"`. It owns view models, icons, Jinja templates, styles and browser controls. Host code retains HTTP transport, validation, access middleware and business-service dispatch.

```python
from pathlib import Path
from whab.plugins import Plugin, UserInterface

plugin = Plugin(
    id="my-web", display_name="My web interface",
    ui=UserInterface(
        templates=Path(__file__).parent / "templates",
        static=Path(__file__).parent / "static",
        build_context=build_context,
    ),
)
```

Register it in `whab.plugins` and select `WHAB_UI=my-web`. `UserInterface` holds template/static directories and a callable:

```python
def build_context(data: dict, status: dict, items: list[dict], *,
                  workspace: str, page: str, month: str,
                  selected: str | None = None) -> dict: ...
```

Inputs are public ledger/status views, not a backend, settings or credential context. Return custom presentation values; the host supplies `data`, `status`, `workspace`, `page`, `month`, `version`, `bank_panels`, `plugin_scripts`, `plugins`, `plugin_insights` and, from WHAB 1.2, `plugin_management`; WHAB 1.3 also supplies `account_logos`, and WHAB 1.7 supplies `merchant_logos`. The management projection has the same safe fields as `GET /api/plugins/management`; it contains package metadata and selection state, not credentials. A builder can omit bundled helpers. The host renders the selected `base.html`, mounts its static resources at `/static`, and serves the existing four page choices. Templates/scripts are trusted code and must escape user data appropriately. API v1 is this server-rendered HTML/Jinja contract, not an arbitrary renderer factory or native application framework.

Canonical browser pages use `/budgets/<budget-slug>/<page>/<month>/<currency>`; account registers use `/budgets/<budget-slug>/<account-slug>/transactions/<month>/<currency>`. Names supply lowercase readable aliases with numeric suffixes for collisions. Device-local routing metadata preserves old aliases after renames; the permanent IDs remain financial identities. Legacy UUID, `home` and account-ID query links remain accepted, while `demo` selects the startup runtime's separate fictional sample destination. The host resolves aliases to IDs and provides scoped page, API, static-resource and download addresses; integrations must use that context instead of constructing unscoped requests or relying on cookies. In the Web context, `budgetUrl`/`budgetKey` describe canonical browser aliases, `budgetId` is the internal identity, and `apiPrefix`/`asset_prefix` retain ID-scoped addresses. `accountSlugs` maps visible account IDs to aliases, `selectedAccountId` identifies the register, `canonicalUrl` normalizes a legacy address, and `budgetAliases` lists accepted historical budget route keys. The month token is `current-month` or an explicit `yyyy-mm`; projected `month` remains the resolved calendar month. Browser currency segments are lowercase; API/domain ISO codes stay uppercase. Explicit portable-budget launches start only the selected runtime, so original `home` is available on ordinary local launches. Root query-style page links remain unsupported. Existing unscoped API contracts remain available. See [budget addresses](usage.md#budget-addresses).

From WHAB 2.2, a visual descriptor can set `UserInterface(..., budget_months=3)`. This optional integer accepts 1 through 12 and defaults to 1, preserving existing interface input shapes. For budget pages requesting multiple months, the host adds `data['periods']`, a list of consecutive public workspace projections starting with the selected month, each restricted to the selected currency. The list stops at `2200-12`; the ordinary top-level projection remains the selected month. Other pages retain their single-month inputs. The ledger service reads these projections under the shared local command lock using the existing storage `workspace` queries. UI plugins format and arrange those values; core budgeting rules and validated assignment commands remain authoritative. Using this option requires `whab>=2.2,<3`.

From WHAB 2.12, `UserInterface(..., lightweight_transactions=True)` optionally requests a transaction-register projection instead of envelope calculations on transaction pages. It contains selected-currency `accounts`, `categories`, `categoryGroups`, `transactions`, `currency`, `currencyMinorUnits` and available `currencies`; account/date filters apply before rendering. Envelope `budget`, `summary` and `trend` keys are absent. The default remains `False`, preserving the full single-month workspace contract for existing visual plugins. The bundled web plugin opts in.

Storage may implement optional `TransactionRegister.transaction_register(workspace, month, currency, account_id)` to select public account/category metadata and matching transactions in one coherent snapshot without loading envelope history or private provider state. `month=None` includes all dates and `account_id=None` includes all accounts of the currency; unknown or other-currency IDs also mean all accounts of that currency. The ledger service retains its lock and scope checks; backends without this optional capability fall back to their existing public reads. This is a local projection optimization, not a new remotely callable query or financial-page cache.

WHAB 2.13 also supports optional `TransactionRegisterFiltered.transaction_register_filtered(workspace, start_date, end_date, currency, account_ids)`. Bounds are inclusive ISO dates, and `None` leaves that bound open. `account_ids=None` selects all accounts of the currency; an empty tuple selects none. Return public account/category metadata and matching transactions in one coherent snapshot, preserving date/ID order. The ledger service validates and scopes results and falls back to existing public reads when this optimization is absent. Existing storage protocols remain compatible.

From WHAB 2.14, `UserInterface(..., transaction_page_size=100)` requests a bounded register. Zero is the compatible default; positive sizes are limited to 1–1000. `LedgerService.transaction_page` applies account/date scope, literal casefolded payee/memo search, review state and page selection. The usual register fields include only that page's transactions, plus `transactionPage` (`total`, `count`, `page`, `pages`, `start`, `end`, `toReview`) and top-level `toReview`. `total` and `toReview` describe the account/date/currency selection before search/review; `count` describes matches. Pending rows do not contribute to `toReview`. The optional `TransactionRegisterPage.transaction_register_page(workspace, start_date, end_date, currency, account_ids, search, review, page, page_size)` storage capability counts and selects within one snapshot, preserves descending date/ID order and clamps stale pages. Search removes surrounding query whitespace and matches a literal substring of `(merchant + ' ' + note).casefold()`; queries accept up to 2000 characters. Existing register/public reads remain the fallback; transaction exports are unchanged.

`UserInterface(..., lightweight_accounts=True)` requests account/category metadata, core account totals and the selected month's review count without envelope projections. The default is `False`. Storage may implement `AccountOverview.account_overview(workspace, month, currency)` returning all account/category metadata and `toReview` in one snapshot; the host filters currencies and computes totals. `AccountSettings.account_settings(workspace, account_id)` optionally returns a public `account` and explicit boolean `currencyEditable`; all history, including deleted rows, must lock currency. Existing workspace/export fallbacks remain supported, and missing proof never enables a currency change.

`WorkspacePeriods.workspace_periods(workspace, month, count)` optionally returns consecutive ordinary workspace projections from one snapshot. The service validates and caps the range and keeps its shared command lock; other backends retain separate `workspace` reads. These are optional SDK capabilities, not additions to the required `StorageBackend` protocol. The bundled web and SQLite plugins use them from WHAB 2.14.

From WHAB 2.16, optional `BulkReviewStorage.review_transactions(workspace, transaction_ids, reviewed)` atomically sets review flags for one to 100 distinct transaction IDs. `validate_review_batch` validates the bounded selection and exact boolean and returns an immutable tuple. Apply core annotation rules to every target before changing any row; failure must leave the whole batch unchanged. Other columns, amounts, classifications and receipt images remain untouched. The host invokes this as one ledger command and tracked mutation, so it makes one checkpoint attempt under the shared budget lock. Existing saved/pending recovery behavior is unchanged. Legacy providers, including remote clients without this capability, keep their individual-edit path; core never fakes atomicity by looping over independent writes. The optional capability does not change the required `StorageBackend` protocol. Extensions importing it require `whab>=2.16,<3`.

The local bulk-review route accepts an immutable request ID and payload. Replays within the bounded in-process command receipts use the original expected revision and return the first result without another mutation/checkpoint; conflicting payloads still fail. After eviction or restart, a local review-only retry is a fresh idempotent boolean assignment. Remote command callers retain their explicit expected-revision checks. Interfaces must retain an unconfirmed batch ticket and confirm it before sending later edits for its rows; submitted batch errors must not trigger automatic individual-write fallback.

From WHAB 2.9, projections advertise `monthlyFundingFlow: true`. Each currency's `summary` exposes `carriedIn`, `carriedInUnassigned`, `carriedInSetAside`, signed `balanceCarryover`, explicit `assignedNextMonth`, `assigned`, `income`, `readyToAssign` and conservative `fundingCapacity`, all integer minor units. The exact display identity is `readyToAssign = income + carriedIn - assignedNextMonth - assigned`. Carry-in includes previously unassigned funds, the preceding source month's explicit set-aside, and pooled category balances only under Overall balance. By-category balances remain allocated in category rows. The existing `previousOverspending` magnitude remains available for compatible consumers. Source-month transfers and target-month category plans are independent; a set-aside may be smaller than the target's assignments. Core validates edits against applicable monthly pools and actual eligible cash rather than reserving all future category plans in earlier displays.

Backends with saved funding support also advertise `nextMonthFundingEditable: true` for optional `NextMonthFunding.assign_next_month(workspace, month, currency, assigned)`, using the SOURCE month. Interfaces require both capability flags to edit with monthly-transfer semantics; older servers remain read-only until upgraded. The public command `assign_next_month` and local HTTPS `PUT /api/budget/funding/next-month` retain their fields and validation boundaries. Neutral snapshots retain optional `futureFunding` records `{month, currency, assigned}` using TARGET months; earlier records are now explicit set-asides and remain readable without a storage-format migration. Existing backends lacking the command remain valid and reject unsupported writes clearly.

Optional `MonthlyRollover.set_rollover_mode(workspace, month, currency, mode)` saves the outgoing SOURCE month's closing rule. `mode` is exactly `category` or `balance`, defaulting to category when absent. Supporting projections advertise `rolloverModeEditable: true`, selected-currency `rolloverMode`, and `rolloverModesByCurrency` for unfiltered data. `select_currency` selects the proper mode alongside that currency's money. The public command `set_rollover_mode` carries `{month, currency, mode}`; local HTTPS exposes `PUT /api/budget/settings/rollover` with those fields and `workspace`. Valid sources run from the budget start through `2200-11`. Neutral snapshots optionally carry `rolloverModes: [{month, currency, mode}]`; normalized exports include the collection. Rules affect only later category carryovers, preserve both signs, and never mutate accounts, transactions or allocations. General balance carryover must not be combined with income or added to account snapshots.

To show bank controls, include each `bank_panels` template with its scoped `status` and `items`; use `bank['items']` because `bank.items` resolves a dict method in Jinja. Include the enabled provider modules from `plugin_scripts`. The decorative `icon` helper is optional. Provider modules own their HTTP helpers and dialog handlers, so alternate interfaces need not copy private bundled JavaScript.

Bank scripts announce feedback through a cancelable document event, `whab:notice`, with `detail.message`. An interface can display this plain text and call `event.preventDefault()` to mark it handled. The bundled Web interface uses its toast; the provider falls back to a browser alert if unhandled.

`WHAB_UI=` initially selects headless mode: `/` and current visual assets are absent, while guarded APIs and required storage remain. Disabling visuals does not delete data or disable bank services. Storage and UI selection are independent. From WHAB 2.36, a private device-local visual decision overrides that initial value after an explicit live selection. `UserInterface` may supply one complete immutable `PluginResources` bundle instead of template/static paths; a template-only interface may have no static assets. `view_protocol=1` opts into the host's browser participation contract and requires a pure explicit live visual descriptor. Existing descriptors default to protocol zero and retain restart-required compatibility. Presentation helpers execute inside approved visual workers from bounded public projections; the host retains financial rules and transport guards. See [visual interface workers](plugin-workers.md#visual-interface-workers) for preparation, generation URLs, unsaved edits, commit/recovery and exact saved-worker behavior.

## Privileged bank capability

The supported Plaid integration is registered by its independent `whab_plugin_plaid` wheel as `plaid = "whab_plugin_plaid.plugin:plugin"`. It uses the same discovery, descriptor validation, enablement, scoped persistence, UI mounting, and service lifecycle as external bank plugins. The app's composition does not instantiate Plaid independently. Explicitly disabling it prevents its entry point from loading and removes its connection UI, assets, and provider routes, while manual budgeting remains available. Two installed entry points claiming `plaid` fail startup rather than allowing an external package to shadow the bundled integration.

A bank plugin supplies a synchronous factory:

```python
from whab.plugins import BankContext, BankUI, Plugin

def create_bank(context: BankContext):
    return MyBankService(context)

plugin = Plugin(
    id="my-bank",
    display_name="My bank integration",
    api_version=1,
    bank=create_bank,
    bank_ui=BankUI(templates=template_directory, static=static_directory),
)
```

`BankContext(store: BankStore, config: Mapping[str, str], environment: str, encryption_key: str)` is factory-only. Its frozen fields and copied read-only configuration prevent accidental changes. Its representation excludes the store, configuration, and key. The host supplies the selected `sandbox` or `production` environment, only that provider's configured environment keys, and a backend scoped to that provider. The context never goes to insights or export hooks. It is a privileged interface: the backend can read that provider's encrypted tokens and atomically write normalized bank imports; providers must decrypt/encrypt server-side using the supplied key. Never log the context, plaintext tokens, decrypted credentials, raw private provider payloads, or exceptions containing those values.

`BankService` is a structural protocol with these members:

```python
config: Mapping[str, object]  # public status only, never credentials

async def link_token(item_id: str | None = None) -> str: ...
async def exchange(public_token: str, institution_name: str | None = None) -> None: ...
async def sync(item_id: str | None = None) -> dict[str, object]: ...
async def disconnect(item_id: str) -> None: ...
async def webhook(raw: bytes, headers: Mapping[str, str]) -> WebhookResult: ...
async def close() -> None: ...
```

The host creates enabled services during its asynchronous application lifespan and closes created services on shutdown or startup failure. The service owns provider-specific authentication, remote requests, token encryption, mapping, synchronization, webhook verification, event interpretation, and any cancellation/cleanup of its tasks. The host owns generic route dispatch, local access controls, safe status fields, and response shape. Canonical routes use `/api/banks/{id}/items`, `/link-token`, `/exchange`, `/sync`, and `/webhook`, with disconnect at `DELETE /api/banks/{id}/items/{item_id}`. Existing `/api/plaid/*` aliases remain available only while the `plaid` plugin is enabled. `BankError(ValueError)` represents an authored, safe user-facing error; never wrap remote exception text in it. Unexpected provider exceptions are sanitized by the host.

`WebhookResult(ok: bool, status_code: int = 200, error: str | None = None)` supplies a bounded primitive response rather than a provider response dictionary. A successful result uses a 2xx status and no error. A failure must explicitly use a 4xx/5xx status and an optional plain-text, authored error of at most 200 characters. The provider verifies authenticity against the original raw request bytes before parsing or applying any event. The host validates this result before responding.

`BankStore` exposes only provider-scoped persistence:

```python
items() -> list[dict]
get_item(item_id: str) -> dict | None
save_item(item_id: str, encrypted_token: str, institution_name: str) -> None
set_item_status(item_id: str, status: str, error: str | None) -> None
disconnect_item(item_id: str) -> None
item_identity(item_id: str) -> str
apply_sync(
    item_id: str, changes: SyncChanges, cursor: str | None,
    accounts: list[ImportedAccount] | None = None,
    historical_complete: bool = True, *, expected_cursor=UNSET,
) -> dict
```

The concrete backend checks provider ownership and accepts normalized `ImportedAccount`, `ImportedTransaction`, and `SyncChanges` records rather than raw Plaid or other provider JSON. These public `TypedDict` types are exported from `whab.plugins`:

| Record | Required fields | Optional fields |
| --- | --- | --- |
| `ImportedAccount` | `id`, `name`, `institution`, `type`, `balance`, `currency`, `source`, `itemId` | `mask`, `stale`, `balanceUnknown` |
| `ImportedTransaction` | `id`, `accountId`, `date`, `merchant`, `amount`, `suggestedCategoryId`, `pending`, `source`, `inferredTransfer`, `pendingTransactionId` | `merchantLogo` (WHAB 1.7+, opaque artwork key or `None`) |
| `SyncChanges` | `added`, `modified`, `removed` | None |

`added` and `modified` are lists of normalized transactions; `removed` is a list of transaction IDs. Nullable category/pending-match values use `None`. `balance` and `amount` are integer minor units of the account currency, `currency` is a supported uppercase ISO code, and flags are booleans. A transaction inherits its account currency. Existing account currencies cannot change during synchronization; category references must resolve to children of the same currency. Invalid provider suggestions become uncategorized, while incompatible user overrides fail the complete atomic batch. The bundled Plaid adapter still imports USD only. The scoped backend owns provider namespacing of record IDs. It retains only supported fields, preserves matched pending annotations, and commits changes together with the cursor; `expected_cursor` provides an optimistic synchronization guard. `item_identity` is the provider-bound identity for token encryption's associated data, preserving existing Plaid ciphertext identities. The backend exposes no global state, generic database connection, unrelated provider tokens, or close method. This is an API boundary for trusted plugins, not an OS security boundary.

### Bank UI resources

Optional `BankUI(templates: pathlib.Path, static: pathlib.Path)` resources belong to an enabled bank plugin. The host validates directories at startup, loads templates under `plugins/{id}/`, and mounts static files at `/plugins/{id}/static`. The accounts fragment is `accounts.html`; the provider's browser connection script is `connect.js`. A UI descriptor requires a bank factory. A disabled provider gets no mounted assets or templates. These are explicitly trusted provider templates and scripts, unlike report hooks' escaped text; they must never embed server secrets or access tokens. The host supplies `status` (`configured`, `environment`, `liveEnabled`, `missingConfig`, `enabled`) and `items` (sanitized connection records) to the accounts fragment. Services use a public `config` mapping with `configured: bool`, `liveEnabled: bool`, and optional environment-variable names in `missingConfig`; never include secret values. The host supplies the environment from its own settings. The bundled Plaid adapter retains its legacy `plaidConfigured` service key, translated by the host. No plugin can register arbitrary HTTP routes through this descriptor.

The current connection lifecycle uses Link-style short-lived tokens and public-token exchange. An external bank plugin must implement that contract and its own connection UI. A provider requiring a different consent workflow may need a later compatible API addition; claiming a `bank` descriptor does not by itself make every institution or aggregation service supported.

## Merchant artwork supplied by a bank (WHAB 1.7+)

A bank descriptor can add `merchant_logos=MerchantLogos(fetch=fetch)` using the public `MerchantLogos` record. It requires a `bank` factory and stays part of that bank plugin's selection. The bundled Plaid plugin supplies this capability; there is no additional merchant-logo plugin to enable. A descriptor with this capability advertises `has_merchant_logos=True` and the `branding` kind alongside its ingestion kind.

The adapter may set `ImportedTransaction.merchantLogo` to an opaque public artwork key: 1–200 ASCII letters, digits, underscores or hyphens, beginning with a letter or digit. Omitted or `None` means unavailable. The public transaction projection includes the nullable `merchantLogo` key; reporting snapshots and CSV exports do not include artwork. Backends supporting artwork retain it through the atomic import and pending-to-posted merge, defaulting existing rows to `None` through a compatible migration. A later import/update replaces it with the provider's current value; there is no automatic historical backfill or reconnection.

The host supplies `merchant_logos`, a map from current transaction IDs to local image URLs, only for enabled bank descriptors with this capability and valid artwork keys. Templates should use that map and keep the payee visible if an image cannot load. `fetch(key: str) -> LogoImage | None` runs only on `GET /api/plugins/{id}/merchant-logos/{key}` and receives the opaque key without a transaction, bank service, backend, settings or credentials. As with bank logos, providers must allowlist keys and destinations, bound bytes, disable redirects and use a short timeout. The host revalidates `LogoImage`, returns a sanitized 404 for misses/errors and shares a bounded 128-entry memory cache across bank/merchant images, with separate keys for each capability. Successes expire after 24 hours and failures after five minutes; the cache clears on restart. Local Host/origin and no-store guards apply. Discovery, startup, synchronization and page rendering never download artwork.

Plaid maps only its supplied top-level `logo_url` to keys such as `merchant-walmart_1100`. It accepts canonical HTTPS PNG URLs on `plaid-merchant-logos.plaid.com` and `plaid-counterparty-logos.plaid.com`, then reconstructs those fixed CDN URLs when fetching. No arbitrary URL, website matching, merchant alias list, LLM or banking credentials enter the artwork fetch. The original merchant-name selection is unchanged. Extensions using this API should require `whab>=1.7,<3`.

## Bank-logo branding (WHAB 1.3+)

Optional branding is independent of the selected UI and bank-ingestion services. The bundled descriptor is registered as `bank-logos = "whab_plugin_bank_logos.plugin:plugin"` and is disabled by default. Enable it through Settings and restart, or select `WHAB_PLUGINS=plaid,bank-logos` when there is no saved selection. See README for its source, coverage and privacy behavior.

```python
from whab.plugins import BankLogos, LogoImage, Plugin

def resolve(institution: str) -> str | None:
    # Local lookup only. Never infer a URL from arbitrary institution text.
    return "example-bank" if institution == "Example Bank" else None

def fetch(key: str) -> LogoImage | None:
    # Fetch only known public icons from the provider's fixed source.
    # Return LogoImage(data=png_bytes, media_type="image/png") on success.
    return None

plugin = Plugin(
    id="my-logos", display_name="Institution logos",
    bank_logos=BankLogos(resolve=resolve, fetch=fetch),
)
```

`BankLogos` is frozen and has two required synchronous hooks plus an optional `configure_cache` hook (WHAB 1.6+). WHAB 2.11 adds optional `resolve_domain(hostname)` and public `provider_name` / `provider_url` attribution. This local-only resolver receives an explicitly saved public hostname, never an account record. Its bounded opaque key uses the same guarded image-fetch route; provider endpoints remain fixed and must not fetch arbitrary supplied URLs. Interfaces use attribution to explain the external provider before saving a website. `resolve(institution: str) -> str | None` receives only institution display text (at most 200 characters), and must perform a local lookup without remote requests. A match returns an opaque lowercase slug of at most 64 characters, using the same syntax as plugin IDs; unknown names return `None`. The first enabled valid match wins. The host supplies `account_logos`, a map of account IDs to local image URLs, to the selected UI context; unmatched accounts are omitted. Interfaces may ignore the map and should retain a visible icon/initial fallback.

The local `POST /api/plugins/bank-logo-preview` accepts `{website}`, normalizes optional user input and returns availability, provider attribution and a guarded local image URL after a lookup. It never changes account metadata. Invalid or unavailable artwork returns a neutral result. The same hostname-only boundary applies.

`fetch(key: str) -> LogoImage | None` runs only when the preview is requested or the guarded host route `GET /api/plugins/{id}/bank-logos/{key}` is requested. It receives a logo key, never an account, snapshot, backend, settings or credentials. It must allowlist its keys and remote destinations, bound streamed bytes, disable redirects and use a short timeout. `LogoImage(data: bytes, media_type: str)` accepts up to 128 KiB of PNG, JPEG, GIF or WebP bytes with matching file signatures; SVG, HTML and arbitrary URLs are unsupported. The host revalidates the returned record and serves the bytes with `nosniff`; this is signature checking, not complete image decoding. Hook errors, malformed results, unknown keys and disabled plugins return a sanitized 404, allowing the UI fallback.

Without `configure_cache`, the registry caches up to 128 images/misses in memory, coalesces concurrent fetches, and retries after 24 hours for successes or five minutes for failures; that fallback cache clears on restart. An optional `configure_cache(directory: Path) -> None` receives only `WHAB_HOME/cache/plugins/{id}/`, with no credentials or ledger context. It must prepare local storage without fetching images; a provider using it owns bounded caching, expiry and concurrent request coalescing. The bundled Bank logos provider keeps validated successful images indefinitely across restarts and memory eviction; failed requests stay in memory for five minutes. Its Plugin Settings advertises cache information and a Flush cache action. Flushing clears downloaded artwork and failed requests while preserving explicit hostname mappings and saved account websites; the next image request downloads again. HTTP responses retain the host's `no-store` policy, while successful image requests read the persistent provider cache. Page rendering, startup, plugin discovery and selection saving never fetch images. Headless ledger requests do not fetch images. Python plugins remain trusted code; these scoped inputs do not sandbox a plugin's own network or filesystem access. External branding packages should require `whab>=1.3,<3`.

## Plugin settings (WHAB 1.6+)

Every visible installed-plugin row has a Settings page in the Web UI; developer teaching examples remain hidden. Active plugins may add structured details and declared actions through the optional `Plugin.settings` capability:

```python
from whab.plugins import Insight, PluginAction, PluginSettings

settings = PluginSettings(
    read=lambda: (Insight("Cache duration", "365 days"),),
    actions=(PluginAction("flush-cache", "Flush cache", "Fetch again on the next image request."),),
    act=perform_action,
)
```

`read() -> tuple[Insight, ...]` returns at most 50 validated plain-text details. `actions` is a tuple of at most 50 `PluginAction(id, label, description="")` controls with unique lowercase slug IDs. Labels are at most 120 characters; descriptions and result messages are at most 2,000 characters. `act(action_id: str) -> str` performs a declared action and returns a safe plain-text result message. Hooks receive no ledger, credentials or application configuration. The host validates records and outputs, rejects undeclared actions before calling plugin code, and sanitizes unexpected exceptions. The interface escapes all plugin text. Settings supplement an existing capability; they do not make an otherwise empty descriptor valid.

Host-owned `GET /api/plugins/{id}/settings` returns `insights` and `actions` for an active plugin, with empty lists when it has no custom settings. `POST /api/plugins/{id}/settings/actions/{action_id}` invokes a declared action and returns `message` and refreshed `settings`. Send JSON (`{}` is sufficient); local Host/origin, request-size and no-store guards apply. Unknown/inactive plugins and unavailable actions return sanitized errors. A successful action also clears that plugin's fallback host logo cache. Browsing inactive settings reads only the installed metadata projection and does not execute hooks. Enabling/disabling still applies after restart; an action such as flushing the active logo cache takes effect immediately.

The management projection's `category` is one of `interface`, `ingestion`, `insight`, `storage`, `automation`, `extension` or `example`. Bundled registrations can be categorized without importing disabled descriptors; otherwise running capabilities supply a category and unknown registrations use `extension`.

## Runtime interface for host integration and tests

```python
load_plugins(
    enabled: tuple[str, ...],
    entrypoints: Sequence[importlib.metadata.EntryPoint] | None = None,
) -> PluginRegistry

registry.describe() -> tuple[PluginDescription, ...]
registry.descriptors() -> tuple[Plugin, ...]  # Added in WHAB 1.2.
registry.storage_plugin(plugin_id: str) -> Plugin
registry.ui_plugin(plugin_id: str) -> Plugin
registry.bank_plugins() -> tuple[Plugin, ...]
registry.bank_logo(institution: str) -> tuple[str, str] | None  # (plugin ID, logo key)
registry.logo_image(plugin_id: str, key: str) -> LogoImage | None
registry.merchant_logo_image(plugin_id: str, key: str) -> LogoImage | None  # Added in WHAB 1.7.
registry.configure_logo_caches(home: Path) -> None  # Added in WHAB 1.6.
registry.settings(plugin_id: str) -> dict  # tuples of validated insights/actions
registry.settings_action(plugin_id: str, action_id: str) -> str
registry.insights(snapshot: BudgetSnapshot) -> tuple[PluginInsightResult, ...]
registry.export(plugin_id: str, snapshot: BudgetSnapshot) -> ExportTable
```

An optional entry-point sequence lets tests supply fake installed packages. `PluginDescription` includes `id`, `display_name`, `api_version`, `has_ui`, `has_storage`, `has_bank`, `has_insights`, `has_export`, `has_bank_logos` (added in 1.3), `has_file_import` (added in 1.4), `has_settings` (added in 1.6), `has_merchant_logos` (added in 1.7) and computed `kinds`. `descriptors()` returns the loaded immutable descriptors without executing their hooks or factories, so the host can reuse required plugins when validating a pending selection. Storage/UI selectors require a unique enabled descriptor with the requested capability; the host skips UI selection when headless. `bank_plugins()` returns descriptors without creating services. Host composition supplies contexts and owns lifecycle. `PluginInsightResult` contains `plugin_id`, `display_name`, `insights` and an optional sanitized `error`. Failures use authored `PluginError` messages with `code` and optional `plugin_id`; never append underlying exceptions or private records.

API v1's documented records, hook signatures, and runtime methods are part of WHAB's SemVer contract. Compatible additions may ship in minor releases; changing/removing their meaning or shape requires a major WHAB release and a new plugin API version. WHAB 2.0's HTTPS requirement changes the HTTP deployment contract while preserving SDK API v1. WHAB rejects incompatible descriptors before executing hooks.

## Local file imports (WHAB 1.4+)

The optional bundled `ynab` descriptor uses a credential-free `FileImport` capability, independently of the bank connection lifecycle:

```python
from whab.plugins import FileImport, Plugin, WorkspaceImport

def parse_export(raw: bytes) -> WorkspaceImport:
    # Normalize the complete archive. No store, settings or credentials are supplied.
    ...

plugin = Plugin("my-import", "My importer", file_import=FileImport(parse=parse_export))
```

`Plugin.file_import` defaults to `None`. `FileImport(parse, templates=None, static=None)` is frozen and requires a synchronous parser; optional `pathlib.Path` template/static directories must be supplied together. The template fragment retains the name `accounts.html` and the browser module is `import.js`, mounted under `imports/{id}/` and `/imports/{id}/static/`. `import_panels` and their `plugin_scripts` are supplied on every visual page; the bundled web interface renders each enabled importer only in that plugin's Settings dialog. Alternate interfaces choose their own placement. Headless mode retains the HTTP capability. Disabling the plugin removes its resources and prevents parsing. This is an additive API v1 capability, classified as `data_ingestion`; `PluginDescription.has_file_import` and `registry.file_import_plugins()` expose it without invoking the parser.

`WorkspaceImport` holds tuples of plain dictionaries in `accounts`, `categories`, `transactions` and `assignments`, a `budget_start_month`, a tuple of plain-text `warnings`, and an optional `currency` (default `USD`) inherited by every imported account and category. Its public `validated_copy()` checks types, field sets, references, money bounds and duplicate IDs/assignments, and returns independent records. Its exact fields are documented in [`domain/file_imports.py`](../app/src/whab/domain/file_imports.py). Unknown account types use `other`, `stale=True` and `balanceUnknown=True` so they cannot create budget funding. Transaction dictionaries preserve notes, review state and transfer annotations. Assignments use `month`, `categoryId`, `assigned`, including negative historical reallocations.

The host accepts raw ZIP bytes at `POST /api/plugins/{id}/import/preview` and `/commit`, using `Content-Type: application/zip` and an explicit `X-Import-Currency` matching the parser result currency (`USD` for the bundled YNAB parser). Uploads are bounded to 10 MiB before request parsing, including chunked bodies; all ordinary APIs retain their existing content types and limits. Host/origin checks and no-store responses apply. A preview returns SHA-256 `fingerprint`, account/category/transaction/assignment counts, `budgetStartMonth`, warnings, `canImport`, a plain-text `reason` and `alreadyImported`. It does not save financial data. Commit requires the same file and `X-Import-Fingerprint` from that preview; it reparses and validates before a storage transaction. Unexpected parser failures are sanitized. Authored `StoreError` messages must never contain private row values.

WHAB 2.10 adds `/api/plugins/{id}/import/new/preview` and `/new/commit` for creating a separate SQLite budget. Both also require a canonical UUID in `X-Import-Operation`. WHAB 2.21 accepts an optional `X-Import-Budget-Name` header containing a UTF-8 name encoded with percent escapes (as by `encodeURIComponent`). Names are trimmed, must contain 1–100 characters and cannot contain NUL; omitting the header retains the legacy `Imported budget` default. Both preview and commit validate the name. Retries preserve the name saved by the original operation, including any later rename. Preview validates only and returns `createsNewBudget: true`; it creates no budget, catalog entry or operation record. Commit returns `id`, a canonical Accounts `url`, `opened: true`, `counts` and `alreadyImported`. Reuse the same operation UUID after an uncertain commit result; the durable operation identity is bound to the source budget, plugin and archive fingerprint, so retries reopen the same target. A different archive or source cannot reuse that operation. A new intentional import uses a fresh UUID. The host saves the imported budget before publishing it in the catalog and copies no source financial data, bank connections, plugin settings or credentials.

Storage support is optional, separate from the unchanged required `StorageBackend` protocol. The public `WorkspaceImporter` protocol supplies:

```python
preview_workspace_import(workspace, plugin_id, fingerprint) -> dict
import_workspace(workspace, plugin_id, fingerprint, data: WorkspaceImport) -> dict
```

Preview reports availability without mutation. Commit returns `{"counts": {"accounts": ..., "categories": ..., "transactions": ..., "assignments": ...}, "alreadyImported": bool}`. SQLite allows only a pristine personal workspace, replacing untouched seed categories; financial records, assignments, customized categories and even disconnected bank connection records block the migration. It revalidates under the write lock, committing all data, budget start and a receipt atomically. A receipt for the identical plugin/fingerprint makes retries harmless and preserves later edits. Any different subsequent archive is refused. Other backends can keep working without this capability and report imports unavailable. See the [YNAB importer](../app/plugins/ingestion/ynab/README.md) for its format-specific limits.

## Remote interface and client

`Interface(factory)` is a trusted transport capability separate from the server-rendered `UserInterface`. The host calls its factory with `InterfaceContext(service, config, environment, budget_id, budget=None)`. The `service` provides scoped public ledger queries and validated commands; it is not a bank store. `config` contains the plugin's machine-specific configuration. A `BudgetContext` provides portable settings/state and namespace-scoped file access where the host supplies it. Save state as bounded JSON. Machine credentials stay local by default; explicitly exported remote credentials use the container’s password-protected credential payload.

The supported live API form is `Interface(adapter=RemoteLedgerInterface(protocol_version=1, token_setting="SERVER_TOKEN", queries=True, commands=True))` with an explicit `PluginContract(lifecycle="live")`. Supply either a factory or an adapter. The data-only adapter declares the existing `/remote/v1` protocol; at least one operation class must be enabled. `token_setting` names a bounded uppercase configuration key in that plugin's own machine-local configuration. The host validates a 32 through 4,096 character token and keeps the token, request authorization, strict command validation and ledger dispatch outside workers. `InterfaceContext.config` is omitted from its representation.

There is exactly one declarative v1 route owner per budget. Duplicate declarative owners and mixtures with legacy factories fail before configuration or factories run. Supported selection changes prepare a generation-owned binding, drain complete admitted requests, persist the decision and retire the old binding. Failed preparation leaves the active binding unchanged. Ledger revisions and retry receipts survive successful optional-interface changes. Disabled remote routes retain HTTPS and local Host/origin guards and return 404 locally. Legacy factory routes preserve their URL compatibility and restart-required lifecycle. [Approved worker upgrades](plugin-workers.md) exchange only a strict interface declaration, require the current host artifact, and restore the saved exact environment on reopen; they never exchange routers, service handles, credentials or executable objects.

The bundled `server` descriptor belongs to Interface and contributes `/remote/v1`. It requires `SERVER_TOKEN` with at least 32 characters and accepts HTTPS only. Its machine/server credential authorizes budgets explicitly configured with the server plugin; this is not a per-household account system. The bundled `client` descriptor belongs to Storage, with `CLIENT_URL` (an HTTPS origin), `CLIENT_TOKEN` and `CLIENT_BUDGET_ID`. Select `WHAB_STORAGE=client` for a client instance and disable local bank plugins there; bank connections run on the server. The connection payload records its public HTTPS origin and remote budget identity. A machine token is used only when the device-configured `CLIENT_URL` and `CLIENT_BUDGET_ID` match that target. Alternatively, the host can supply an explicitly unlocked password-encrypted remote credential bound to that exact origin and budget. Saving or opening the connection container does not fetch the financial ledger.

The remote public protocol exposes accounts, categories, transactions, workspace projections and portable ledger snapshots. Commands support manual accounts/categories/transactions, account classification changes, transaction edits and allocations. A command includes `operation`, `workspace`, `payload`, `request_id` and `expected_revision`. Unknown operations/fields fail validation. Responses include `budgetId`, `revision` and `data`; the client verifies budget identity. Bank tokens, Item operations and synchronization cursors are outside this protocol.

The server serializes public operations per budget. A stale revision returns a conflict requiring refresh and review. It retains at most 1,024 in-memory request receipts so a retry with the same ID and body can return the committed result; reusing the ID for different content fails. Revisions change on server restart, preventing a stale retry from silently replaying when receipts are lost. If a response is lost, the client preserves the pending command and requires retrying that same change before a different mutation. It reports explicit failures and never falls back to another ledger.

Every route requires HTTPS, including localhost and webhooks. The launcher uses native TLS and rejects plaintext rather than redirecting or trusting forwarded proxy headers. The visual application and private banking routes remain loopback-only with Host/origin checks. Only explicitly enabled `/remote/v1` protocol routes and registered signed bank-webhook paths bypass those local checks: the remote plugin requires bearer authorization and webhook providers must verify signatures before processing. TLS encryption is separate from those authentication checks. Certificate paths and explicit local CA trust are described in [usage](usage.md#local-https); exposing selected remote/webhook routes must preserve the other local restrictions. Enabling a server capability does not provide a household login system.

## Extension scope

The current host and supported plugins provide selected server-rendered interfaces, required storage backends, bank-service factories with scoped normalized imports and optional merchant artwork, bounded local file imports, reports, and optional bank-logo branding. Participating visual descriptors support coherent live workers and browser handovers; local SQLite/JSON migration and copies use explicit previewed authority transitions. Installing or selecting a package alone never migrates a budget. The trusted `Interface` hook supplies scoped transport routes; family login authentication is not implemented. Plaid is the bundled bank provider, not proof of institution access. Every backend/provider must preserve monetary rules, isolation, encrypted credentials, annotations and atomic checkpoints.

## Core category and currency projections

Public child category records retain `id`, `name`, `group` and `target`, and add `parentId` and `currency` (legacy values default to USD). `categoryGroups` contains exactly two levels: `{id, name, children}` parents containing child records. Parent IDs use a reserved `category-group:` prefix derived from the group label; only child IDs may occur on transactions, targets or budget rows. Category creation accepts a `group` label or an existing `parentId`, plus a currency. Passing a child as `parentId` or requesting deeper nesting fails. Parents have no assignable amounts.

Workspace accounts and transactions identify their currency; transactions inherit it from the account. Unfiltered `/api/workspace` returns all records and `summariesByCurrency`/`trendsByCurrency`. Its legacy `summary` and `trend` are USD only. `?currency=EUR` returns a homogeneous view; the Web interface and reporting/export snapshots always select one currency. All cash, debt, rollover, activity and allocation calculations remain separate, with no FX. `/api/export` appends `currency` after the existing CSV fields and supports optional currency filtering. The `amount_cents` field retains its legacy name and contains minor units of the identified currency.


## Categorization capability

WHAB 2.19 adds `Categorization(factory)` to the public SDK. The factory receives a `BudgetContext` providing only the plugin's namespaced files. Its engine supplies `defaults`, `index()`, `train(rows, parameters)` as a cooperative iterator returning a candidate model, `save(model, name)`, `select(id)`, `rename(id, name)`, `delete(id)`, and `predict(rows)`. Training rows are detached snapshots of categorized, reviewed, posted transactions; inference rows are uncategorized and unreviewed. Both exclude transfers and unsupported splits. Fields include payee (`merchant`), account ID/type/subtype, amount in minor units, date, currency, category ID, and review status. Engines must never infer training eligibility from a category alone.

Factories and engine methods are synchronous; optional `close()` releases owned resources. The [live categorization selection adapter](plugin-runtime.md#live-categorization-selection) prepares explicit live categorizers with detached read-only namespaces after draining, then activates their callbacks only after selection commits. Unfinished training defers a changed categorizer until finish/cancel; unchanged engines retain their continuations. Retired callbacks reject reads and writes. The [categorization worker adapter](plugin-workers.md#categorization-workers) runs installed pure engines in a child, keeps training/model objects there, and supports approved immutable code upgrades. Complete namespace effects and original result receipts commit in the host before materialization; interrupted saves recover without rerunning plugin code.

The host owns suggestion identities, input fingerprints, currency checks, dismissal, acceptance, ingestion triggers, and settings-session expiry. Engines return `transactionId`, `categoryId`, `modelId`, and a finite `score` between zero and one. A prediction never writes the ledger category or marks a transaction reviewed. The host saves suggestions in `suggestions-personal.json` or `suggestions-demo.json` within the owning plugin namespace. Demo model files use a separate `demo/` namespace. Models and accepted/dismissed suggestion provenance remain data-only portable files.

`GET /api/categorization/suggestions?workspace=personal` returns eligible current suggestions. `POST /api/categorization` accepts `{workspace, payload}`. Payload actions are `status`, `train`, `step`, `cancel`, `select`, `rename`, `delete`, `suggest`, and `dismiss`, scoped by `pluginId`; training steps carry an opaque session ID. Each settings request advances at most one engine yield, with no training between requests. Only the initiating view retains that session ID; close/pagehide cancels it and inactivity expires it. `PATCH /api/transactions/{id}` accepts `suggestionId` with `reviewed: true` and an optional selected `categoryId`, validating the displayed identity before committing category and review together. Stale suggestions cannot be silently accepted.

The same behavior is available through public ledger query `category_suggestions`, command `categorization`, and the additive `suggestionId` edit field. The API interface remains budget-scoped and bearer-authenticated; the remote client forwards requests to the authority rather than training locally. Older hosts report unsupported operations. Installed categorization plugins remain optional and disabled until selected. See [Random forest categorization](../app/plugins/automation/random-forest-categorization/README.md) for features, settings, evaluation, limits, and usage.
