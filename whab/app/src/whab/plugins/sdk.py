"""Public plugin API v1: immutable, scoped data and structured text outputs."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, TypeAlias
from urllib.parse import urlsplit

from whab.domain.accounts import is_on_budget
from whab.domain.categories import category_group_id, validate_child_category
from whab.domain.file_imports import WorkspaceImport
from whab.domain.imports import ImportedAccount, SyncChanges
from whab.domain.money import currency_code
from whab.domain.transactions import UNSET

API_VERSION = 1
ENTRYPOINT_GROUP = "whab.plugins"
_PLUGIN_NAME = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*\Z")


def _text(value: object, label: str, *, limit: int = 2000, empty: bool = True) -> None:
    # Exact primitives reject HTML-aware subclasses and mutable/custom payload objects.
    if type(value) is not str or len(value) > limit or (not empty and not value):
        raise ValueError(f"{label} must be plain text within the documented length limit.")


def _integer(value: object, label: str) -> None:
    if type(value) is not int:
        raise ValueError(f"{label} must be integer currency minor units.")


def _boolean(value: object, label: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean.")


def _identifier(value: object, label: str) -> None:
    _text(value, label, limit=200, empty=False)


def valid_plugin_name(value: object) -> bool:
    return type(value) is str and len(value) <= 64 and _PLUGIN_NAME.fullmatch(value) is not None


def valid_merchant_logo_key(value: object) -> bool:
    return (
        type(value) is str
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}", value) is not None
    )


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    id: str
    name: str
    type: str
    subtype: str
    balance_cents: int
    balance_unknown: bool = False
    stale: bool = False
    on_budget: bool | None = None

    def __post_init__(self) -> None:
        _identifier(self.id, "Account ID")
        _text(self.name, "Account name")
        _text(self.type, "Account type", limit=100)
        _text(self.subtype, "Account subtype", limit=100)
        _integer(self.balance_cents, "Account balance")
        _boolean(self.balance_unknown, "Unknown balance")
        _boolean(self.stale, "Stale balance")
        if self.on_budget is None:
            object.__setattr__(self, "on_budget", is_on_budget({"type": self.type}))
        _boolean(self.on_budget, "Account budget scope")


@dataclass(frozen=True, slots=True)
class CategorySnapshot:
    id: str
    name: str
    group: str
    assigned_cents: int
    activity_cents: int
    available_cents: int

    @property
    def parent_id(self) -> str:
        return category_group_id(self.group)

    def __post_init__(self) -> None:
        _identifier(self.id, "Category ID")
        _text(self.name, "Category name")
        _text(self.group, "Category group")
        _integer(self.assigned_cents, "Category assignment")
        _integer(self.activity_cents, "Category activity")
        _integer(self.available_cents, "Category availability")
        validate_child_category({"id": self.id, "name": self.name, "group": self.group, "target": 0})


@dataclass(frozen=True, slots=True)
class TransactionSnapshot:
    id: str
    date: str
    payee: str
    amount_cents: int
    account_id: str
    category_id: str | None
    pending: bool = False
    is_transfer: bool = False
    reviewed: bool = False

    def __post_init__(self) -> None:
        _identifier(self.id, "Transaction ID")
        _identifier(self.account_id, "Transaction account ID")
        if self.category_id is not None:
            _identifier(self.category_id, "Transaction category ID")
        _text(self.date, "Transaction date", limit=10, empty=False)
        try:
            parsed = date.fromisoformat(self.date)
        except ValueError:
            raise ValueError("Transaction date must be a valid YYYY-MM-DD date.") from None
        if parsed.isoformat() != self.date:
            raise ValueError("Transaction date must use YYYY-MM-DD.")
        _text(self.payee, "Transaction payee")
        _integer(self.amount_cents, "Transaction amount")
        _boolean(self.pending, "Pending transaction")
        _boolean(self.is_transfer, "Transfer transaction")
        _boolean(self.reviewed, "Reviewed transaction")


def _records(value: object, record_type: type, label: str) -> None:
    if type(value) is not tuple or any(type(row) is not record_type for row in value):
        raise ValueError(f"{label} must be a tuple of the documented snapshot records.")
    ids = [row.id for row in value]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{label} must have unique IDs.")


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    workspace: str
    month: str
    accounts: tuple[AccountSnapshot, ...]
    categories: tuple[CategorySnapshot, ...]
    transactions: tuple[TransactionSnapshot, ...]
    currency: str = "USD"

    def __post_init__(self) -> None:
        if type(self.workspace) is not str or self.workspace not in {"demo", "personal"}:
            raise ValueError("Snapshot workspace must be demo or personal.")
        _text(self.month, "Snapshot month", limit=7, empty=False)
        try:
            parsed = date.fromisoformat(self.month + "-01")
        except ValueError:
            raise ValueError("Snapshot month must be a valid YYYY-MM month.") from None
        if parsed.isoformat()[:7] != self.month:
            raise ValueError("Snapshot month must use YYYY-MM.")
        currency_code(self.currency)
        _records(self.accounts, AccountSnapshot, "Accounts")
        _records(self.categories, CategorySnapshot, "Categories")
        _records(self.transactions, TransactionSnapshot, "Transactions")
        account_ids = {row.id for row in self.accounts}
        category_ids = {row.id for row in self.categories}
        for transaction in self.transactions:
            if transaction.date[:7] != self.month:
                raise ValueError("Snapshot transactions must belong to its selected month.")
            if transaction.account_id not in account_ids:
                raise ValueError("Snapshot transaction account must belong to its workspace.")
            if transaction.category_id is not None and transaction.category_id not in category_ids:
                raise ValueError("Snapshot transaction category must belong to its workspace.")


@dataclass(frozen=True, slots=True)
class Insight:
    title: str
    value: str
    detail: str = ""

    def __post_init__(self) -> None:
        _text(self.title, "Insight title", limit=120, empty=False)
        _text(self.value, "Insight value", limit=160)
        _text(self.detail, "Insight detail", limit=2000)


ExportCell: TypeAlias = str | int | bool | None


@dataclass(frozen=True, slots=True)
class ExportTable:
    columns: tuple[str, ...]
    rows: tuple[tuple[ExportCell, ...], ...]

    def __post_init__(self) -> None:
        if type(self.columns) is not tuple or not 1 <= len(self.columns) <= 50:
            raise ValueError("Export columns must be a tuple of 1 to 50 text headings.")
        for heading in self.columns:
            _text(heading, "Export heading", limit=200, empty=False)
        if type(self.rows) is not tuple or len(self.rows) > 100000:
            raise ValueError("Export rows must be a tuple of at most 100000 rows.")
        for row in self.rows:
            if type(row) is not tuple or len(row) != len(self.columns):
                raise ValueError("Each export row must be a tuple matching the column count.")
            for cell in row:
                if type(cell) is str:
                    _text(cell, "Export cell", limit=10000)
                elif cell is not None and type(cell) not in {int, bool}:
                    raise ValueError(
                        "Export cells must be plain text, integers, booleans, or None."
                    )


InsightsHook: TypeAlias = Callable[[BudgetSnapshot], tuple[Insight, ...]]
ExportHook: TypeAlias = Callable[[BudgetSnapshot], ExportTable]


class BulkReviewStorage(Protocol):
    """Optional atomic review flags for one to 100 distinct transaction IDs.

    Use the core review rules for every target. Commit all flags or leave every
    target unchanged on failure; preserve all other transaction and ledger data.
    """

    def review_transactions(self, workspace: str, transaction_ids: tuple[str, ...], reviewed: bool) -> None: ...


class ReceiptStorage(Protocol):
    """Optional private raster attachments, isolated and portable with their ledger."""

    def receipt_ids(self, workspace: str) -> list[str]: ...

    def get_receipt(self, workspace: str, transaction_id: str) -> bytes | None: ...

    def save_receipt(self, workspace: str, transaction_id: str, raw: bytes) -> None: ...

    def delete_receipt(self, workspace: str, transaction_id: str) -> None: ...


class ReceiptSelectionIndex(Protocol):
    """Optional receipt markers for one to 100 distinct selected transaction IDs.

    Return sorted distinct IDs only from the requested active transactions in
    the workspace. Pending rows remain eligible. Read no image payloads and
    preserve the full receipt index's workspace and missing-metadata semantics.
    """

    def receipt_ids_for(self, workspace: str, transaction_ids: tuple[str, ...]) -> list[str]: ...


class ReceiptTransactionLookup(Protocol):
    """Optional scoped existence check before private receipt operations.

    Validate the workspace and return a strict bool for an active transaction
    in that workspace. Deleted rows are absent; pending rows remain eligible.
    Receipt reads and writes must still enforce their own active-row checks.
    """

    def receipt_transaction_exists(self, workspace: str, transaction_id: str) -> bool: ...


class StorageBackend(Protocol):
    """Synchronous application ledger contract, independent of any database driver.

    Implementations preserve currency minor units, isolation, annotations, and atomic imports.
    Internal state, database connections, schema operations, and migrations are not
    host-facing methods. ``items`` is sanitized; ``get_item`` is server-private.
    """

    def close(self) -> None: ...

    def accounts(self, workspace: str) -> list[dict]: ...

    def categories(self, workspace: str) -> list[dict]: ...

    def transactions(self, workspace: str, month: str | None = None) -> list[dict]: ...

    def workspace(self, workspace: str, month: str | None = None) -> dict: ...

    def add_account(self, workspace: str, account: dict) -> str: ...

    def add_category(self, workspace: str, category: dict) -> str: ...

    def add_transaction(self, workspace: str, transaction: dict, source: str = "manual") -> str: ...

    def edit_transaction(
        self,
        workspace: str,
        transaction_id: str,
        category_id: object = UNSET,
        reviewed: bool | None = None,
        note: object = UNSET,
    ) -> None: ...

    def assign(self, workspace: str, category_id: str, month: str, assigned: int) -> None: ...

    def items(self) -> list[dict]: ...

    def get_item(self, item_id: str) -> dict | None: ...

    def save_item(
        self,
        item_id: str,
        encrypted_token: str,
        institution_name: str,
        *,
        provider_id: str = "plaid",
    ) -> None: ...

    def set_item_status(self, item_id: str, status: str, error: str | None) -> None: ...

    def disconnect_item(self, item_id: str) -> None: ...

    def apply_sync(
        self,
        item_id: str,
        changes: SyncChanges,
        cursor: str | None,
        accounts: list[ImportedAccount] | None = None,
        historical_complete: bool = True,
        *,
        expected_cursor: object = UNSET,
    ) -> dict: ...


@dataclass(frozen=True, slots=True)
class StorageContext:
    """Factory-only storage setup. Host supplies no bank credentials or token key."""

    home: Path
    environment: str
    config: Mapping[str, str] = field(repr=False)
    budget: BudgetContext | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.home, Path):
            raise ValueError("Storage home must be a pathlib.Path directory.")
        if type(self.environment) is not str or self.environment not in {"sandbox", "production"}:
            raise ValueError("Storage environment must be sandbox or production.")
        if not isinstance(self.config, Mapping) or any(
            type(key) is not str or type(value) is not str for key, value in self.config.items()
        ):
            raise ValueError("Storage configuration must be a mapping of plain strings.")
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))


StorageFactory: TypeAlias = Callable[[StorageContext], StorageBackend]


@dataclass(frozen=True, slots=True)
class BudgetContext:
    """One plugin's portable settings, state and data files, scoped to its budget."""

    budget_id: str
    settings: Mapping[str, object]
    state: Mapping[str, object]
    save_state: Callable[[dict], None] = field(repr=False)
    read_file: Callable[[str], bytes | None] | None = field(default=None, repr=False)
    write_file: Callable[[str, bytes], None] | None = field(default=None, repr=False)
    delete_file: Callable[[str], None] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class InterfaceContext:
    """A scoped command service and plugin configuration; never a banking store."""

    service: object
    config: Mapping[str, str]
    environment: str
    budget_id: str
    budget: BudgetContext | None = None


