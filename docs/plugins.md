# WHAB plugins — API v1

WHAB 1.1 provides **user-interface**, **storage**, **data-ingestion** and **reporting** plugin capabilities. Plugins are installed Python packages registered through `whab.plugins` entry points. Web, SQLite and Plaid are bundled descriptors using those same contracts. Reports receive immutable snapshots; storage and bank factories receive separate privileged contexts.

## Trust and enablement

**Installed Python plugins are trusted code running in WHAB's server process, not a security sandbox.** Reports receive no backend, settings or credentials. Bank factories receive scoped persistence/configuration and an encryption key. Storage factories own the full ledger and backend credentials; visual descriptors provide trusted templates, scripts and presentation code. Python can independently access files/network or import internals with WHAB's privileges. Select only reviewed code. These interfaces limit host-supplied capabilities and accidental mutation, not malicious code.

Installation alone does not select an additional package. The host loads the required `WHAB_STORAGE` descriptor (`sqlite` by default), selected `WHAB_UI` descriptor (`web` by default), and optional `WHAB_PLUGINS` names (`plaid` when absent). `WHAB_UI=` is headless: APIs/storage remain without the root page or core visual assets. `WHAB_PLUGINS=` disables optional banks/reports while retaining storage and selected UI. Restart after changing settings. Names are lowercase slugs of at most 64 characters.

Select storage and visual implementations through their dedicated settings. Exactly one backend is required; at most one visual interface may be selected. Missing selections, incompatible capabilities and multiple enabled storage/UI descriptors fail with actionable errors. The host deduplicates a selected name also present in the optional list; repeated optional names remain invalid. A standalone `load_plugins(())` loads nothing, but app composition adds required storage and selected UI before discovery.

An enabled name must match exactly one entry point and its descriptor ID. A duplicate setting name, duplicate installed entry point, missing package, invalid descriptor, or unsupported API version prevents startup with an actionable message. WHAB resolves all configured names before importing any plugin; it never silently chooses one of two installed packages claiming the same name. Ordinary import or hook exceptions are reported through fixed messages without their original exception text or traceback in WHAB's public responses/logging. Arbitrary plugin code can still produce its own logs or hang, so this is error handling rather than sandboxing.

## Install the working example

Run these commands from a WHAB source checkout using the same virtual environment as the server:

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pip install -e examples/whab-example-plugin
```

Add this line to the private local `.env`, then restart WHAB:

```dotenv
WHAB_PLUGINS=plaid,example
```

Open **Insights** for either the fictional demo or your selected personal month. The example shows cleared net spending and income. Pending transactions and transfers are excluded from these totals; categorized inflows count as refunds, while uncategorized inflows count as income. Imported income is analytics only and does not increase WHAB's assignable cash. The example export includes all selected-month transactions with pending/transfer flags and integer cents.

To disable optional extensions, use `WHAB_PLUGINS=` and restart. Removing that setting restores default Plaid. Storage remains required, and the selected UI remains until `WHAB_UI=` requests headless mode. Disabling only the example uses `WHAB_PLUGINS=plaid`. Disabling a bank does not delete records or create another connection. The example package has its own version; public compatibility uses `api_version=1`.

## Public SDK

Import public types from `whab.plugins`; concrete database, visual/transport internals and global configuration are outside the stable SDK. Descriptors may provide `ui`, `storage`, `bank`, `insights` and `export`. Their computed `kinds` are `user_interface`, `storage`, `data_ingestion` and `reporting`; one descriptor may have multiple kinds. At least one capability is required. Data ingestion currently means bank connection/sync, not arbitrary file imports.

Frozen report records use immutable tuples and exact primitives. Every hook receives fresh records from a whitelisted snapshot, protecting against accidental mutation. Storage, visual and bank capabilities are described below.

```python
from whab.plugins import (
    AccountSnapshot, BudgetSnapshot, CategorySnapshot, TransactionSnapshot,
    ExportTable, Insight, Plugin,
)

