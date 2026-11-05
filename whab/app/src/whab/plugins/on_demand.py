"""Release stateless discovery children and acquire exact code for each hook.

Bound state, models, cache owners and interface sessions retain their existing
lifecycle. Pure visual rendering, file parsing, reporting and public artwork
receive only data and need no resident child between operations.
"""

import inspect
from dataclasses import replace
from threading import RLock

from .contracts import contract_for
from .runtime import PluginError


class OnDemandProcess:
    def __init__(self, process):
        self.plugin_id, self.info, self.code_identity = process.plugin_id, process.info, process.code_identity
        self.scope, self.dependencies = process.scope, process.dependencies
        self.runtime, self.children, self.lock = None, set(), RLock()
        self.closed = False

    @property
    def _process(self):
        with self.lock:
            return next((child._process for child in self.children if child._process is not None), None)

    @property
    def healthy(self):
        return not self.closed and not self.cleanup_pending

    @property
    def cleanup_pending(self):
        return any(child.cleanup_pending for child in self.children)

    @property
    def resource(self):
        from starlette.concurrency import run_in_threadpool

        from whab.budgets.runtime import RuntimeResource

        async def close():
            await run_in_threadpool(self.close)
            if self.cleanup_pending:
                raise PluginError("On-demand plugin cleanup remains incomplete.", code="process_cleanup_pending", plugin_id=self.plugin_id)
        if not hasattr(self, "_resource"):
            self._resource = RuntimeResource(self.plugin_id, close, plugin_process=True)
        return self._resource

    def close(self):
        with self.lock:
            self.closed = True
            children = tuple(self.children)
        for child in children:
            self.release(child)

    def release(self, child):
        try:
            child.close()
        finally:
            if not child.cleanup_pending:
                with self.lock:
                    self.children.discard(child)

    def hook(self, *path):
        def invoke(*args, **kwargs):
            from .process_worker import PluginProcess

            with self.lock:
                if self.closed or self.cleanup_pending:
                    raise PluginError("This plugin session is closed.", code="process_unavailable", plugin_id=self.plugin_id)
                child = PluginProcess(self.plugin_id)
                self.children.add(child)
                child.scope, child.runtime = self.scope, self.runtime
            try:
                if child.info != self.info or child.code_identity != self.code_identity:
                    raise PluginError("The plugin code changed. Prepare an approved replacement before continuing.",
                                      code="process_code_changed", plugin_id=self.plugin_id)
                value = child.descriptor
                for name in path:
                    value = getattr(value, name)
                result = value(*args, **kwargs)
                if inspect.isawaitable(result):
                    async def finish():
                        try:
                            return await result
                        finally:
                            self.release(child)
                    return finish()
            except BaseException:
                self.release(child)
                raise
            self.release(child)
            return result
        return invoke


def release_stateless(process, plugin):
    """Only explicit pure contracts qualify; stateful/legacy hooks stay owned."""
    contract = contract_for(plugin)
    if (plugin.contract is None or contract.lifecycle != "live" or contract.state_version != 1
            or contract.state_upgrades or any(getattr(plugin, name) is not None for name in (
                "bind", "migrate_state", "budget_connection", "storage", "categorization",
                "branding_factory", "bank_logos", "settings"))
            or plugin.interface is not None and plugin.interface.factory is not None):
        return plugin
    owner = OnDemandProcess(process)
    updates = {"_process": owner}
    for name in ("insights", "export"):
        if getattr(plugin, name) is not None:
            updates[name] = owner.hook(name)
    for name, method in (("ui", "render"), ("file_import", "parse"), ("merchant_logos", "fetch")):
        value = getattr(plugin, name)
        if value is not None:
            updates[name] = replace(value, **{method: owner.hook(name, method)})
    result = replace(plugin, **updates)
    process.close()
    if process.cleanup_pending:
        owner.children.add(process)
    return result