@dataclass(frozen=True, slots=True)
class Interface:
    """Trusted transport factory. The host owns shared access guards and lifecycle."""

    factory: Callable[[InterfaceContext], object]

    def __post_init__(self):
        if not callable(self.factory):
            raise ValueError("Interface factory must be callable.")


class UIContextBuilder(Protocol):
    """Presentation data for the host's server-rendered browser pages."""

    def __call__(
        self,
        data: dict,
        status: dict,
        items: list[dict],
        *,
        workspace: str,
        page: str,
        month: str,
        selected: str | None = None,
    ) -> dict: ...


@dataclass(frozen=True, slots=True)
class UserInterface:
    """Trusted visual resources and view-model builder; never an HTTP router factory.

    A budget_months value above one requests a data["periods"] list of consecutive
    core projections on budget pages. The default retains the single-month shape.
    lightweight_transactions opts into register metadata/rows without envelope
    summaries on transaction pages; the default retains full workspace inputs.
    lightweight_accounts requests account metadata/totals without envelopes.
    A positive transaction_page_size requests a bounded register projection;
    zero keeps the complete register input for existing visual plugins.
    """

    templates: Path
    static: Path
    build_context: UIContextBuilder
    budget_months: int = 1
    lightweight_transactions: bool = False
    lightweight_accounts: bool = False
    transaction_page_size: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.templates, Path) or not isinstance(self.static, Path):
            raise ValueError("User interface resources must be pathlib.Path directories.")
        if not callable(self.build_context):
            raise ValueError("User interface context builder must be callable.")
        if type(self.budget_months) is not int or not 1 <= self.budget_months <= 12:
            raise ValueError("User interface budget months must be an integer between 1 and 12.")
        if type(self.lightweight_transactions) is not bool:
            raise ValueError("User interface lightweight transactions must be a boolean.")
        if type(self.lightweight_accounts) is not bool:
            raise ValueError("User interface lightweight accounts must be a boolean.")
        if type(self.transaction_page_size) is not int or not 0 <= self.transaction_page_size <= 1000:
            raise ValueError("User interface transaction page size must be an integer between 0 and 1000.")


