"""Host-owned generation leases and controlled, per-budget runtime handovers.

This module does not load plugins or change durable authority. Preparation must
use staged contexts. Commit callbacks own their durable protocol; an uncertain
commit gates admission until an explicit, validated recovery decision. Callback
work is never cancelled when the initiating request disconnects.
"""

import asyncio
import inspect
import math
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from threading import Condition
from time import monotonic
from types import MappingProxyType

from whab.plugins.runtime import PluginError
from whab.plugins.sdk import valid_plugin_name

_MESSAGES = {
    "invalid_runtime": "The prepared budget runtime is invalid.",
    "runtime_paused": "This budget is paused for a runtime transition. Retry after it finishes.",
    "runtime_closed": "This budget runtime has closed. Reopen the budget before continuing.",
    "lease_expired": "This operation's runtime lease has ended. Start a new operation.",
    "admission_timeout": "This budget is still paused for a runtime transition. Retry later.",
    "drain_timeout": "The runtime transition timed out waiting for active work. Existing work continues.",
    "prepare_failed": "The candidate runtime could not be prepared. The active runtime is unchanged.",
    "readiness_failed": "The candidate runtime did not pass readiness checks. The active runtime is unchanged.",
    "continuation_busy": "Finish or cancel this plugin's unfinished operation before replacing or disabling it.",
    "commit_uncertain": "The runtime handover could not be confirmed. Validate authority before resuming this budget.",
    "recovery_required": "Validate this budget's runtime authority before another transition.",
    "recovery_failed": "Runtime authority could not be validated. This budget remains paused.",
    "transition_in_operation": "Finish the current budget operation before changing its runtime.",
    "service_unavailable": "This service is not available in the operation's runtime generation.",
    "async_service": "Use asynchronous service access for this runtime operation.",
    "retirement_failed": "A retired runtime resource could not close. Check its plugin's recovery controls.",
    "retirement_timeout": "A retired runtime resource is still closing. Its cleanup remains tracked.",
}
_SCOPES = ContextVar("whab_budget_runtime_leases", default=())


class RuntimeTransitionError(PluginError):
    """A fixed, safe diagnostic with attribution, never underlying exception text."""

    def __init__(self, code, plugin_id=None):
        super().__init__(_MESSAGES[code], code=code, plugin_id=plugin_id)


def _timeout(value):
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise RuntimeTransitionError("invalid_runtime")
    return value


def _services(values):
    if not isinstance(values, Mapping) or any(
        type(name) is not str or not name.isidentifier() or name.startswith("_")
        for name in values
    ):
        raise RuntimeTransitionError("invalid_runtime")
    return MappingProxyType(dict(values))


@dataclass(frozen=True)
class RuntimeResource:
    """One owned resource. Reuse this record to retain it across generations."""

    plugin_id: str
    close: Callable = field(repr=False)

    def __post_init__(self):
        if not valid_plugin_name(self.plugin_id) or not callable(self.close):
            raise RuntimeTransitionError("invalid_runtime")


@dataclass(frozen=True)
class RuntimeCandidate:
    services: Mapping[str, object]
    resources: tuple[RuntimeResource, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "services", _services(self.services))
        if type(self.resources) is not tuple or any(
            type(resource) is not RuntimeResource for resource in self.resources
        ) or len({id(resource) for resource in self.resources}) != len(self.resources):
            raise RuntimeTransitionError("invalid_runtime")


@dataclass(frozen=True)
class RuntimeGeneration:
    number: int
    services: Mapping[str, object]
    resources: tuple[RuntimeResource, ...]


class RuntimePreparation:
    """Register acquired resources immediately, including before startup can fail."""

    def __init__(self, active):
        self.active = active
        self._resources = []

    def own(self, resource):
        if type(resource) is not RuntimeResource:
            raise RuntimeTransitionError("invalid_runtime")
        if all(resource is not previous for previous in self._resources):
            self._resources.append(resource)
        return resource

    def candidate(self, services):
        return RuntimeCandidate(services, tuple(self._resources))


