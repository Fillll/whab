"""Per-budget browser participation in a coherent live UI handover.

Only opaque view/generation identities and dirty/ready flags are retained. Draft
form contents stay in the browser. An offline or old view is never instructed to
reload while dirty; immutable generation assets remain owned by the UI adapter.
"""

import asyncio
import re
from copy import deepcopy
from time import monotonic
from uuid import uuid4

from whab.plugins.runtime import PluginError

_GENERATION = re.compile(r"[0-9a-f]{64}\Z")


def _generation(value):
    if type(value) is not str or _GENERATION.fullmatch(value) is None:
        raise PluginError("Refresh the interface before registering this browser view.", code="ui_view_invalid")
    return value


class BrowserViews:
    """Event-loop-owned coordination; preparation does not gate financial work."""

    def __init__(self, generation, *, clock=monotonic, lifetime=60):
        self.generation = _generation(generation)
        self.clock, self.lifetime = clock, lifetime
        self.views = {}
        self.pending = None
        self._changed = asyncio.Event()

    def register(self, generation, *, dirty=False):
        _generation(generation)
        if type(dirty) is not bool:
            raise PluginError("Use a valid browser edit status.", code="ui_view_invalid")
        self._prune()
        if len(self.views) >= 1024:
            raise PluginError("Too many browser views are registered. Close unused budget views.", code="ui_views_full")
        identifier = str(uuid4())
        self.views[identifier] = {"generation": generation, "dirty": dirty, "seen": self.clock(), "prepared": None}
        self._changed.set()
        return {"view": identifier, **self.status(identifier)}

    def _prune(self):
        now = self.clock()
        self.views = {name: value for name, value in self.views.items() if now - value["seen"] <= self.lifetime}

    def status(self, identifier):
        view = self.views.get(identifier)
        if view is None:
            raise PluginError("Register this browser view again before changing its interface.", code="ui_view_unknown")
        return {"generation": self.generation, "pending": deepcopy(self.pending),
                "reload": view["generation"] != self.generation and not view["dirty"],
                "dirty": view["dirty"]}

    def update(self, identifier, generation, *, dirty, prepared=None):
        _generation(generation)
        view = self.views.get(identifier)
        if view is None:
            raise PluginError("Register this browser view again before changing its interface.", code="ui_view_unknown")
        if type(dirty) is not bool or generation != view["generation"]:
            raise PluginError("Use this browser view's current generation and edit status.", code="ui_view_invalid")
        if prepared is not None and (type(prepared) is not str or not self.pending or prepared != self.pending["id"] or dirty):
            raise PluginError("Save or cancel edits before preparing the current interface change.", code="ui_view_invalid")
        view.update(dirty=dirty, prepared=prepared, seen=self.clock())
        self._changed.set()
        return self.status(identifier)

    def unregister(self, identifier):
        self.views.pop(identifier, None)
        self._changed.set()

    def _ready(self, identifier):
        self._prune()
        return bool(self.pending and self.pending["id"] == identifier and all(
            not view["dirty"] and view["prepared"] == identifier for view in self.views.values()
        ))

    async def prepare(self, generation, *, timeout=10):
        _generation(generation)
        if self.pending is not None:
            raise PluginError("An interface change is already waiting for browser views.", code="transition_busy")
        if type(timeout) not in (int, float) or not 0 <= timeout <= 300:
            raise ValueError("Use a bounded UI preparation deadline.")
        identifier = str(uuid4())
        self.pending = {"id": identifier, "generation": generation}
        try:
            async with asyncio.timeout(timeout):
                while not self._ready(identifier):
                    self._changed.clear()
                    await self._changed.wait()
            return identifier
        except BaseException as error:
            self.abort(identifier)
            if isinstance(error, TimeoutError):
                raise PluginError("Save or cancel edits and keep affected budget views open before changing the interface.", code="ui_views_busy") from None
            raise

    def validate(self, identifier):
        if not self._ready(identifier):
            raise PluginError("A browser view changed during interface preparation. Retry after saving its edits.", code="ui_views_busy")

    def commit(self, identifier):
        self.validate(identifier)
        self.generation = self.pending["generation"]
        self.pending = None
        for view in self.views.values():
            view["prepared"] = None
        self._changed.set()

    def abort(self, identifier):
        if self.pending and self.pending["id"] == identifier:
            self.pending = None
            for view in self.views.values():
                view["prepared"] = None
            self._changed.set()