class BankStore(Protocol):
    """Provider-scoped ledger persistence; supplied by the host to a trusted bank factory."""

    def items(self) -> list[dict]: ...

    def get_item(self, item_id: str) -> dict | None: ...

    def save_item(self, item_id: str, encrypted_token: str, institution_name: str) -> None: ...

    def set_item_status(self, item_id: str, status: str, error: str | None) -> None: ...

    def disconnect_item(self, item_id: str) -> None: ...

    def item_identity(self, item_id: str) -> str: ...

    def apply_sync(
        self,
        item_id: str,
        changes: SyncChanges,
        cursor: str | None,
        accounts: list[ImportedAccount] | None = None,
        historical_complete: bool = True,
        *,
        expected_cursor: object = UNSET,
    ) -> dict: ...


class BankError(ValueError):
    """An authored bank error safe to display; never include credentials or provider payloads."""


@dataclass(frozen=True, slots=True)
class BankContext:
    """Privileged factory-only capabilities. Never pass this context to report hooks."""

    store: BankStore = field(repr=False)
    config: Mapping[str, str] = field(repr=False)
    environment: str
    encryption_key: str = field(repr=False)
    budget: BudgetContext | None = None

    def __post_init__(self) -> None:
        if type(self.environment) is not str or self.environment not in {"sandbox", "production"}:
            raise ValueError("Bank plugin environment must be sandbox or production.")
        if not isinstance(self.config, Mapping) or any(
            type(key) is not str or type(value) is not str for key, value in self.config.items()
        ):
            raise ValueError("Bank plugin configuration must be a mapping of plain strings.")
        if type(self.encryption_key) is not str:
            raise ValueError("Bank plugin encryption key must be a plain string.")
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))