AccountSnapshot(
    id: str, name: str, type: str, subtype: str, balance_cents: int,
    balance_unknown: bool = False, stale: bool = False,
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

Amounts are **integer USD cents**, never float dollars. Transactions use WHAB's signed convention: positive is an outflow, negative is an inflow. Category activity is net categorized cleared spending, so refunds reduce it. Available money may include rollover from earlier months, while account balances are the host's snapshots rather than a historical month-end reconstruction. Check `stale` and `balance_unknown` before reporting available account cash. Transaction notes, Plaid Items/tokens, raw provider responses, broader transaction history, and configuration are deliberately absent.

### Insights hook

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

At least one UI descriptor, storage factory, bank factory or reporting hook is required. The entry point resolves directly to the `Plugin` object, not a factory. Register it in the package's `pyproject.toml`:

```toml
[project.entry-points."whab.plugins"]
my-summary = "my_package:plugin"
```

Set `WHAB_PLUGINS=plaid,my-summary` after installing the package to keep the default bank connection capability. Multiple names use commas; configured order controls insight panel order. Your package should declare a compatible WHAB dependency, for example `whab>=1.1.0,<2` for this API.

## Storage backends

The bundled SQLite descriptor is registered as `sqlite = "whab.storage.sqlite.plugin:plugin"`. Host code imports `StorageBackend` rather than a concrete store or database driver. The SQLite descriptor opens its backend only when the selected factory runs.

```python
from whab.plugins import Plugin, StorageBackend, StorageContext

def create_storage(context: StorageContext) -> StorageBackend:
    return MyLedgerBackend(context)

plugin = Plugin(id="my-storage", display_name="My storage", storage=create_storage)
```

Register the descriptor in `whab.plugins` and set `WHAB_STORAGE=my-storage`. Its synchronous factory receives `StorageContext(home: Path, environment: str, config: Mapping[str, str])`: resolved runtime home, `sandbox`/`production`, and only backend-prefixed environment keys. The context is frozen; configuration is copied read-only and excluded from its representation. It supplies no bank credentials or token-decryption key.

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

Implementations must preserve integer USD cents, workspace/environment isolation, conservative budgets, budget start, valid category references and annotation-preserving pending matches. Imports, account snapshots and cursors commit atomically, with expected-cursor checks and the latest annotations read inside the mutation. Validation/commit failures must leave every ledger field and cursor unchanged; reads must be coherent. Test these guarantees with fictional fixtures.

The host closes owned backends after bank cleanup on shutdown/startup failure; tests own injected backends. SQLite owns `home/data/ihab-{environment}.sqlite`, additive migrations and seeds. Other backends own settings/schema and compatible reopen behavior. Changing `WHAB_STORAGE` does not copy an existing ledger or migrate credentials/history. Configure existing or new data deliberately; cross-backend migration is outside API v1.

## User interfaces

The bundled visual descriptor is `web = "whab.interfaces.web.plugin:plugin"`. It owns view models, icons, Jinja templates, styles and browser controls. Host code retains HTTP transport, validation, access middleware and business-service dispatch.

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

Inputs are public ledger/status views, not a backend, settings or credential context. Return custom presentation values; the host supplies `data`, `status`, `workspace`, `page`, `month`, `version`, `bank_panels`, `plugin_scripts`, `plugins` and `plugin_insights`. A builder can omit bundled helpers. The host renders the selected `base.html`, mounts its static resources at `/static`, and serves the existing four page choices. Templates/scripts are trusted code and must escape user data appropriately. API v1 is this server-rendered HTML/Jinja contract, not an arbitrary renderer factory or native application framework.

To show bank controls, include each `bank_panels` template with its scoped `status` and `items`; use `bank['items']` because `bank.items` resolves a dict method in Jinja. Include the enabled provider modules from `plugin_scripts`. The decorative `icon` helper is optional. Provider modules own their HTTP helpers and dialog handlers, so alternate interfaces need not copy private bundled JavaScript.

Bank scripts announce feedback through a cancelable document event, `whab:notice`, with `detail.message`. An interface can display this plain text and call `event.preventDefault()` to mark it handled. The bundled Web interface uses its toast; the provider falls back to a browser alert if unhandled.

`WHAB_UI=` selects headless mode: `/` and core visual assets are absent, while guarded APIs and required storage remain. Disabling visuals does not delete data or disable bank services. Storage and UI selection are independent.

## Privileged bank capability

The bundled Plaid integration is a plugin registered by WHAB's own wheel as `plaid = "whab.integrations.plaid.plugin:plugin"`. It uses the same discovery, descriptor validation, enablement, scoped persistence, UI mounting, and service lifecycle as external bank plugins. The app's composition does not instantiate Plaid independently. Explicitly disabling it prevents its entry point from loading and removes its connection UI, assets, and provider routes, while manual budgeting remains available. Two installed entry points claiming `plaid` fail startup rather than allowing an external package to shadow the bundled integration.

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
| `ImportedTransaction` | `id`, `accountId`, `date`, `merchant`, `amount`, `suggestedCategoryId`, `pending`, `source`, `inferredTransfer`, `pendingTransactionId` | None |
| `SyncChanges` | `added`, `modified`, `removed` | None |

`added` and `modified` are lists of normalized transactions; `removed` is a list of transaction IDs. Nullable category/pending-match values use `None`. `balance` and `amount` are integer USD cents, `currency` is `USD`, and flags are booleans. The scoped backend owns provider namespacing of record IDs. It retains only supported fields, preserves matched pending annotations, and commits changes together with the cursor; `expected_cursor` provides an optimistic synchronization guard. `item_identity` is the provider-bound identity for token encryption's associated data, preserving existing Plaid ciphertext identities. The backend exposes no global state, generic database connection, unrelated provider tokens, or close method. This is an API boundary for trusted plugins, not an OS security boundary.

### Bank UI resources

Optional `BankUI(templates: pathlib.Path, static: pathlib.Path)` resources belong to an enabled bank plugin. The host validates directories at startup, loads templates under `plugins/{id}/`, and mounts static files at `/plugins/{id}/static`. The accounts fragment is `accounts.html`; the provider's browser connection script is `connect.js`. A UI descriptor requires a bank factory. A disabled provider gets no mounted assets or templates. These are explicitly trusted provider templates and scripts, unlike report hooks' escaped text; they must never embed server secrets or access tokens. The host supplies `status` (`configured`, `environment`, `liveEnabled`, `missingConfig`, `enabled`) and `items` (sanitized connection records) to the accounts fragment. Services use a public `config` mapping with `configured: bool`, `liveEnabled: bool`, and optional environment-variable names in `missingConfig`; never include secret values. The host supplies the environment from its own settings. The bundled Plaid adapter retains its legacy `plaidConfigured` service key, translated by the host. No plugin can register arbitrary HTTP routes through this descriptor.

The current connection lifecycle uses Link-style short-lived tokens and public-token exchange. An external bank plugin must implement that contract and its own connection UI. A provider requiring a different consent workflow may need a later compatible API addition; claiming a `bank` descriptor does not by itself make every institution or aggregation service supported.

## Runtime interface for host integration and tests

```python
load_plugins(
    enabled: tuple[str, ...],
    entrypoints: Sequence[importlib.metadata.EntryPoint] | None = None,
) -> PluginRegistry

registry.describe() -> tuple[PluginDescription, ...]
registry.storage_plugin(plugin_id: str) -> Plugin
registry.ui_plugin(plugin_id: str) -> Plugin
registry.bank_plugins() -> tuple[Plugin, ...]
registry.insights(snapshot: BudgetSnapshot) -> tuple[PluginInsightResult, ...]
registry.export(plugin_id: str, snapshot: BudgetSnapshot) -> ExportTable
```

An optional entry-point sequence lets tests supply fake installed packages. `PluginDescription` includes `id`, `display_name`, `api_version`, `has_ui`, `has_storage`, `has_bank`, `has_insights`, `has_export` and computed `kinds`. Storage/UI selectors require a unique enabled descriptor with the requested capability; the host skips UI selection when headless. `bank_plugins()` returns descriptors without creating services. Host composition supplies contexts and owns lifecycle. `PluginInsightResult` contains `plugin_id`, `display_name`, `insights` and an optional sanitized `error`. Failures use authored `PluginError` messages with `code` and optional `plugin_id`; never append underlying exceptions or private records.

API v1's documented records, hook signatures, and runtime methods are part of WHAB's SemVer contract. Compatible additions may ship in minor releases; changing/removing their meaning or shape requires a major WHAB release and a new plugin API version. WHAB rejects incompatible descriptors before executing hooks.

## Extension scope

This release supports selected server-rendered interfaces, required storage backends, bank-service factories with scoped normalized imports, and reports. UI/storage selection does not create native runtimes or cross-backend migration. Report/bank plugins have no arbitrary ledger-write or route-registration hook; family authentication is not implemented. Plaid is the bundled bank provider, not proof of institution access. Every backend/provider must preserve monetary rules, isolation, encrypted credentials, annotations and atomic checkpoints.
