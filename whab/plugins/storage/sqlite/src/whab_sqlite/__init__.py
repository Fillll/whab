"""Bundled SQLite storage plugin with a lazy backend export."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Store

__all__ = ["Store"]


def __getattr__(name: str):
    if name == "Store":
        from .store import Store

        return Store
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