@dataclass(frozen=True, slots=True)
class WebhookResult:
    ok: bool
    status_code: int = 200
    error: str | None = None

    def __post_init__(self) -> None:
        _boolean(self.ok, "Webhook success")
        if type(self.status_code) is not int or not 200 <= self.status_code <= 599:
            raise ValueError("Webhook status must be an HTTP response code from 200 to 599.")
        if self.ok and not 200 <= self.status_code <= 299:
            raise ValueError("A successful webhook must use a 2xx response code.")
        if not self.ok and not 400 <= self.status_code <= 599:
            raise ValueError("A failed webhook must use a 4xx or 5xx response code.")
        if self.error is not None:
            _text(self.error, "Webhook error", limit=200)
        if self.ok and self.error is not None:
            raise ValueError("A successful webhook must not contain an error.")


class BankService(Protocol):
    """Connection/sync lifecycle implemented by explicitly enabled privileged providers."""

    config: Mapping[str, object]

    async def link_token(self, item_id: str | None = None) -> str: ...

    async def exchange(self, public_token: str, institution_name: str | None = None) -> None: ...

    async def sync(self, item_id: str | None = None) -> dict[str, object]: ...

    async def disconnect(self, item_id: str) -> None: ...

    async def webhook(self, raw: bytes, headers: Mapping[str, str]) -> WebhookResult: ...

    async def close(self) -> None: ...