class RuntimeLease:
    """An operation's immutable generation; raw services stay inside its scope."""

    def __init__(self, runtime, generation):
        self._runtime = runtime
        self.generation = generation
        self._released = False
        self._references = 1

    def service(self, name):
        with self._runtime._condition:
            if self._released:
                raise RuntimeTransitionError("lease_expired")
            try:
                return self.generation.services[name]
            except KeyError:
                raise RuntimeTransitionError("service_unavailable") from None


class ManagedServiceHandle:
    """A stable access point that resolves bound methods inside an operation lease."""

    def __init__(self, runtime, name):
        self._runtime, self._name = runtime, name

    def call(self, method, *args, **kwargs):
        with self._runtime.lease() as lease:
            result = getattr(lease.service(self._name), method)(*args, **kwargs)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise RuntimeTransitionError("async_service")
            return result

    async def acall(self, method, *args, **kwargs):
        async def operation():
            async with self._runtime.request() as lease:
                result = getattr(lease.service(self._name), method)(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result

        # A disconnected caller must not abandon an admitted asynchronous write.
        return await self._runtime._owned(operation())


async def _invoke(callback, *args):
    # Synchronous hooks must be short. Their owners offload blocking disk or
    # process work; keeping this boundary explicit avoids uncancellable threads.
    result = callback(*args)
    return await result if inspect.isawaitable(result) else result


class BudgetRuntime:
    """Stable coordination state independent of a selected plugin generation.

    Request scopes nest coherently, including synchronous calls in a copied
    thread context. Background work must use ``background()`` for its own lease.
    Admission/drain deadlines never cancel an admitted operation. A cancelled
    transition caller leaves the owned transition running and observable.
    """

    def __init__(self, budget_id, services, resources=()):
        # Budget identities are public ledger identifiers, not plugin slugs.
        # In particular, canonical UUIDs may begin with a digit.
        if type(budget_id) is not str or not 1 <= len(budget_id) <= 200 or "\x00" in budget_id:
            raise RuntimeTransitionError("invalid_runtime")
        try:
            budget_id.encode("utf-8")
        except UnicodeError:
            raise RuntimeTransitionError("invalid_runtime") from None
        candidate = RuntimeCandidate(services, resources)
        self.budget_id = budget_id
        self._active = RuntimeGeneration(1, candidate.services, candidate.resources)
        self._condition = Condition()
        self._waiters = set()
        self._leases = {}
        self._gate = False
        self._closed = False
        self._serial = asyncio.Lock()
        self._tasks = set()
        self._cleanup = set()
        self._cleanup_errors = []
        self._pending = None
        self._error = None
        self._phase = "active"
        self._sequence = 0
        self._uncertain = None

    def handle(self, name):
        if type(name) is not str or not name.isidentifier() or name.startswith("_"):
            raise RuntimeTransitionError("invalid_runtime")
        return ManagedServiceHandle(self, name)

    def status(self):
        with self._condition:
            return {
                "budget_id": self.budget_id,
                "generation": self._active.number,
                "phase": self._phase,
                "admission": "closed" if self._closed else "gated" if self._gate else "open",
                "active_leases": sum(self._leases.values()),
                "pending": dict(self._pending) if self._pending else None,
                "error": dict(self._error) if self._error else None,
                "cleanup_errors": [dict(error) for error in self._cleanup_errors],
                "cleanup_pending": len(self._cleanup),
                "health": "recovery_required" if self._uncertain else "degraded" if self._cleanup_errors else "ready",
            }

    @staticmethod
    def _diagnostic(code, plugin_id):
        return {"code": code, "plugin_id": plugin_id, "message": _MESSAGES[code]}

    @staticmethod
    def _wake(future):
        if not future.done():
            future.set_result(None)

    def _notify(self):
        self._condition.notify_all()
        for loop, future in self._waiters:
            loop.call_soon_threadsafe(self._wake, future)

    def _progress(self, phase, progress):
        with self._condition:
            self._phase = phase
            if self._pending:
                self._pending.update(phase=phase, progress=progress)
            self._notify()

    async def _wait(self, predicate, timeout, code):
        deadline = asyncio.get_running_loop().time() + _timeout(timeout)
        while True:
            with self._condition:
                if predicate():
                    return
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise RuntimeTransitionError(code)
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                registration = (loop, future)
                self._waiters.add(registration)
            try:
                await asyncio.wait_for(future, remaining)
            except TimeoutError:
                raise RuntimeTransitionError(code) from None
            finally:
                with self._condition:
                    self._waiters.discard(registration)

    async def wait_for_phase(self, phase, timeout=30):
        """An observable lifecycle barrier for host tooling and deterministic tests."""
        await self._wait(lambda: self._phase == phase, timeout, "admission_timeout")
        return self.status()

    def _inherited(self):
        for lease in reversed(_SCOPES.get()):
            if lease._runtime is self:
                if lease._released:
                    raise RuntimeTransitionError("lease_expired")
                return lease
        return None

    def _new_lease(self):
        if self._closed:
            raise RuntimeTransitionError("runtime_closed")
        lease = RuntimeLease(self, self._active)
        number = lease.generation.number
        self._leases[number] = self._leases.get(number, 0) + 1
        self._notify()
        return lease

    def _retain(self, lease):
        with self._condition:
            if lease._released:
                raise RuntimeTransitionError("lease_expired")
            lease._references += 1

    def _release(self, lease):
        with self._condition:
            lease._references -= 1
            if lease._references:
                return
            lease._released = True
            number = lease.generation.number
            self._leases[number] -= 1
            if not self._leases[number]:
                del self._leases[number]
            self._notify()

    @contextmanager
    def lease(self, timeout=30):
        """Synchronous operation scope. Async callers use ``request()``."""
        _timeout(timeout)
        inherited = self._inherited()
        if inherited:
            self._retain(inherited)
            try:
                yield inherited
            finally:
                self._release(inherited)
            return
        deadline = monotonic() + timeout
        with self._condition:
            while self._gate and not self._closed:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    pass
                else:
                    raise RuntimeTransitionError("runtime_paused")
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise RuntimeTransitionError("admission_timeout")
                self._condition.wait(remaining)
            lease = self._new_lease()
        token = _SCOPES.set((*_SCOPES.get(), lease))
        try:
            yield lease
        finally:
            _SCOPES.reset(token)
            self._release(lease)

    @asynccontextmanager
    async def request(self, timeout=30, *, independent=False):
        _timeout(timeout)
        inherited = None if independent else self._inherited()
        if inherited:
            self._retain(inherited)
            try:
                yield inherited
            finally:
                self._release(inherited)
            return
        # Check and admit under one lock: a transition cannot gate the budget
        # between observing an open gate and counting this operation.
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            with self._condition:
                if self._closed or not self._gate:
                    lease = self._new_lease()
                    break
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            await self._wait(lambda: self._closed or not self._gate, remaining, "admission_timeout")
        token = _SCOPES.set((*_SCOPES.get(), lease))
        try:
            yield lease
        finally:
            _SCOPES.reset(token)
            self._release(lease)

    def background(self, timeout=30):
        return self.request(timeout, independent=True)

    def schedule(self, coroutine):
        """Own host transition/control work outside the scheduling request lease.

        This is not a plugin background-job API. Detached plugin operations must
        explicitly acquire background() so retirement observes their lifetime.
        """
        context = copy_context()
        context.run(_SCOPES.set, tuple(lease for lease in _SCOPES.get() if lease._runtime is not self))
        task = asyncio.create_task(coroutine, context=context)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    async def _owned(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return await asyncio.shield(task)

    def _finished(self, task):
        self._tasks.discard(task)
        # Observe failures even when the initiating HTTP request disconnected.
        if not task.cancelled():
            task.exception()

    async def _retire(self, resources, keep, timeout):
        retained = {id(resource) for resource in keep}
        for resource in reversed(resources):
            if id(resource) in retained:
                continue
            task = asyncio.create_task(_invoke(resource.close))
            self._cleanup.add(task)
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except TimeoutError:
                with self._condition:
                    self._cleanup_errors.append(self._diagnostic("retirement_timeout", resource.plugin_id))
            except (Exception, asyncio.CancelledError):
                with self._condition:
                    self._cleanup_errors.append(self._diagnostic("retirement_failed", resource.plugin_id))
            finally:
                if task.done():
                    self._cleanup.discard(task)
                else:
                    task.add_done_callback(lambda finished, owner=resource.plugin_id: self._cleanup_finished(finished, owner))
                with self._condition:
                    self._notify()

    def _cleanup_finished(self, task, plugin_id):
        with self._condition:
            self._cleanup.discard(task)
            if task.cancelled() or task.exception() is not None:
                self._cleanup_errors.append(self._diagnostic("retirement_failed", plugin_id))
            self._notify()

    async def wait_cleanup(self, timeout=30):
        """Wait for tracked slow cleanup without cancelling it on a deadline."""
        await self._wait(lambda: not self._cleanup, timeout, "retirement_timeout")
        return self.status()

    def _check_transition(self):
        if self._inherited() is not None:
            raise RuntimeTransitionError("transition_in_operation")
        with self._condition:
            if self._closed:
                raise RuntimeTransitionError("runtime_closed")
            if self._uncertain:
                raise RuntimeTransitionError("recovery_required")

    def _begin(self, plugin_id):
        with self._condition:
            self._sequence += 1
            self._pending = {"id": self._sequence, "plugin_id": plugin_id,
                             "from_generation": self._active.number,
                             "to_generation": self._active.number + 1}
            self._error = None
            return self._active

    def _resume(self, phase="active"):
        with self._condition:
            self._gate = False
            self._pending = None
            self._phase = phase
            self._notify()

    async def transition(self, prepare, *, readiness=None, finalize=None, commit=None, plugin_id=None,
                         drain_timeout=30, retirement_timeout=5):
        """Prepare/readiness, gate/drain, finalize, commit, activate, then retire.

        ``prepare(preparation)`` returns a RuntimeCandidate. Register acquired
        resources with preparation.own() before operations that can fail.
        ``readiness(candidate)`` must return True. Optional
        ``finalize(preparation, candidate)`` returns a RuntimeCandidate using the
        latest drained state. Its factories must use staged contexts; failure
        resumes the unchanged active generation and retires acquired resources.
        ``commit(candidate, number)``
        durably commits selection/authority when supplied; failure is uncertain.
        The coordinator does not cancel callbacks or admitted financial work.
        """
        self._check_transition()
        if not callable(prepare) or any(callback is not None and not callable(callback) for callback in (readiness, finalize, commit)) or (plugin_id is not None and not valid_plugin_name(plugin_id)):
            raise RuntimeTransitionError("invalid_runtime")
        _timeout(drain_timeout)
        _timeout(retirement_timeout)
        return await self._owned(self._transition(prepare, readiness, finalize, commit, plugin_id,
                                                 drain_timeout, retirement_timeout))

    async def _transition(self, prepare, readiness, finalize, commit, plugin_id, drain_timeout, retirement_timeout):
        async with self._serial:
            self._check_transition()
            active = self._begin(plugin_id)
            preparation = RuntimePreparation(active)
            candidate = None
            code = "prepare_failed"
            try:
                self._progress("preparing", 10)
                candidate = await _invoke(prepare, preparation)
                if type(candidate) is not RuntimeCandidate:
                    raise RuntimeTransitionError("invalid_runtime")
                for resource in candidate.resources:
                    preparation.own(resource)
                candidate = RuntimeCandidate(candidate.services, tuple(preparation._resources))
                self._progress("readiness", 30)
                code = "readiness_failed"
                if readiness is not None and await _invoke(readiness, candidate) is not True:
                    raise RuntimeTransitionError(code)
                with self._condition:
                    self._gate = True
                self._progress("draining", 50)
                code = "drain_timeout"
                await self._wait(lambda: not self._leases, drain_timeout, code)
                if finalize is not None:
                    self._progress("finalizing", 60)
                    code = "readiness_failed"
                    candidate = await _invoke(finalize, preparation, candidate)
                    if type(candidate) is not RuntimeCandidate:
                        raise RuntimeTransitionError("invalid_runtime")
                    for resource in candidate.resources:
                        preparation.own(resource)
                    retained = {id(resource) for resource in candidate.resources}
                    old = {id(resource) for resource in active.resources}
                    candidate = RuntimeCandidate(candidate.services, tuple(
                        resource for resource in preparation._resources
                        if id(resource) not in old or id(resource) in retained
                    ))
            except (Exception, asyncio.CancelledError) as error:
                owner = plugin_id
                if isinstance(error, PluginError) and valid_plugin_name(error.plugin_id):
                    owner = error.plugin_id
                if isinstance(error, RuntimeTransitionError) and error.code == "continuation_busy":
                    code, owner = error.code, error.plugin_id
                with self._condition:
                    self._error = self._diagnostic(code, owner)
                self._resume("failed")
                await self._retire(tuple(preparation._resources), active.resources, retirement_timeout)
                raise RuntimeTransitionError(code, owner) from None
            self._progress("committing", 70)
            try:
                if commit is not None:
                    await _invoke(commit, candidate, active.number + 1)
            except (Exception, asyncio.CancelledError):
                with self._condition:
                    self._uncertain = (active, candidate, retirement_timeout)
                    self._error = self._diagnostic("commit_uncertain", plugin_id)
                self._progress("recovery_required", 70)
                raise RuntimeTransitionError("commit_uncertain", plugin_id) from None
            return await self._activate(active, candidate, retirement_timeout)

    async def _activate(self, active, candidate, retirement_timeout):
        with self._condition:
            self._active = RuntimeGeneration(active.number + 1, candidate.services, candidate.resources)
            self._uncertain = None
        self._progress("retiring", 90)
        # New operations may use the ready generation while cleanup proceeds.
        self._resume("retiring")
        await self._retire(active.resources, candidate.resources, retirement_timeout)
        self._progress("active", 100)
        return self._active.number

    async def recover(self, authority, validate):
        """Explicitly validate active/candidate authority after an uncertain commit."""
        if self._inherited() is not None:
            raise RuntimeTransitionError("transition_in_operation")
        if type(authority) is not str or authority not in {"active", "candidate"} or not callable(validate):
            raise RuntimeTransitionError("invalid_runtime")
        return await self._owned(self._recover(authority, validate))

    async def _recover(self, authority, validate):
        async with self._serial:
            with self._condition:
                uncertain = self._uncertain
            if uncertain is None:
                raise RuntimeTransitionError("recovery_required")
            active, candidate, timeout = uncertain
            try:
                if await _invoke(validate, authority, active, candidate) is not True:
                    raise RuntimeTransitionError("recovery_failed")
            except (Exception, asyncio.CancelledError):
                raise RuntimeTransitionError("recovery_failed") from None
            with self._condition:
                self._error = None
                self._uncertain = None
            if authority == "candidate":
                return await self._activate(active, candidate, timeout)
            self._resume("retiring")
            await self._retire(candidate.resources, active.resources, timeout)
            self._progress("active", 100)
            return active.number

    async def close(self, *, drain_timeout=30, retirement_timeout=5):
        self._check_transition()
        _timeout(drain_timeout)
        _timeout(retirement_timeout)
        return await self._owned(self._close(drain_timeout, retirement_timeout))

    async def shutdown(self, *, drain_timeout=30, retirement_timeout=5):
        """Retire all owned resources at host exit without deciding authority.

        An uncertain activation remains uncertain in its owner's durable record.
        Closing its resources grants neither candidate nor active new writes.
        Recovery on the next process start must validate that record first.
        """
        if self._inherited() is not None:
            raise RuntimeTransitionError("transition_in_operation")
        _timeout(drain_timeout)
        _timeout(retirement_timeout)
        return await self._owned(self._shutdown(drain_timeout, retirement_timeout))

    async def _shutdown(self, drain_timeout, retirement_timeout):
        async with self._serial:
            with self._condition:
                if self._closed:
                    return
                self._gate = True
                resources = list(self._active.resources)
                if self._uncertain:
                    resources.extend(resource for resource in self._uncertain[1].resources
                                     if all(resource is not previous for previous in resources))
            self._progress("draining", 50)
            await self._wait(lambda: not self._leases, drain_timeout, "drain_timeout")
            with self._condition:
                self._closed = True
            await self._retire(tuple(resources), (), retirement_timeout)
            self._progress("closed", 100)

    async def _close(self, drain_timeout, retirement_timeout):
        async with self._serial:
            self._check_transition()
            with self._condition:
                self._gate = True
            self._progress("draining", 50)
            try:
                await self._wait(lambda: not self._leases, drain_timeout, "drain_timeout")
            except RuntimeTransitionError:
                self._resume("active")
                raise
            with self._condition:
                self._closed = True
            await self._retire(self._active.resources, (), retirement_timeout)
            self._progress("closed", 100)
