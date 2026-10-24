"""No portable/export observer sees a partially materialized worker effect."""

import pytest
from whab_plugin_sqlite import Store

from whab.budgets.container import decode_container
from whab.budgets.session import BudgetSession
from whab.domain.errors import StoreError


@pytest.fixture
def opened(tmp_path):
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "Fictional", ("fictional-categorizer",), path=tmp_path / "budget.whab")
    store = Store(session.storage_home / "ledger.sqlite", month="2026-10")
    session.bind(store)
    try:
        yield session
    finally:
        store.close()


def test_nested_materialization_publishes_exactly_one_complete_namespace(opened):
    session = opened
    original = session.path.read_bytes()
    notifications = []
    session.after_checkpoint = lambda: notifications.append(session.path.read_bytes())
    with session.namespace_materialization():
        session.write_file("fictional-categorizer", "model.bin", b"new synthetic model")
        assert session.path.read_bytes() == original and not notifications
        with session.namespace_materialization():
            session.write_file("fictional-categorizer", "models.json", b'{"model":"synthetic"}')
            session.save_plugin_data("fictional-categorizer", state={"active": "synthetic"})
        with pytest.raises(StoreError, match="Recover"):
            session.bundle()
        assert session.path.read_bytes() == original and not notifications
    assert len(notifications) == 1
    snapshot = decode_container(notifications[0])
    assert snapshot.files["plugins/fictional-categorizer/model.bin"] == b"new synthetic model"
    assert snapshot.files["plugins/fictional-categorizer/models.json"] == b'{"model":"synthetic"}'
    assert next(row for row in snapshot.manifest["plugins"] if row["id"] == "fictional-categorizer")["state"] == {"active": "synthetic"}


def test_failure_fences_future_checkpoints_until_complete_recovery_batch(opened):
    session = opened
    original = session.path.read_bytes()
    notifications = []
    session.after_checkpoint = lambda: notifications.append(True)
    with pytest.raises(OSError):
        with session.namespace_materialization():
            session.write_file("fictional-categorizer", "model.bin", b"staged partial native file")
            raise OSError("synthetic interrupted materialization")
    session.checkpoint()
    assert session.path.read_bytes() == original and not notifications
    with pytest.raises(StoreError):
        session.bundle()
    with session.namespace_materialization():
        session.write_file("fictional-categorizer", "models.json", b"completed matching index")
    assert len(notifications) == 1
    assert decode_container(session.path.read_bytes()).files["plugins/fictional-categorizer/models.json"] == b"completed matching index"


def test_caught_inner_failure_still_fences_outer_publication(opened):
    original = opened.path.read_bytes()
    with pytest.raises(StoreError, match="complete recovery"):
        with opened.namespace_materialization():
            try:
                with opened.namespace_materialization():
                    opened.write_file("fictional-categorizer", "model.bin", b"partial")
                    raise OSError("synthetic inner interruption")
            except OSError:
                pass
    assert opened.path.read_bytes() == original


def test_startup_binding_stays_unpublished_until_outer_namespace_recovery(tmp_path):
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "Fictional", ("fictional-categorizer",), path=tmp_path / "budget.whab")
    store = Store(session.storage_home / "ledger.sqlite", month="2026-10")
    try:
        with session.namespace_materialization():
            session.bind(store)
            assert not session.path.exists()
            session.write_file("fictional-categorizer", "model.bin", b"recovered complete data")
        assert session.path.is_file()
    finally:
        store.close()


def test_export_rechecks_fence_after_waiting_for_materialization_lock(opened):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread

    waiting = Event()
    original = opened.lock
    class ObservedLock:
        def __enter__(self):
            if current_thread().name.startswith('export-reader'):
                waiting.set()
            original.acquire()
            return self
        def __exit__(self, *_):
            original.release()
    opened.lock = ObservedLock()
    with ThreadPoolExecutor(1, thread_name_prefix='export-reader') as executor:
        with original:
            future = executor.submit(opened.bundle)
            assert waiting.wait(5)
            with pytest.raises(OSError):
                with opened.namespace_materialization():
                    opened.write_file('fictional-categorizer', 'model.bin', b'partial staged bytes')
                    raise OSError('synthetic interruption before matching index')
        with pytest.raises(StoreError, match='Recover'):
            future.result(timeout=5)