BankFactory: TypeAlias = Callable[[BankContext], BankService]


@dataclass(frozen=True, slots=True)
class BankUI:
    """Trusted provider UI resources. The host owns mounting and validates directories."""

    templates: Path
    static: Path

    def __post_init__(self) -> None:
        if not isinstance(self.templates, Path) or not isinstance(self.static, Path):
            raise ValueError("Bank UI resources must be pathlib.Path directories.")


@dataclass(frozen=True, slots=True)
class LogoImage:
    """Small raster image, served by the host without exposing external URLs."""

    data: bytes = field(repr=False)
    media_type: str

    def __post_init__(self) -> None:
        if type(self.data) is not bytes or not 0 < len(self.data) <= 128 * 1024:
            raise ValueError("Logo images must contain at most 128 KiB of bytes.")
        if type(self.media_type) is not str:
            raise ValueError("Logo images must use a supported raster media type.")
        signatures = {
            "image/png": self.data.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": self.data.startswith(b"\xff\xd8\xff"),
            "image/gif": self.data.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": self.data.startswith(b"RIFF") and self.data[8:12] == b"WEBP",
        }
        if not signatures.get(self.media_type, False):
            raise ValueError("Logo images must match a supported raster media type.")


@dataclass(frozen=True, slots=True)
class BankLogos:
    """Resolve institution text locally; fetch public icons only on image requests.

    Keys are lowercase slugs of at most 64 characters. Hooks receive no ledger,
    configuration or bank-service context. Unknown/unavailable logos return None.
    """

    resolve: Callable[[str], str | None]
    fetch: Callable[[str], LogoImage | None]
    configure_cache: Callable[[Path], None] | None = None
    resolve_domain: Callable[[str], str | None] | None = None
    provider_name: str = ""
    provider_url: str = ""

    def __post_init__(self) -> None:
        if not callable(self.resolve) or not callable(self.fetch):
            raise ValueError("Bank logo resolution and fetching must be callable.")
        if self.configure_cache is not None and not callable(self.configure_cache):
            raise ValueError("Bank logo cache configuration must be callable or None.")
        if self.resolve_domain is not None and not callable(self.resolve_domain):
            raise ValueError("Bank logo domain resolution must be callable or None.")
        _text(self.provider_name, "Bank logo provider name", limit=200)
        _text(self.provider_url, "Bank logo provider URL", limit=2048)
        if self.provider_url:
            try:
                url = urlsplit(self.provider_url)
                if url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None:
                    raise ValueError()
            except ValueError:
                raise ValueError("Bank logo providers must use a public HTTPS URL.") from None


