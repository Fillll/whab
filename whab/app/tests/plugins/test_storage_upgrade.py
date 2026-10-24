"""Actual approved backend wheels, gated handover and durable reopen decisions."""

import asyncio
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.plugins.environments import prepare_environment

from .test_environments import approve, local_closure  # noqa: F401


def native_wheel(directory, kind, version, *, broken=False, crash_after_commit=False):
    root = Path(__file__).resolve().parents[2]
    project = directory / f"{kind}-{version}"
    shutil.copytree(root / "plugins" / "storage" / kind, project,
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build", "dist", ".coverage*"))
    metadata = project / "pyproject.toml"
    metadata.write_text(re.sub(r'^version = ".*"', f'version = "{version}"', metadata.read_text(), count=1, flags=re.M))
    if broken:
        source = project / "src" / f"whab_plugin_{kind}" / "store.py"
        source.write_text(source.read_text().replace(
            '        if type(initial_role) is not str',
            '        if read_only:\n            raise RuntimeError("fictional-private-candidate-error")\n        if type(initial_role) is not str', 1))
    if crash_after_commit:
        source = project / "src" / f"whab_plugin_{kind}" / "store.py"
        source.write_text(source.read_text() + "\n_original_execute = Store.execute_operation\ndef _crash_after_native_commit(self, operation):\n    _original_execute(self, operation)\n    os._exit(44)\nStore.execute_operation = _crash_after_native_commit\n")
    wheels = directory / "wheels"
    subprocess.run([sys.executable, "-I", "-m", "pip", "wheel", "--disable-pip-version-check", "--no-deps", "--wheel-dir", str(wheels), str(project)],
                   cwd=directory, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    return approve(next(wheels.glob(f"whab_plugin_{kind}-{version}-*.whl")))


def configuration(home, kind):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), storage=kind, ui="")


def settle(client, app):
    async def wait():
        if app.state.live_plugins._tasks:
            await asyncio.gather(*tuple(app.state.live_plugins._tasks), return_exceptions=True)
    client.portal.call(wait)


def upgrade(client, app, kind, approvals):
    response = client.post("/api/plugins/management/upgrade", json={
        "pluginId": kind, "wheels": [{"path": str(w.path), "sha256": w.sha256} for w in approvals],
    })
    assert response.status_code == 202
    settle(client, app)
    return next(row for row in client.get("/api/plugins/runtime").json()["operations"] if row["id"] == response.json()["id"])


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_actual_backend_wheels_upgrade_reject_bad_readiness_and_reopen_exact_version(kind, tmp_path, local_closure):  # noqa: F811
    first = native_wheel(tmp_path, kind, "9.0.1")
    broken = native_wheel(tmp_path, kind, "9.0.2", broken=True)
    last = native_wheel(tmp_path, kind, "9.0.3")
    crashing = native_wheel(tmp_path, kind, "9.0.4", crash_after_commit=True)
    settings = configuration(tmp_path / "runtime", kind)
    app = create_app(settings=settings)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        response = client.post("/api/accounts", json={"workspace": "personal", "name": "Fictional", "type": "depository", "balance": 12345})
        assert response.status_code == 200
        original = app.state.tracked_store.commit_evidence()
        ledger, revision = app.state.ledger_service, app.state.ledger_service.revision
        assert upgrade(client, app, kind, (*local_closure, first))["state"] == "complete"
        binding = app.state.storage_upgrade.binding
        assert binding.environment.version == "9.0.1" and binding.worker.healthy
        assert app.state.tracked_store.commit_evidence() == original
        assert app.state.ledger_service is ledger and ledger.revision == revision
        failed = upgrade(client, app, kind, (*local_closure, broken))
        assert failed["state"] == "failed" and "fictional-private" not in str(failed)
        assert app.state.storage_upgrade.binding is binding and binding.worker.healthy
        assert app.state.tracked_store.commit_evidence() == original
        assert upgrade(client, app, kind, (*local_closure, last))["state"] == "complete"
        assert upgrade(client, app, kind, (*local_closure, crashing))["state"] == "complete"
        lost = client.post("/api/accounts", json={"workspace": "personal", "name": "Committed before child death", "type": "depository", "balance": 111})
        assert lost.status_code == 400 and "unknown" in lost.json()["error"]
        assert app.state.budget_runtime.status()["health"] == "recovery_required"
        recovered = client.post("/api/plugins/management/recover", json={"authority": "active"})
        assert recovered.status_code == 200, recovered.json()
        assert len(app.state.ledger_service.accounts("personal")) == 2
        assert upgrade(client, app, kind, (*local_closure, last))["state"] == "complete"
        selected = app.state.storage_upgrade.binding.environment.identifier
        assert app.state.tracked_store.commit_evidence().generation == original.generation + 1
        late = client.post("/api/accounts", json={"workspace": "personal", "name": "Late acknowledged", "type": "depository", "balance": 67890})
        assert late.status_code == 200
        final = app.state.tracked_store.commit_evidence()
        assert final.instance_id == original.instance_id and final.authority_epoch == original.authority_epoch
        assert final.generation == original.generation + 2
        assert app.state.budget_runtime.status()["health"] == "ready"
    reopened = create_app(settings=settings)
    with TestClient(reopened, base_url="https://127.0.0.1:5173") as client:
        assert reopened.state.storage_upgrade.binding.environment.identifier == selected
        assert reopened.state.storage_upgrade.binding.environment.version == "9.0.3"
        assert reopened.state.tracked_store.commit_evidence() == final
        assert sorted(row["balance"] for row in reopened.state.ledger_service.accounts("personal")) == [111, 12345, 67890]
        assert client.get("/api/storage/status").json()["code"]["mode"] == "worker"


def test_storage_environment_uses_explicit_v2_receipt(tmp_path, local_closure):  # noqa: F811
    wheel = native_wheel(tmp_path, "json", "9.1.0")
    prepared = prepare_environment(tmp_path / "prepared", "json", (*local_closure, wheel), family="storage")
    import json
    record = json.loads((prepared.path / "ready.json").read_bytes())
    assert record["format"] == 2 and record["family"] == "storage"
