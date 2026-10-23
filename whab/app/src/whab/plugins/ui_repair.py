"""Explicit stopped-host visual repair without executing the saved environment."""

import os
import stat
import tempfile
from pathlib import Path
from threading import RLock
from uuid import NAMESPACE_URL, uuid5

from whab.web.visual_interface import VisualGeneration

from .effect_store import EffectError, _mkdir, digest, encode, fsync_directory
from .environments import prepare_environment
from .runtime import PluginError
from .sdk import Plugin, PluginContract, PluginResources, UserInterface, valid_plugin_name
from .ui_resources import UIResourceArchive
from .ui_selection import DISABLED_GENERATION, UISelectionStore
from .ui_worker import proxy_context, readiness_arguments, supports_live_visual
from .worker_selection import _start

_CLEANUP = {}
_CLEANUP_LOCK = RLock()


def _retire(home, worker):
    key = str(Path(home).resolve())
    with _CLEANUP_LOCK:
        retained = _CLEANUP.setdefault(key, [])
        if all(worker is not prior for prior in retained):
            retained.append(worker)
        worker.close()
        if worker.cleanup_pending:
            raise _failed()
        retained.remove(worker)
        if not retained:
            _CLEANUP.pop(key, None)


def _reap(home):
    with _CLEANUP_LOCK:
        for worker in tuple(_CLEANUP.get(str(Path(home).resolve()), ())):
            _retire(home, worker)


def _failed():
    return PluginError("The visual repair could not be validated or published. Stop this budget and preserve its saved decision before retrying.",
                       code="ui_repair_failed")


def _revision(value):
    if (type(value) is not str or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise _failed()


def _owner(home, budget_id, environment):
    path = Path(home) / "visual-ui"
    if path.is_symlink() or not (path / "visual-selection.json").is_file():
        raise _failed()
    return UISelectionStore(home, budget_id, environment, None)


def visual_worker_revision(home, budget_id, environment):
    """Opaque stopped-host CAS; a running owner refuses this second writer."""
    try:
        with _owner(home, budget_id, environment) as selected:
            return selected.revision(selected.snapshot())
    except Exception:
        raise _failed() from None


def _history(selected, revision):
    path = selected.store.root / "history"
    if path.is_symlink():
        raise _failed()
    descriptor = os.open(path / (revision + ".json"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        information = os.fstat(descriptor)
        if (not stat.S_ISREG(information.st_mode) or information.st_mode & 0o077
                or not 0 < information.st_size <= selected.store.limit):
            raise _failed()
        raw = os.read(descriptor, selected.store.limit + 1)
        if selected.revision(selected.store._validated(raw)["body"]) != revision:
            raise _failed()
        return raw
    finally:
        os.close(descriptor)


def _preserve(selected, revision):
    directory = selected.store.root / "history"
    _mkdir(directory)
    raw = selected.store._raw
    descriptor, temporary = tempfile.mkstemp(prefix=".visual-choice-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, directory / (revision + ".json"))
        except FileExistsError:
            if _history(selected, revision) != raw:
                raise _failed()
        fsync_directory(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _publish(selected, expected_revision, selection, generation, worker):
    _revision(expected_revision)
    identity = digest(encode({"scope": selected.store.scope, "expected": expected_revision,
                             "selection": selection, "generation": generation, "worker": worker}, 4096))
    decision = {"id": str(uuid5(NAMESPACE_URL, "whab-visual-repair-v1:" + identity)),
                "selection": selection, "generation": generation, "worker": worker}
    with selected.store.lock:
        current = selected.snapshot()
        if selected.revision(current) == expected_revision:
            _preserve(selected, expected_revision)
        else:
            if decision not in current["decisions"]:
                raise EffectError("effect_conflict")
            _history(selected, expected_revision)
        return selected.publish(decision, expected_revision)


def _candidate(home, plugin_id, approvals):
    """Only fresh approved code executes; the native/saved descriptor is unused."""
    _reap(home)
    prepared = prepare_environment(Path(home) / "plugin-workers" / "environments", plugin_id, approvals)
    template = Plugin(plugin_id, plugin_id, contract=PluginContract(lifecycle="live"), ui=UserInterface(
        None, None, proxy_context, resources=PluginResources({"templates/base.html": b""}), view_protocol=1))
    proxy, binding = _start(template, prepared, home)
    try:
        if not supports_live_visual(proxy):
            raise _failed()
        visual = VisualGeneration.prepare(proxy, UIResourceArchive(home), code_identity=prepared.identifier)
        for page in ("budget", "transactions", "insights", "accounts"):
            binding.worker.render_ui(readiness_arguments(page))
        _retire(home, binding.worker)
        return visual, {"environment": prepared.identifier, "distribution": prepared.distribution, "version": prepared.version}
    except BaseException:
        try:
            _retire(home, binding.worker)
        except Exception:
            pass
        raise


def replace_saved_visual_worker(home, budget_id, environment, plugin_id, approvals, *, expected_revision):
    """Publish an approved current-host UI after stopping its owning budget.

    No saved/native UI code or financial backend opens. Retain the exact prior
    decision and all resource/environment generations. An exact lost-reply retry
    returns its original decision; it cannot reselect an older choice.
    """
    try:
        _revision(expected_revision)
        if not valid_plugin_name(plugin_id):
            raise _failed()
        with _owner(home, budget_id, environment) as selected:
            current = selected.snapshot()
            if selected.revision(current) != expected_revision:
                _history(selected, expected_revision)
            if current["selection"] != plugin_id:
                # A completed exact retry can be recognized by its receipt even
                # after a later explicit disable; no older code is executed.
                if selected.revision(current) == expected_revision:
                    raise EffectError("effect_conflict")
            visual, worker = _candidate(home, plugin_id, approvals)
            return _publish(selected, expected_revision, plugin_id, visual.generation, worker)
    except EffectError:
        raise
    except Exception:
        raise _failed() from None


def disable_saved_visual(home, budget_id, environment, *, expected_revision):
    """Explicitly select headless startup; retained UI versions/state survive."""
    try:
        with _owner(home, budget_id, environment) as selected:
            return _publish(selected, expected_revision, None, DISABLED_GENERATION, None)
    except EffectError:
        raise
    except Exception:
        raise _failed() from None