@dataclass(frozen=True, slots=True)
class MerchantLogos:
    """Fetch a bank provider's public artwork by an imported opaque key.

    Keys contain 1–200 ASCII letters, digits, underscores or hyphens and begin
    with a letter or digit. Hooks receive no transaction or banking context.
    The provider must allowlist destinations; missing artwork returns None.
    """

    fetch: Callable[[str], LogoImage | None]

    def __post_init__(self) -> None:
        if not callable(self.fetch):
            raise ValueError("Merchant logo fetching must be callable.")


@dataclass(frozen=True, slots=True)
class PluginAction:
    """An explicit local settings operation, executed only after a user request."""

    id: str
    label: str
    description: str = ""

    def __post_init__(self) -> None:
        if not valid_plugin_name(self.id):
            raise ValueError("Plugin action ID must be a lowercase slug of at most 64 characters.")
        _text(self.label, "Plugin action label", limit=120, empty=False)
        _text(self.description, "Plugin action description", limit=2000)


@dataclass(frozen=True, slots=True)
class PluginSettings:
    """Plain local settings information and actions, without host or ledger context."""

    read: Callable[[], tuple[Insight, ...]]
    actions: tuple[PluginAction, ...] = ()
    act: Callable[[str], str] | None = None

    def __post_init__(self) -> None:
        if not callable(self.read):
            raise ValueError("Plugin settings reader must be callable.")
        if type(self.actions) is not tuple or len(self.actions) > 50:
            raise ValueError("Plugin settings actions must be a tuple of at most 50 actions.")
        for action in self.actions:
            if type(action) is not PluginAction:
                raise ValueError("Plugin settings actions must use PluginAction records.")
            PluginAction(action.id, action.label, action.description)
        if len({action.id for action in self.actions}) != len(self.actions):
            raise ValueError("Plugin settings actions must have unique IDs.")
        if self.act is not None and not callable(self.act):
            raise ValueError("Plugin settings action handler must be callable or None.")
        if self.actions and self.act is None:
            raise ValueError("Plugin settings actions require an action handler.")


@dataclass(frozen=True, slots=True)
class FileImport:
    """Parse a bounded local file without ledger access; the host owns persistence."""

    parse: Callable[[bytes], WorkspaceImport]
    templates: Path | None = None
    static: Path | None = None

    def __post_init__(self) -> None:
        if not callable(self.parse):
            raise ValueError("File import parser must be callable.")
        if (self.templates is None) != (self.static is None):
            raise ValueError("File import UI requires both template and static directories.")
        if self.templates is not None and (
            not isinstance(self.templates, Path) or not isinstance(self.static, Path)
        ):
            raise ValueError("File import resources must be pathlib.Path directories.")


class NextMonthFunding(Protocol):
    """Optional saved funding capability; month identifies the source month.

    Backends supporting this command advertise nextMonthFundingEditable in
    their workspace projections. Core 2.9 also advertises monthlyFundingFlow
    for independent source-month set-asides and assignable carry-in.
    Existing storage backends remain valid.
    """

    def assign_next_month(
        self, workspace: str, month: str, currency: str, assigned: int,
    ) -> None: ...


