"""Public data-only provider engines; the host owns keys and native persistence."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol, TypedDict

from .storage_transfer import storage_identifier


class ProviderFailure(ValueError):
    """Explicit transport outcomes with fixed codes, never provider payload text."""

    _MESSAGES = {
        'not_configured': 'The bank provider is not configured.',
        'reconnect': 'The bank connection requires a scoped update through its provider.',
        'transport': 'The bank provider request could not complete.',
    }

    def __init__(self, code):
        if type(code) is not str or code not in self._MESSAGES:
            raise ValueError('Invalid provider failure code.')
        super().__init__(self._MESSAGES[code])
        self.code = code


@dataclass(frozen=True, slots=True)
class BankProviderContext:
    """Reviewed provider credentials/configuration, without native capabilities.

    Runtime encryption keys, file callbacks, backend handles and background
    factories never enter this record. Provider API credentials are deliberately
    available to the selected trusted provider engine and hidden from repr.
    """

    budget_id: str
    environment: str
    config: Mapping[str, str] = field(repr=False)

    def __post_init__(self):
        storage_identifier(self.budget_id)
        if type(self.environment) is not str or self.environment not in {'sandbox', 'production'}:
            raise ValueError('Invalid bank provider environment.')
        if not isinstance(self.config, Mapping) or len(self.config) > 100:
            raise ValueError('Invalid bank provider configuration.')
        size = 0
        for key, value in self.config.items():
            if (type(key) is not str or not 1 <= len(key) <= 200 or type(value) is not str or len(value) > 8192
                    or key.casefold() in {'token_encryption_key', 'whab_encryption_key', 'encryption_key'}
                    or '\x00' in key or '\x00' in value):
                raise ValueError('Invalid bank provider configuration.')
            try:
                size += len(key.encode('utf-8')) + len(value.encode('utf-8'))
            except UnicodeError:
                raise ValueError('Invalid bank provider configuration.') from None
        if size > 128 * 1024:
            raise ValueError('Invalid bank provider configuration.')
        object.__setattr__(self, 'config', MappingProxyType(dict(self.config)))


@dataclass(frozen=True, slots=True)
class ProviderWebhook:
    """A verified provider action; the host still enforces connection ownership."""

    ok: bool
    status_code: int = 200
    item_id: str | None = field(default=None, repr=False)
    sync: bool = False
    reconnect: bool = False

    def __post_init__(self):
        if type(self.ok) is not bool or type(self.sync) is not bool or type(self.reconnect) is not bool:
            raise ValueError('Invalid provider webhook result.')
        if type(self.status_code) is not int or not (200 <= self.status_code <= 299 if self.ok else 400 <= self.status_code <= 599):
            raise ValueError('Invalid provider webhook result.')
        if self.item_id is not None:
            storage_identifier(self.item_id)
        if ((self.sync or self.reconnect) and (not self.ok or self.item_id is None)
                or (not self.ok and self.item_id is not None)):
            raise ValueError('Invalid provider webhook result.')


class BankProvider(Protocol):
    """Pure provider transport/normalization; synchronous factories own engines."""

    config: Mapping[str, object]

    async def connection_token(self, item_id: str | None, access_token: str | None) -> str: ...
    async def exchange_token(self, public_token: str) -> dict: ...
    async def read_sync(self, item_id: str, access_token: str, cursor: str | None, institution_name: str) -> dict: ...
    async def remove_token(self, access_token: str) -> None: ...
    async def webhook_event(self, raw: bytes, headers: Mapping[str, str]) -> ProviderWebhook: ...
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class BankAdapter:
    """Explicit typed provider factory, usable only through the host coordinator."""

    factory: Callable[[BankProviderContext], BankProvider] = field(repr=False)

    def __post_init__(self):
        if not callable(self.factory):
            raise ValueError('Bank provider factory must be callable.')

    def __call__(self, _context):
        raise ValueError('This bank provider requires the host continuation adapter.')


class BankContinuationStatus(TypedDict):
    flow_id: str
    generation: int
    kind: Literal['link', 'update']
    stage: Literal['ready', 'exchanging', 'exchange_uncertain', 'captured', 'applying', 'apply_uncertain', 'complete', 'cancelled', 'expired']
    deadline_ms: int


class BankLinkReady(BankContinuationStatus):
    link_token: str


class BankContinuationService(Protocol):
    """Optional host facade. Legacy BankService remains an explicit restart ABI."""

    async def begin_link(self, item_id: str | None = None) -> BankLinkReady: ...
    async def continuation_status(self, flow_id: str) -> BankContinuationStatus: ...
    async def cancel_link(self, flow_id: str) -> BankContinuationStatus: ...
    async def complete_link(self, flow_id: str, public_token: str | None = None,
                            institution_name: str | None = None) -> BankContinuationStatus: ...
