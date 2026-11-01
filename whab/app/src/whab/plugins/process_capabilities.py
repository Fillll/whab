"""Budget-scoped host services exported to one enabled local plugin process."""

from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import replace
from threading import local
from weakref import ref

from whab.domain.errors import StoreError

from .process_rpc import ProcessUnavailable
from .sdk import BudgetContext, InterfaceContext


class OperationLock:
    def __init__(self, service):
        self.service = service
        self._local = local()

    def __enter__(self):
        stack = ExitStack()
        try:
            stack.enter_context(self.service.scope())
            stack.enter_context(self.service._service.operation_lock)
        except BaseException:
            stack.close()
            raise
        if not hasattr(self._local, "stacks"):
            self._local.stacks = []
        self._local.stacks.append(stack)

    def __exit__(self, *_):
        self._local.stacks.pop().close()


class ScopedService:
    def __init__(self, service, process):
        self._service, self._owner = service, ref(process)
        self._lock = OperationLock(self)

    @contextmanager
    def scope(self):
        owner = self._owner()
        if owner is None or not owner.healthy:
            raise ProcessUnavailable()
        runtime = getattr(owner, "runtime", None)
        with runtime.lease() if runtime is not None else nullcontext():
            if not owner.healthy:
                raise ProcessUnavailable()
            yield

    @property
    def operation_lock(self):
        return self._lock

    @property
    def budget_id(self):
        return self._service.budget_id

    @property
    def revision(self):
        with self.scope():
            return self._service.revision

    def query(self, *args, **kwargs):
        with self.scope():
            return self._service.query(*args, **kwargs)

    def command(self, *args, **kwargs):
        with self.scope():
            return self._service.command(*args, **kwargs)

    def external_mutation(self, operation):
        with self.scope():
            return self._service.external_mutation(operation)

    def register_feature(self, plugin_id, operations):
        owner = self._owner()
        if owner is None or plugin_id != owner.plugin_id:
            raise StoreError("A plugin can register only its own feature operations.")
        with self.scope():
            return self._service.register_feature(plugin_id, operations)

    def feature_adapter(self, plugin_id):
        owner = self._owner()
        if owner is None or plugin_id not in {owner.plugin_id, *owner.dependencies}:
            raise StoreError("Declare a plugin dependency before requesting its public capability.")
        with self.scope():
            return self._service.feature_adapter(plugin_id)


def prepare_record(value, owner):
    process = owner()
    if process is None:
        raise ProcessUnavailable()
    if type(value) is InterfaceContext and not isinstance(value.service, ScopedService):
        return replace(value, service=ScopedService(value.service, process))
    if type(value) is BudgetContext:
        def guarded(callback):
            if callback is None:
                return None

            def invoke(*args):
                current = owner()
                if current is None or not current.healthy:
                    raise ProcessUnavailable()
                runtime = getattr(current, "runtime", None)
                with runtime.lease() if runtime is not None else nullcontext():
                    if not current.healthy:
                        raise ProcessUnavailable()
                    return callback(*args)

            return invoke

        return replace(value, **{name: guarded(getattr(value, name))
                                 for name in ("save_state", "read_file", "write_file", "delete_file")})
    return value