class TransactionRegister(Protocol):
    """Optional scoped public-row read, without calculating envelope projections.

    Month None reads all dates; account_id None reads every account of currency.
    Return accounts/categories metadata plus scoped transactions in one snapshot.
    Preserve transactions() public fields and deterministic date/id order. Unknown
    or other-currency account IDs mean all accounts of the addressed currency.
    Do not expose deleted rows or provider state. Existing storage remains valid.
    """

    def transaction_register(
        self, workspace: str, month: str | None, currency: str, account_id: str | None,
    ) -> dict: ...


class TransactionRegisterFiltered(Protocol):
    """Optional indexed register read for inclusive dates and multiple accounts.

    None bounds are open; account_ids None means all accounts of currency, while
    an empty tuple selects no transactions. Preserve public transaction fields,
    deterministic date/id ordering and a coherent account/category/row snapshot.
    Existing TransactionRegister and StorageBackend implementations remain valid.
    """

    def transaction_register_filtered(
        self, workspace: str, start_date: str | None, end_date: str | None,
        currency: str, account_ids: tuple[str, ...] | None,
    ) -> dict: ...


class TransactionRegisterPage(Protocol):
    """Optional atomic bounded register query; existing register reads stay valid.

    Scope dates, currency and accounts before counting or paging. Search is a
    literal substring of casefolded merchant + space + note, with surrounding
    query whitespace removed. Review is all, needs-review or reviewed. Preserve
    date/id descending order and public row fields. Return accounts/categories,
    transactions and transactionPage metadata (total/count/page/pages/start/end/
    toReview). total and toReview describe the account/date selection before
    search/review; toReview excludes pending rows. Clamp positive pages to the
    available range. Empty account_ids selects nothing; None selects currency.
    """

    def transaction_register_page(
        self, workspace: str, start_date: str | None, end_date: str | None,
        currency: str, account_ids: tuple[str, ...] | None, search: str,
        review: str, page: int, page_size: int,
    ) -> dict: ...


class AccountOverview(Protocol):
    """Optional account/category metadata and selected month/currency review count.

    Return accounts, categories and toReview atomically without envelope history.
    toReview counts nondeleted, nonpending, unreviewed rows. The service owns
    currency filtering and core account totals. Existing storage remains valid.
    """

    def account_overview(self, workspace: str, month: str, currency: str) -> dict: ...


class AccountSettings(Protocol):
    """Optional atomic public account plus currencyEditable history proof.

    Every transaction, including deleted and historical rows, locks currency.
    Never include provider state or credentials. Legacy export fallback remains.
    """

    def account_settings(self, workspace: str, account_id: str) -> dict: ...


class WorkspacePeriods(Protocol):
    """Optional batch of ordinary workspace projections from one ledger snapshot."""

    def workspace_periods(self, workspace: str, month: str, count: int) -> list[dict]: ...


class MonthlyRollover(Protocol):
    """Optional per-currency closing rule, saved on the outgoing source month.

    Supporting projections advertise rolloverModeEditable and rolloverMode.
    Existing storage backends remain valid without this capability.
    """

    def set_rollover_mode(
        self, workspace: str, month: str, currency: str, mode: str,
    ) -> None: ...


class WorkspaceImporter(Protocol):
    """Optional storage capability; existing StorageBackend implementations stay valid."""

    def preview_workspace_import(
        self, workspace: str, plugin_id: str, fingerprint: str,
    ) -> dict: ...

    def import_workspace(
        self, workspace: str, plugin_id: str, fingerprint: str, data: WorkspaceImport,
    ) -> dict: ...


