"""Backend factories, isolated homes and detached public observations."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from json import dumps, loads
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

from whab.plugins import StorageBackend, StorageContext


@dataclass(frozen=True)
class BackendAdapter:
    """Open a fresh backend or reopen durable data through public factories.

    ``capabilities`` names suites to require, beyond the mandatory ledger suite.
    The harness never reads an existing runtime or supplies real credentials.
    A separate reopen factory accommodates providers with distinct open APIs.
    """

    factory: Callable[[StorageContext], StorageBackend]
    capabilities: frozenset[str] = frozenset()
    reopen_factory: Callable[[StorageContext], StorageBackend] | None = None
    config: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if not callable(self.factory) or (
            self.reopen_factory is not None and not callable(self.reopen_factory)
        ):
            raise ValueError("Backend adapters require callable factories.")
        capabilities = frozenset(self.capabilities)
        if any(not isinstance(value, str) for value in capabilities):
            raise ValueError("Capabilities must be strings.")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))

    def open(self, home: Path, *, reopen=False, environment="sandbox", initial_role="primary"):
        factory = self.reopen_factory if reopen and self.reopen_factory else self.factory
        # Keep the ordinary path usable on the kit's oldest supported SDK.
        context = StorageContext(home, environment, self.config) if initial_role == "primary" else StorageContext(
            home, environment, self.config, initial_role=initial_role,
        )
        return factory(context)


class BackendHarness:
    """Own all temporary homes and close every successfully created backend.

    Close failures are surfaced after attempting cleanup of every other backend.
    Factory-internal startup cleanup remains the adapter's responsibility.
    """

    def __init__(self, adapter: BackendAdapter):
        self.adapter = adapter
        self._temporary = TemporaryDirectory(prefix="whab-plugin-conformance-")
        self.home = Path(self._temporary.name)
        self._backends = []
        self.cleanup_attempts = 0

    def __enter__(self):
        return self

    def open(self, name="budget-a", *, reopen=False, environment="sandbox", initial_role="primary"):
        if type(name) is not str or not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError("Use one temporary budget name.")
        home = self.home / name
        home.mkdir(mode=0o700, exist_ok=True)
        backend = self.adapter.open(home, reopen=reopen, environment=environment, initial_role=initial_role)
        self._backends.append(backend)
        return backend

    def close(self, backend):
        # Remove before closing: a failed close must never be repeated blindly.
        index = next(index for index, candidate in enumerate(self._backends) if candidate is backend)
        self._backends.pop(index)
        self.cleanup_attempts += 1
        backend.close()

    def __exit__(self, kind, value, traceback):
        failed = False
        for backend in list(reversed(self._backends)):
            try:
                self.close(backend)
            except Exception:
                failed = True
        self._temporary.cleanup()
        if failed:
            raise RuntimeError("A conformance backend failed to close.") from None


def public_snapshot(backend, workspace="personal", *, month=None):
    """Detach and normalize observable records, never a backend's private state.

    List order is ignored for account/category/transaction identity comparisons;
    the backend's register-order behavior needs a separate optional suite.
    This value contains synthetic records and is never included in reports.
    """
    result = {
        name: sorted(getattr(backend, name)(workspace), key=lambda row: row["id"])
        for name in ("accounts", "categories", "transactions")
    }
    if month is not None:
        result["workspace"] = backend.workspace(workspace, month)
    return loads(dumps(result, sort_keys=True, allow_nan=False))