@dataclass(frozen=True, slots=True)
class Plugin:
    id: str
    display_name: str
    api_version: int = API_VERSION
    insights: InsightsHook | None = None
    export: ExportHook | None = None
    bank: BankFactory | None = None
    bank_ui: BankUI | None = None
    storage: StorageFactory | None = None
    ui: UserInterface | None = None
    bank_logos: BankLogos | None = None
    file_import: FileImport | None = None
    settings: PluginSettings | None = None
    merchant_logos: MerchantLogos | None = None
    interface: Interface | None = None
    bind: Callable[[BudgetContext], Plugin] | None = None
    budget_connection: Callable[[Mapping[str, bytes]], Mapping[str, str]] | None = None

    def __post_init__(self) -> None:
        if not valid_plugin_name(self.id):
            raise ValueError("Plugin ID must be a lowercase slug of at most 64 characters.")
        _text(self.display_name, "Plugin display name", limit=120, empty=False)
        if self.interface is not None and type(self.interface) is not Interface:
            raise ValueError("Plugin interface must be an Interface descriptor or None.")
        if self.bind is not None and not callable(self.bind):
            raise ValueError("Plugin budget binder must be callable or None.")
        if self.budget_connection is not None and not callable(self.budget_connection):
            raise ValueError("Plugin budget connection reader must be callable or None.")
        if type(self.api_version) is not int:
            raise ValueError("Plugin API version must be an integer.")
        if self.insights is not None and not callable(self.insights):
            raise ValueError("Plugin insights must be callable or None.")
        if self.export is not None and not callable(self.export):
            raise ValueError("Plugin export must be callable or None.")
        if self.bank is not None and not callable(self.bank):
            raise ValueError("Plugin bank factory must be callable or None.")
        if self.bank_ui is not None and type(self.bank_ui) is not BankUI:
            raise ValueError("Plugin bank UI must be a BankUI descriptor or None.")
        if self.bank_ui is not None and self.bank is None:
            raise ValueError("Plugin bank UI requires a bank factory.")
        if self.storage is not None and not callable(self.storage):
            raise ValueError("Plugin storage factory must be callable or None.")
        if self.ui is not None and type(self.ui) is not UserInterface:
            raise ValueError("Plugin user interface must be a UserInterface descriptor or None.")
        if self.bank_logos is not None:
            if type(self.bank_logos) is not BankLogos:
                raise ValueError("Plugin bank logos must be a BankLogos descriptor or None.")
            BankLogos(
                self.bank_logos.resolve, self.bank_logos.fetch, self.bank_logos.configure_cache,
                self.bank_logos.resolve_domain, self.bank_logos.provider_name, self.bank_logos.provider_url,
            )
        if self.file_import is not None:
            if type(self.file_import) is not FileImport:
                raise ValueError("Plugin file import must be a FileImport descriptor or None.")
            FileImport(self.file_import.parse, self.file_import.templates, self.file_import.static)
        if self.settings is not None:
            if type(self.settings) is not PluginSettings:
                raise ValueError("Plugin settings must be a PluginSettings descriptor or None.")
            PluginSettings(self.settings.read, self.settings.actions, self.settings.act)
        if self.merchant_logos is not None:
            if type(self.merchant_logos) is not MerchantLogos:
                raise ValueError("Plugin merchant logos must be a MerchantLogos descriptor or None.")
            MerchantLogos(self.merchant_logos.fetch)
            if self.bank is None:
                raise ValueError("Plugin merchant logos require a bank factory.")
        if (
            self.insights is None
            and self.export is None
            and self.bank is None
            and self.storage is None
            and self.ui is None
            and self.bank_logos is None
            and self.file_import is None
            and self.interface is None
            and self.bind is None
        ):
            raise ValueError(
                "Plugin must provide a reporting, bank, file import, storage, visual interface, "
                "transport interface, branding capability, or budget binder."
            )


@dataclass(frozen=True, slots=True)
class PluginDescription:
    id: str
    display_name: str
    api_version: int
    has_insights: bool
    has_export: bool
    has_bank: bool
    has_storage: bool
    has_ui: bool
    kinds: tuple[str, ...]
    has_bank_logos: bool = False
    has_file_import: bool = False
    has_settings: bool = False
    has_merchant_logos: bool = False


@dataclass(frozen=True, slots=True)
class PluginInsightResult:
    plugin_id: str
    display_name: str
    insights: tuple[Insight, ...]
    error: str | None = None
