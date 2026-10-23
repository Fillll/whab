"""Versioned environments install only approved local artifacts and fictional reports."""

import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

import pytest

from whab import __version__
from whab.plugins import BudgetSnapshot
from whab.plugins import environments as environments
from whab.plugins.environments import (
    ApprovedWheel,
    EnvironmentPreparationError,
    inspect_wheel,
    prepare_environment,
)
from whab.plugins.report_worker import ReportWorker


def test_application_upgrades_real_reports_without_losing_ledger_or_reopen_version(tmp_path, local_closure, store, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import (
        ExportTable,
        Insight,
        Plugin,
        PluginContract,
        PluginError,
        PluginRegistry,
    )

    template = Plugin("fictional-reports", "Fictional reports",
                      insights=lambda _: (Insight("Version", "host"),),
                      export=lambda _: ExportTable(("version",), (("host",),)),
                      contract=PluginContract(lifecycle="live"))
    entry = SimpleNamespace(name=template.id, group="whab.plugins", load=lambda: template)
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: (entry,))
    monkeypatch.setattr("whab.app.installed_entrypoints", lambda: (entry,))
    configuration = Settings(tmp_path / "runtime", 5173, "sandbox", "", "", "", False,
                             "", "", (template.id,), ui="")
    def registry():
        return PluginRegistry((template,))
    app = create_app(store=store, settings=configuration, plugins=registry())

    def settle(client):
        async def wait():
            if app.state.live_plugins._tasks:
                await asyncio.gather(*tuple(app.state.live_plugins._tasks), return_exceptions=True)
        client.portal.call(wait)

    def upgrade(client, wheel):
        response = client.post("/api/plugins/management/upgrade", json={
            "pluginId": template.id,
            "wheels": [{"path": str(item.path), "sha256": item.sha256} for item in (*local_closure, approve(wheel))],
        })
        assert response.status_code == 202
        settle(client)
        return next(item for item in client.get("/api/plugins/runtime").json()["operations"] if item["id"] == response.json()["id"])

    def exported(client):
        response = client.get("/api/plugins/fictional-reports/export?workspace=personal&month=2026-10")
        assert response.status_code == 200
        return response.text

    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        account = client.post("/api/accounts", json={"workspace": "personal", "name": "Fictional stable account",
                                                   "type": "depository", "balance": 12345}).json()["accountId"]
        ledger, revision = app.state.ledger_service, app.state.ledger_service.revision
        assert "host" in exported(client)
        assert upgrade(client, make_wheel(tmp_path, "1.0.0"))["state"] == "complete"
        first = app.state.budget_runtime._active.services["plugins"].workers[template.id]
        assert "1.0.0" in exported(client) and first.worker.healthy
        broken = make_wheel(tmp_path, "3.0.0", source="raise RuntimeError('synthetic-private-startup')\n")
        assert upgrade(client, broken)["state"] == "failed"
        assert "1.0.0" in exported(client) and first.worker.healthy
        assert app.state.budget_runtime.status()["generation"] == 2
        broken_hook = make_wheel(tmp_path, "3.1.0", source=(
            "from whab.plugins import Plugin, PluginContract, Insight\n"
            "def broken(snapshot):\n    raise RuntimeError('synthetic-private-hook-detail')\n"
            "plugin=Plugin('fictional-reports','Fictional reports',insights=broken,contract=PluginContract(lifecycle='live'))\n"
        ))
        assert upgrade(client, broken_hook)["state"] == "failed"
        assert "1.0.0" in exported(client) and first.worker.healthy
        assert app.state.budget_runtime.status()["generation"] == 2
        from whab.plugins import worker_selection

        original_save = worker_selection.save_reference

        def lost_response(home, prepared):
            original_save(home, prepared)
            raise OSError("synthetic failure after durable report decision")

        monkeypatch.setattr(worker_selection, "save_reference", lost_response)
        assert upgrade(client, make_wheel(tmp_path, "2.0.0"))["state"] == "failed"
        assert app.state.budget_runtime.status()["health"] == "recovery_required"
        monkeypatch.setattr(worker_selection, "save_reference", original_save)
        refused = client.post("/api/plugins/management/recover", json={"authority": "active"})
        assert refused.status_code == 409 and refused.json()["code"] == "recovery_failed"
        recovered = client.post("/api/plugins/management/recover", json={"authority": "candidate"})
        assert recovered.status_code == 200 and recovered.json()["generation"] == 3
        second = app.state.budget_runtime._active.services["plugins"].workers[template.id]
        assert "2.0.0" in exported(client) and not first.worker.healthy
        assert app.state.ledger_service is ledger and ledger.revision == revision
        for selection in ([], [template.id]):
            response = client.post("/api/plugins/management/transition", json={"plugins": selection})
            assert response.status_code == 202
            settle(client)
        assert not second.worker.healthy and "2.0.0" in exported(client)
        versions = client.get("/api/plugins/runtime").json()["workers"]
        assert versions[0]["version"] == "2.0.0" and versions[0]["health"] == "ready"
        assert "fictional_reports" not in sys.modules
    # A fresh application loads its saved content-addressed environment rather
    # than the host's still-installed report implementation.
    app = create_app(store=store, settings=configuration, plugins=registry())
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert "2.0.0" in exported(client)
        found = client.get("/api/workspace?workspace=personal").json()["accounts"][0]
        assert found["id"] == account and found["balance"] == 12345
    code, = second.environment.path.glob("environment/lib/python*/site-packages/fictional_reports/__init__.py")
    code.write_text("raise RuntimeError('tampered environment must not execute')\n")
    with pytest.raises(PluginError) as raised:
        create_app(store=store, settings=configuration, plugins=registry())
    assert raised.value.code == "worker_environment_unavailable"


def approve(path):
    return ApprovedWheel(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_real_visual_worker_versions_keep_code_resource_identity_and_failed_readiness_preserves_old(tmp_path, local_closure):
    from whab.plugins import Plugin, PluginContract, PluginResources, UserInterface
    from whab.plugins.ui_resources import UIResourceArchive, capture_visual
    from whab.plugins.ui_worker import readiness_arguments
    from whab.plugins.worker_selection import _start

    def wheel(version, *, broken=False):
        source = (
            "from pathlib import Path\n"
            "from whab.plugins import Plugin, PluginContract, UserInterface\n"
            "root = Path(__file__).parent\n"
            "def context(data, status, items, **values):\n"
            f"    return {{'data': data, 'amount': lambda amount: '{version}:' + str(amount)}}\n"
            "plugin=Plugin('fictional-reports','Fictional visual',ui=UserInterface(root/'templates',root/'static',context,view_protocol=1),contract=PluginContract(lifecycle='live'))\n"
        )
        files = {"fictional_reports/templates/base.html": b"<p>{{ amount(data.summary.readyToAssign) }}</p>"}
        if not broken:
            files["fictional_reports/static/app.js"] = f"export const version = '{version}';".encode()
        return approve(make_wheel(tmp_path, version, source=source, extra_files=files))

    template = Plugin("fictional-reports", "Fictional visual", ui=UserInterface(
        None, None, lambda *args, **values: {}, resources=PluginResources({
            "templates/base.html": b"host", "static/app.js": b"host",
        }), view_protocol=1), contract=PluginContract(lifecycle="live"))
    root = tmp_path / "approved"
    archive = UIResourceArchive(tmp_path / "runtime")
    first = prepare_environment(root, template.id, (*local_closure, wheel("1.0.0")))
    old_proxy, old = _start(template, first)
    try:
        old_bundle = archive.store(capture_visual(template.id, old_proxy.ui, first.identifier))
        assert old_proxy.ui.render(readiness_arguments("budget")) == b"<p>1.0.0:0</p>"
        assert old_proxy.ui.build_context({}, {}, [], workspace="personal", page="budget", month="2000-01", selected=None)["_ui_inputs"]["page"] == "budget"
        with pytest.raises(EnvironmentPreparationError):
            prepare_environment(root, template.id, (*local_closure, wheel("1.1.0", broken=True)))
        assert old.worker.healthy
        assert old_proxy.ui.render(readiness_arguments("accounts")) == b"<p>1.0.0:0</p>"
        second = prepare_environment(root, template.id, (*local_closure, wheel("2.0.0")))
        new_proxy, new = _start(template, second)
        try:
            new_bundle = archive.store(capture_visual(template.id, new_proxy.ui, second.identifier))
            assert new_bundle.generation != old_bundle.generation
            assert new_proxy.ui.render(readiness_arguments("transactions")) == b"<p>2.0.0:0</p>"
            assert archive.load(old_bundle.generation).resources.files["static/app.js"] == b"export const version = '1.0.0';"
            old.worker.close()
            assert not old.worker.healthy and new.worker.healthy
            assert first.path != second.path and first.path.is_dir()
        finally:
            new.worker.close()
        reopened = ReportWorker(template.id, python=second.python)
        try:
            assert reopened.render_ui(readiness_arguments("insights")) == b"<p>2.0.0:0</p>"
        finally:
            reopened.close()
    finally:
        old.worker.close()


def parser_wheel(directory, version, *, broken_resources=False):
    source = (
        "from pathlib import Path\n"
        "from whab.plugins import Plugin, PluginContract, FileImport, WorkspaceImport\n"
        "def parse(raw):\n"
        "    if raw != b'fictional archive': raise ValueError('synthetic-private-parser-detail')\n"
        "    return WorkspaceImport(({'id':'wallet','name':'Parser " + version + "','institution':'Fictional',"
        "'type':'other','balance':0,'source':'fictional','stale':True,'balanceUnknown':True,"
        "'onBudget':False,'subtype':None},),(),(),(),'2026-10')\n"
        "root=Path(__file__).parent\n"
        "plugin=Plugin('fictional-reports','Fictional parser',file_import=FileImport(parse,root/'templates',root/'static'),contract=PluginContract(lifecycle='live'))\n"
    )
    files = {"fictional_reports/templates/accounts.html": f'<section data-fixture="{version}">Parser {version}</section>'.encode()}
    if not broken_resources:
        files["fictional_reports/static/import.js"] = f"/* parser {version} */".encode()
    return make_wheel(directory, version, source=source, extra_files=files)


def test_real_parser_upgrades_keep_versioned_assets_reject_stale_previews_and_reopen(tmp_path, local_closure, store, monkeypatch):
    import asyncio
    import re
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from whab_plugin_sqlite.plugin import plugin as sqlite_plugin
    from whab_plugin_web.plugin import plugin as web_plugin

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import (
        FileImport,
        Plugin,
        PluginContract,
        PluginRegistry,
        PluginResources,
        WorkspaceImport,
    )

    initial = WorkspaceImport((), (), (), (), "2026-10")
    template = Plugin("fictional-reports", "Fictional parser", file_import=FileImport(lambda _: initial, resources=PluginResources({
        "templates/accounts.html": b"<section>Host parser</section>", "static/import.js": b"/* host parser */",
    })), contract=PluginContract(lifecycle="live"))
    entries = tuple(SimpleNamespace(name=plugin.id, group="whab.plugins", load=lambda plugin=plugin: plugin)
                    for plugin in (template, sqlite_plugin, web_plugin))
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: entries)
    monkeypatch.setattr("whab.app.installed_entrypoints", lambda: entries)
    configuration = Settings(tmp_path / "runtime", 5173, "sandbox", "", "", "", False, "", "", (template.id,), ui="web")

    def application():
        return create_app(store=store, settings=configuration, plugins=PluginRegistry((template, web_plugin)))

    app = application()

    def upgrade(client, wheel):
        response = client.post("/api/plugins/management/upgrade", json={"pluginId": template.id,
            "wheels": [{"path": str(item.path), "sha256": item.sha256} for item in (*local_closure, approve(wheel))]})
        assert response.status_code == 202
        async def settle():
            await asyncio.gather(*tuple(app.state.live_plugins._tasks), return_exceptions=True)
        client.portal.call(settle)
        return next(row for row in client.get("/api/plugins/runtime").json()["operations"] if row["id"] == response.json()["id"])

    def asset(client, version):
        page = client.get("/budgets/home/accounts/current-month/usd")
        assert page.status_code == 200 and f"Parser {version}" in page.text
        url = re.search(r"/budgets/home/imports/fictional-reports/[0-9a-f]{32}/static/import.js", page.text)[0]
        assert client.get(url).content == f"/* parser {version} */".encode()
        return url

    base = "/api/plugins/fictional-reports/import/"
    headers = {"Content-Type": "application/zip", "X-Import-Currency": "USD"}
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert upgrade(client, parser_wheel(tmp_path, "4.0.0"))["state"] == "complete"
        first = app.state.budget_runtime._active.services["plugins"].workers[template.id]
        old_asset = asset(client, "4.0.0")
        preview = client.post(base + "preview", content=b"fictional archive", headers=headers).json()
        assert preview["canImport"] is True
        before = client.get("/api/workspace?workspace=personal").json()
        assert upgrade(client, parser_wheel(tmp_path, "4.1.0", broken_resources=True))["state"] == "failed"
        assert first.worker.healthy and asset(client, "4.0.0") == old_asset
        assert upgrade(client, parser_wheel(tmp_path, "5.0.0"))["state"] == "complete"
        second = app.state.budget_runtime._active.services["plugins"].workers[template.id]
        assert not first.worker.healthy and second.worker.healthy
        new_asset = asset(client, "5.0.0")
        assert new_asset != old_asset and client.get(old_asset).content == b"/* parser 4.0.0 */"
        old_headers = {**headers, "X-Import-Fingerprint": preview["fingerprint"], "X-Import-Parser": preview["parserIdentity"]}
        assert client.post(base + "commit", content=b"fictional archive", headers=old_headers).status_code == 409
        assert client.post(base + "commit", content=b"fictional archive", headers={**headers, "X-Import-Fingerprint": preview["fingerprint"]}).status_code == 409
        assert client.get("/api/workspace?workspace=personal").json() == before
        fresh = client.post(base + "preview", content=b"fictional archive", headers=headers).json()
        assert fresh["fingerprint"] == preview["fingerprint"] and fresh["parserIdentity"] != preview["parserIdentity"]
        committed_headers = {**headers, "X-Import-Fingerprint": fresh["fingerprint"], "X-Import-Parser": fresh["parserIdentity"]}
        assert client.post(base + "commit", content=b"fictional archive", headers=committed_headers).status_code == 200
        assert client.post(base + "commit", content=b"fictional archive", headers=committed_headers).json()["alreadyImported"] is True
        assert client.get("/api/workspace?workspace=personal").json()["accounts"][0]["name"] == "Parser 5.0.0"
    app = application()
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        asset(client, "5.0.0")
        assert client.get(old_asset).status_code == 404
        assert client.post(base + "commit", content=b"fictional archive", headers=committed_headers).status_code == 409
        fresh = client.post(base + "preview", content=b"fictional archive", headers=headers).json()
        assert fresh["alreadyImported"] is True


def test_real_branding_worker_versions_own_separate_caches_and_reopen(tmp_path, local_closure, store, monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import BankLogos, Plugin, PluginContract, PluginRegistry

    template = Plugin("fictional-reports", "Fictional artwork", bank_logos=BankLogos(lambda _: "fixture", lambda _: None),
                      contract=PluginContract(lifecycle="live"))
    entry = SimpleNamespace(name=template.id, group="whab.plugins", load=lambda: template)
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: (entry,))
    monkeypatch.setattr("whab.app.installed_entrypoints", lambda: (entry,))
    configuration = Settings(tmp_path / "runtime", 5173, "sandbox", "", "", "", False, "", "", (template.id,), ui="")

    def application():
        return create_app(store=store, settings=configuration, plugins=PluginRegistry((template,)))

    def wheel(version):
        source = (
            "from whab.plugins import Plugin, PluginContract, BankLogos, LogoImage, Insight, PluginAction, PluginSettings\n"
            "directory=None\n"
            "def configure(path):\n    global directory\n    directory=path\n    path.mkdir(parents=True,exist_ok=True)\n"
            "def fetch(key):\n    image=LogoImage(bytes.fromhex('89504e470d0a1a0a')+b'" + version + "','image/png')\n    (directory/'artwork.public').write_bytes(image.data)\n    return image\n"
            "def read():\n    return (Insight('Version','" + version + "'),)\n"
            "def flush(action):\n    (directory/'artwork.public').unlink(missing_ok=True)\n    return 'Cleared " + version + "'\n"
            "plugin=Plugin('fictional-reports','Artwork " + version + "',bank_logos=BankLogos(lambda text:'fixture',fetch,configure,lambda domain:'fixture','Fictional','https://bank.example.org'),settings=PluginSettings(read,(PluginAction('flush','Flush'),),flush),contract=PluginContract(lifecycle='live'))\n"
        )
        return make_wheel(tmp_path, version, source=source)

    app = application()
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        def upgrade(version):
            approvals = (*local_closure, approve(wheel(version)))
            async def run():
                return await app.state.live_plugins.upgrade(template.id, approvals)
            client.portal.call(run)
            return app.state.budget_runtime._active.services["plugins"].workers[template.id]

        first = upgrade("6.0.0")
        assert client.get("/api/plugins/fictional-reports/bank-logos/fixture").content.endswith(b"6.0.0")
        cache = configuration.home / "cache/plugins/fictional-reports/environments"
        old = cache / first.environment.identifier / "artwork.public"
        original = old.read_bytes()
        second = upgrade("7.0.0")
        assert not first.worker.healthy and second.worker.healthy
        assert client.get("/api/plugins/fictional-reports/bank-logos/fixture").content.endswith(b"7.0.0")
        current = cache / second.environment.identifier / "artwork.public"
        assert current.is_file() and old.read_bytes() == original
        flushed = client.post("/api/plugins/fictional-reports/settings/actions/flush", json={})
        assert flushed.status_code == 200 and not current.exists() and old.read_bytes() == original
        assert client.get("/api/plugins/fictional-reports/settings").json()["insights"][0]["value"] == "7.0.0"
    app = application()
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert client.get("/api/plugins/fictional-reports/bank-logos/fixture").content.endswith(b"7.0.0")


def make_wheel(directory, version="1.0.0", *, source=None, metadata_suffix="", entrypoint="fictional_reports:plugin", extra_files=None, host_requirement=None):
    name = "whab_plugin_fictional_reports"
    prefix = f"{name}-{version}.dist-info/"
    source = source if source is not None else (
        "from whab.plugins import Plugin, PluginContract, Insight, ExportTable\n"
        f"def insights(snapshot):\n    return (Insight('Version', '{version}'),)\n"
        f"def export(snapshot):\n    return ExportTable(('version',), (('{version}',),))\n"
        "plugin = Plugin('fictional-reports', 'Fictional reports', insights=insights, export=export, contract=PluginContract(lifecycle='live'))\n"
    )
    files = {"fictional_reports/__init__.py": source.encode(),
             prefix + "METADATA": (f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\nRequires-Python: >=3.14\nRequires-Dist: whab{host_requirement or '==' + __version__}\n" + metadata_suffix).encode(),
             prefix + "WHEEL": b"Wheel-Version: 1.0\nGenerator: reviewed-test-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
             prefix + "entry_points.txt": f"[whab.plugins]\nfictional-reports = {entrypoint}\n".encode(),
             **(extra_files or {})}
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for path, raw in files.items():
        writer.writerow((path, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("="), str(len(raw))))
    writer.writerow((prefix + "RECORD", "", ""))
    files[prefix + "RECORD"] = record.getvalue().encode()
    path = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, raw in files.items():
            archive.writestr(filename, raw)
    return path


_LEGACY_REPORT_WORKER = '''
import importlib.metadata as metadata
import sys
from whab import __version__
from whab.plugins import AccountSnapshot, BudgetSnapshot, CategorySnapshot, TransactionSnapshot, load_plugins
from whab.plugins.wire import HEADER, Frame, frame_lengths
from dataclasses import asdict

plugin_id = sys.argv[1]
registry = load_plugins((plugin_id,))
plugin = registry.descriptors()[0]
entry, = (entry for entry in metadata.entry_points(group='whab.plugins') if entry.name == plugin_id)
def send(value):
    sys.stdout.buffer.write(Frame(value).encode())
    sys.stdout.buffer.flush()
send({'kind':'ready','worker':1,'sdk':1,'plugin':plugin_id,'distribution':entry.dist.metadata['Name'],
      'version':entry.dist.version,'host':__version__,'live':True,
      'capabilities':[{'name':'reporting.export','version':1},{'name':'reporting.insights','version':1}],
      'insights':True,'export':True})
while True:
    header = sys.stdin.buffer.read(HEADER.size)
    if not header:
        break
    meta, payload = frame_lengths(header)
    request = Frame.decode(header + sys.stdin.buffer.read(meta + payload)).metadata
    identifier = request['id']
    if request['operation'] == 'shutdown':
        send({'id':identifier,'kind':'closed'})
        break
    value = request['snapshot']
    snapshot = BudgetSnapshot(value['workspace'],value['month'],tuple(AccountSnapshot(**row) for row in value['accounts']),
                              tuple(CategorySnapshot(**row) for row in value['categories']),
                              tuple(TransactionSnapshot(**row) for row in value['transactions']),currency=value['currency'])
    if request['operation'] == 'insights':
        output = [asdict(row) for row in registry.insights(snapshot)[0].insights]
    else:
        table = registry.export(plugin_id,snapshot)
        output = {'columns':list(table.columns),'rows':[list(row) for row in table.rows]}
    send({'id':identifier,'kind':'result','data':output})
'''


def host_version_fixture(directory, approval, version, *, legacy_marker=None):
    """Synthetic negotiation fixture; this is not a historical release artifact."""
    directory.mkdir()
    with zipfile.ZipFile(approval.path) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    metadata, = (name for name in files if name.endswith(".dist-info/METADATA"))
    prefix = metadata.removesuffix("METADATA")
    replacement = "whab-" + version + ".dist-info/"
    files = {name.replace(prefix, replacement, 1) if name.startswith(prefix) else name: raw for name, raw in files.items()}
    files[replacement + "METADATA"] = re.sub(rb"(?m)^Version: [^\n]+", b"Version: " + version.encode(), files[replacement + "METADATA"])
    files["whab/__init__.py"] = re.sub(rb"__version__ = ['\"][^'\"]+['\"]", b'__version__ = "' + version.encode() + b'"', files["whab/__init__.py"])
    if legacy_marker is not None:
        files["whab/plugins/wire.py"] = re.sub(rb"PROTOCOL_VERSION = [0-9]+", b"PROTOCOL_VERSION = 1", files["whab/plugins/wire.py"])
        marker = "from pathlib import Path\nwith Path(" + repr(str(legacy_marker)) + ").open('ab') as marker: marker.write(b'started\\n')\n"
        files["whab/plugins/report_worker.py"] = (marker + _LEGACY_REPORT_WORKER).encode()
    record_name = replacement + "RECORD"
    del files[record_name]
    records = io.StringIO()
    writer = csv.writer(records, lineterminator="\n")
    for name, raw in files.items():
        writer.writerow((name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("="), len(raw)))
    writer.writerow((record_name, "", ""))
    files[record_name] = records.getvalue().encode()
    target = directory / ("whab-" + version + "-py3-none-any.whl")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return approve(target)


def test_synthetic_saved_protocol1_environment_is_fenced_then_explicitly_reprepared_for_current_host(tmp_path, local_closure, monkeypatch):
    from whab.plugins import (
        ExportTable,
        Insight,
        Plugin,
        PluginContract,
        PluginError,
        PluginRegistry,
        report_worker,
        wire,
        worker_selection,
    )

    host, = (wheel for wheel in local_closure if inspect_wheel(wheel).canonical_name == "whab")
    dependencies = tuple(wheel for wheel in local_closure if wheel is not host)
    marker = tmp_path / "legacy-worker-starts"
    legacy_host = host_version_fixture(tmp_path / "legacy-wheel", host, "2.25.0", legacy_marker=marker)
    current_host = host
    report = approve(make_wheel(tmp_path, host_requirement=">=2.24,<3"))
    home = tmp_path / "runtime"
    root = home / "plugin-workers/environments"

    class LegacyProbe(ReportWorker):
        """A report-only protocol-1 client used solely to publish the old fixture."""
        def __init__(self, plugin_id, *, python, timeout, expected_distribution, expected_version):
            self.plugin_id, self.timeout, self._lock, self._retiring = plugin_id, timeout, Lock(), None
            self._process = subprocess.Popen([str(python), "-I", "-B", "-m", "whab.plugins.report_worker", plugin_id],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             env=environments._environment(), start_new_session=True, bufsize=0)
            try:
                self.info = self._read(timeout).metadata
                assert self.info["worker"] == 1 and self.info["distribution"] == expected_distribution and self.info["version"] == expected_version
                # New preparation uses this explicit shim only for constructing
                # a genuine old-format fixture. Restoring later never uses it.
                self.info["adapters"] = {"bank_logos": None, "merchant_logos": False, "file_import": None, "settings": None, "interface": None, "ui": None, "categorization": False}
            except BaseException:
                self._stop()
                raise

    with monkeypatch.context() as previous:
        previous.setattr(environments, "__version__", "2.25.0")
        previous.setattr(report_worker, "PROTOCOL_VERSION", 1)
        previous.setattr(wire, "PROTOCOL_VERSION", 1)
        previous.setattr(environments, "ReportWorker", LegacyProbe)
        old = prepare_environment(root, "fictional-reports", (*dependencies, legacy_host, report))
        worker_selection.save_reference(home, old)
    reference = home / "plugin-workers/active/fictional-reports.json"
    original_reference, original_ready = reference.read_bytes(), (old.path / "ready.json").read_bytes()
    original_installation = environments._installation_hash(old.path)
    starts = marker.read_bytes()
    revision = worker_selection.worker_reference_revision(home, "fictional-reports")

    with monkeypatch.context() as refused:
        refused.setattr(environments, "_run", lambda *_: pytest.fail("An incompatible old interpreter must never execute"))
        with pytest.raises(PluginError) as raised:
            worker_selection.reopen_environment(root, "fictional-reports", old.identifier)
        assert raised.value.code == "worker_environment_unavailable"
    assert reference.read_bytes() == original_reference and marker.read_bytes() == starts

    approvals = (*dependencies, current_host, report)
    original_publish = worker_selection._save_reference

    def lost_publish_reply(home, prepared):
        original_publish(home, prepared)
        raise OSError("fictional lost response after durable replacement")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(worker_selection, "_save_reference", lost_publish_reply)
        with pytest.raises(PluginError) as raised:
            worker_selection.replace_saved_worker(home, "fictional-reports", approvals, expected_reference_sha256=revision)
        assert raised.value.code == "worker_replacement_failed" and "fictional" not in str(raised.value)
    assert reference.read_bytes() != original_reference
    # The original expected revision remains the retry identity after a lost
    # reply; matching history and the already-published decision prove the result.
    repaired = worker_selection.replace_saved_worker(home, "fictional-reports", approvals, expected_reference_sha256=revision)
    assert repaired.identifier != old.identifier and repaired.version == old.version == "1.0.0"
    assert (home / "plugin-workers/history/fictional-reports" / (revision + ".json")).read_bytes() == original_reference
    assert reference.read_bytes() != original_reference
    assert (old.path / "ready.json").read_bytes() == original_ready
    assert environments._installation_hash(old.path) == original_installation and marker.read_bytes() == starts
    # A lost successful response can retry the original expected revision; it
    # returns the same new durable decision without rewriting the old version.
    assert worker_selection.replace_saved_worker(home, "fictional-reports", approvals, expected_reference_sha256=revision) == repaired
    template = Plugin("fictional-reports", "Fictional reports", insights=lambda _: (Insight("Version", "host"),),
                      export=lambda _: ExportTable(("version",), (("host",),)), contract=PluginContract(lifecycle="live"))
    registry, bindings = worker_selection.restore_workers(home, PluginRegistry((template,)))
    try:
        assert registry.insights(BudgetSnapshot("personal", "2000-01", (), (), ()))[0].insights[0].value == "1.0.0"
    finally:
        for binding in bindings.values():
            binding.worker.close()


def test_static_inspection_exposes_identity_requirements_and_hash_without_importing(tmp_path):
    wheel = make_wheel(tmp_path, source="raise RuntimeError('must not execute during inspection')\n")
    record = inspect_wheel(approve(wheel))
    assert record.distribution == "whab_plugin_fictional_reports"
    assert record.canonical_name == "whab-plugin-fictional-reports"
    assert record.version == "1.0.0" and record.sha256 == approve(wheel).sha256
    assert record.requires_python == ">=3.14"
    assert record.requirements == (f"whab=={__version__}",)
    assert record.entry_points == (("whab.plugins", "fictional-reports", "fictional_reports:plugin"),)


def test_explicit_approval_hash_is_required_and_modified_wheels_are_rejected(tmp_path):
    path = make_wheel(tmp_path)
    approved = approve(path)
    with pytest.raises(ValueError):
        ApprovedWheel(path, "not-an-approval")
    with pytest.raises(ValueError):
        inspect_wheel(path)
    with path.open("ab") as target:
        target.write(b"changed")
    with pytest.raises(EnvironmentPreparationError) as raised:
        inspect_wheel(approved)
    assert raised.value.code == "wheel_hash_mismatch"


@pytest.mark.parametrize("name", ["../escape.py", "/absolute.py", "a/../../escape.py", "a\\escape.py", "a:escape.py", "a/./escape.py", "a//escape.py", "a/" * 40 + "file.py", "FICTIONAL_REPORTS/__INIT__.PY"])
def test_wheel_paths_cannot_escape_installation_or_alias_namespaces(tmp_path, name):
    path = make_wheel(tmp_path, extra_files={name: b"unexecuted"})
    with pytest.raises(EnvironmentPreparationError) as raised:
        inspect_wheel(approve(path))
    assert raised.value.code == "invalid_wheel"


@pytest.mark.parametrize("metadata_suffix", ["Name: duplicate\n", "Version: 2.0.0\n", "Requires-Python: >=3.14\n",
                                           "Requires-Dist: arbitrary @ https://invalid.example/plugin.whl\n"])
def test_duplicate_identity_python_fields_and_remote_dependency_references_are_rejected(tmp_path, metadata_suffix):
    path = make_wheel(tmp_path, metadata_suffix=metadata_suffix)
    with pytest.raises(EnvironmentPreparationError) as raised:
        inspect_wheel(approve(path))
    assert raised.value.code == "invalid_wheel"


def test_incompatible_python_and_unsafe_entrypoints_fail_statically(tmp_path):
    path = make_wheel(tmp_path, entrypoint="not a valid entrypoint")
    with pytest.raises(EnvironmentPreparationError) as raised:
        inspect_wheel(approve(path))
    assert raised.value.code == "invalid_wheel"
    path = make_wheel(tmp_path)
    with zipfile.ZipFile(path) as archive:
        files = {info.filename: archive.read(info) for info in archive.infolist()}
    metadata_name = next(name for name in files if name.endswith("/METADATA"))
    files[metadata_name] = files[metadata_name].replace(b">=3.14", b">=99")
    with zipfile.ZipFile(path, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    with pytest.raises(EnvironmentPreparationError) as raised:
        inspect_wheel(approve(path))
    assert raised.value.code == "incompatible_python"


def test_symlinks_duplicate_entries_record_tampering_and_size_bounds_are_rejected(tmp_path, monkeypatch):
    path = make_wheel(tmp_path)
    link = tmp_path / "alias.whl"
    link.symlink_to(path)
    with pytest.raises(EnvironmentPreparationError):
        inspect_wheel(ApprovedWheel(link, approve(path).sha256))
    with zipfile.ZipFile(path, "a") as archive:
        information = zipfile.ZipInfo("symlink")
        information.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(information, "fictional_reports")
    with pytest.raises(EnvironmentPreparationError):
        inspect_wheel(approve(path))
    path = make_wheel(tmp_path)
    with zipfile.ZipFile(path, "a") as archive:
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("fictional_reports/__init__.py", "changed code")
    with pytest.raises(EnvironmentPreparationError):
        inspect_wheel(approve(path))
    path = make_wheel(tmp_path)
    monkeypatch.setattr(environments, "MAX_WHEEL_BYTES", path.stat().st_size - 1)
    with pytest.raises(EnvironmentPreparationError):
        inspect_wheel(approve(path))


@pytest.fixture(scope="module")
def local_closure(tmp_path_factory):
    """Build reviewed host code; download only its declared runtime closure."""
    base = tmp_path_factory.mktemp("approved-wheelhouse")
    root = Path(__file__).resolve().parents[2]
    source = base / "host-source"
    source.mkdir()
    shutil.copytree(root / "src", source / "src", ignore=shutil.ignore_patterns("*.pyc", "__pycache__", "*.egg-info"))
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(root / name, source / name)
    wheelhouse = base / "wheels"
    subprocess.run([sys.executable, "-I", "-m", "pip", "wheel", "--disable-pip-version-check", "--no-deps", "--wheel-dir", str(wheelhouse), str(source)],
                   cwd=base, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    host, = wheelhouse.glob("whab-*.whl")
    subprocess.run([sys.executable, "-I", "-m", "pip", "download", "--disable-pip-version-check", "--only-binary=:all:", "--dest", str(wheelhouse), str(host)],
                   cwd=base, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    return tuple(approve(path) for path in sorted(wheelhouse.glob("*.whl")))


def test_real_versions_coexist_failed_candidate_preserves_active_worker_and_reuse_detects_tampering(tmp_path, local_closure, monkeypatch):
    root = tmp_path / "environments"
    first_wheel = approve(make_wheel(tmp_path, "1.0.0"))
    first = prepare_environment(root, "fictional-reports", (*local_closure, first_wheel))
    snapshot = BudgetSnapshot("personal", "2026-10", (), (), ())
    worker = ReportWorker(first.plugin_id, python=first.python, expected_distribution=first.distribution, expected_version=first.version)
    ready_before = (first.path / "ready.json").read_bytes()
    code, = first.path.glob("environment/lib/python*/site-packages/fictional_reports/__init__.py")
    code_before = code.read_bytes()
    try:
        assert worker.insights(snapshot)[0].value == "1.0.0"
        assert worker.export(snapshot).rows == (("1.0.0",),)
        broken = approve(make_wheel(tmp_path, "3.0.0", source="raise RuntimeError('synthetic-private-startup-detail')\n"))
        with pytest.raises(EnvironmentPreparationError) as raised:
            prepare_environment(root, "fictional-reports", (*local_closure, broken))
        assert raised.value.code == "environment_failed" and raised.value.plugin_id == "fictional-reports"
        assert "synthetic-private" not in str(raised.value)
        assert worker.insights(snapshot)[0].value == "1.0.0"
        assert len(tuple(root.glob("*/ready.json"))) == 1
        second_wheel = approve(make_wheel(tmp_path, "2.0.0"))
        second = prepare_environment(root, "fictional-reports", (*local_closure, second_wheel))
        assert first.identifier != second.identifier and first.python != second.python
        replacement = ReportWorker(second.plugin_id, python=second.python, expected_distribution=second.distribution, expected_version=second.version)
        try:
            assert replacement.insights(snapshot)[0].value == "2.0.0"
            assert worker.insights(snapshot)[0].value == "1.0.0"
        finally:
            replacement.close()
        assert code.read_bytes() == code_before
        assert (first.path / "ready.json").read_bytes() == ready_before
        reused = prepare_environment(root, "fictional-reports", tuple(reversed((*local_closure, first_wheel))))
        assert reused.identifier == first.identifier
        manifest = json.loads(ready_before)
        assert {item["sha256"] for item in manifest["wheels"]} == {wheel.sha256 for wheel in (*local_closure, first_wheel)}
        assert manifest["plugin"] == "fictional-reports"
        assert "synthetic-private" not in ready_before.decode()
    finally:
        worker.close()
    unexpected = code.parent.parent / "unapproved.pth"
    unexpected.write_text("raise RuntimeError('must not execute unapproved additions')\n")
    with monkeypatch.context() as changes:
        changes.setattr(environments, "_run", lambda *_: pytest.fail("Detect changes before executing the reused environment"))
        with pytest.raises(EnvironmentPreparationError) as raised:
            prepare_environment(root, "fictional-reports", (*local_closure, first_wheel))
        assert raised.value.code == "environment_changed"
    assert unexpected.exists()
    unexpected.unlink()
    (first.path / "ready.json").unlink()
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, first_wheel))
    assert raised.value.code == "environment_changed" and code.read_bytes() == code_before
    (first.path / "ready.json").write_bytes(ready_before)
    code.write_text("changed after approval\n")
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, first_wheel))
    assert raised.value.code == "environment_changed"
    assert code.read_text() == "changed after approval\n"
    assert (first.path / "ready.json").read_bytes() == ready_before


def test_concurrent_preparation_builds_identical_content_only_once(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path, "4.0.0"))
    barrier = threading.Barrier(2)
    original_lock, original_run = environments._lock, environments._run
    builds = []

    @contextmanager
    def synchronized_lock(path, deadline):
        barrier.wait(timeout=30)
        with original_lock(path, deadline):
            yield

    def observe_run(command, deadline):
        if command[1:4] == ["-I", "-m", "venv"]:
            builds.append(command)
        return original_run(command, deadline)

    monkeypatch.setattr(environments, "_lock", synchronized_lock)
    monkeypatch.setattr(environments, "_run", observe_run)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(prepare_environment, tmp_path / "environments", "fictional-reports", (*local_closure, candidate)) for _ in range(2)]
        first, second = (future.result(timeout=120) for future in futures)
    assert first.identifier == second.identifier and len(builds) == 1


@pytest.mark.parametrize("declaration", ["", ", contract=PluginContract(lifecycle='live'), settings=PluginSettings(lambda: {})"])
def test_preparation_refuses_legacy_and_stateful_report_lifecycle_without_publication(tmp_path, local_closure, declaration):
    source = ("from whab.plugins import Plugin, PluginContract, PluginSettings, Insight\n"
              "def insights(snapshot): return (Insight('Fictional','1'),)\n"
              "plugin=Plugin('fictional-reports','Fictional',insights=insights" + declaration + ")\n")
    candidate = approve(make_wheel(tmp_path, source=source))
    root = tmp_path / "environments"
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, candidate))
    assert raised.value.code == "unsupported_lifecycle"
    assert [path.name for path in root.iterdir()] == ["locks"]


def test_source_change_after_approval_is_rejected_after_private_copy(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path, "5.0.0"))
    original_copy = environments.shutil.copyfile

    def changed_copy(source, target, *args, **kwargs):
        result = original_copy(source, target, *args, **kwargs)
        if Path(source) == candidate.path:
            with Path(target).open("ab") as output:
                output.write(b"changed during copy")
        return result

    monkeypatch.setattr(environments.shutil, "copyfile", changed_copy)
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(tmp_path / "environments", "fictional-reports", (*local_closure, candidate))
    assert raised.value.code == "wheel_hash_mismatch"
    assert not tuple((tmp_path / "environments").glob("*/ready.json"))


def test_missing_dependencies_and_extra_runtime_plugins_fail_before_installation(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path))
    monkeypatch.setattr(environments, "_run", lambda *_: pytest.fail("Invalid closure must fail before executable installation"))
    for closure in ((candidate,), (*local_closure, candidate, candidate), (*local_closure,)):
        with pytest.raises(EnvironmentPreparationError) as raised:
            prepare_environment(tmp_path / "environments", "fictional-reports", closure)
        assert raised.value.code in {"invalid_closure", "incompatible_host"}


def test_preparation_failure_removes_partial_environment_and_sanitizes_subprocess_output(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path))

    def fail(*_):
        raise subprocess.CalledProcessError(7, ["synthetic-private-command"], output="synthetic-private-output")

    monkeypatch.setattr(environments, "_run", fail)
    root = tmp_path / "environments"
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, candidate))
    assert raised.value.code == "environment_failed"
    assert "synthetic-private" not in str(raised.value)
    assert [path.name for path in root.iterdir()] == ["locks"]


def test_preparation_cancellation_removes_partial_candidate_and_preserves_cancellation(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path))
    cancellation = KeyboardInterrupt("fictional cancellation")

    def cancel(*_):
        raise cancellation

    monkeypatch.setattr(environments, "_run", cancel)
    root = tmp_path / "environments"
    with pytest.raises(KeyboardInterrupt) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, candidate))
    assert raised.value is cancellation
    assert [path.name for path in root.iterdir()] == ["locks"]


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "success"])
def test_installer_completion_timeout_and_cancellation_stop_only_the_owned_process_group(tmp_path, monkeypatch, outcome):
    cancel = outcome == "cancel"
    marker = tmp_path / "fictional-child.pid"
    child_program = "import os,pathlib,signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    installer = ("import pathlib,subprocess,sys,time\n"
                 "subprocess.Popen([sys.executable,'-I','-c',sys.argv[1],sys.argv[2]],"
                 "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                 "deadline=time.monotonic()+5\n"
                 "while not pathlib.Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
                 + ("" if outcome == "success" else "time.sleep(60)\n"))
    command = [sys.executable, "-I", "-c", installer, child_program, str(marker)]
    original = subprocess.Popen
    processes = []
    cancellation = KeyboardInterrupt("fictional cancellation")

    def spawn(arguments, **kwargs):
        assert kwargs["start_new_session"] is True
        process = original(arguments, **kwargs)
        processes.append(process)
        if cancel:
            original_wait = process.wait
            pending = [True]

            def wait(*, timeout):
                if pending:
                    pending.clear()
                    deadline = time.monotonic() + 5
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert marker.exists()
                    raise cancellation
                return original_wait(timeout=timeout)

            process.wait = wait
        return process

    def running(pid):
        with original(["ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as check:
            state = check.communicate(timeout=2)[0].strip()
        return bool(state) and not state.startswith(b"Z")

    monkeypatch.setattr(environments.subprocess, "Popen", spawn)
    pid = None
    try:
        if outcome == "success":
            environments._run(command, time.monotonic() + 5)
        else:
            with pytest.raises(KeyboardInterrupt if cancel else subprocess.TimeoutExpired) as raised:
                environments._run(command, time.monotonic() + (5 if cancel else 0.3))
            if cancel:
                assert raised.value is cancellation
        process, = processes
        pid = int(marker.read_text())
        deadline = time.monotonic() + 2
        while running(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not running(pid)
        assert process.returncode is not None
        assert process.pid != os.getpgrp()
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
        if pid is not None and running(pid):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("cancel", [False, True])
def test_installer_cleanup_failure_is_authored_and_does_not_replace_cancellation(monkeypatch, cancel):
    failure = KeyboardInterrupt("fictional cancellation") if cancel else subprocess.TimeoutExpired("fictional", 1)

    def fail_wait(*_, **__):
        raise failure

    process = type("FictionalProcess", (), {"wait": fail_wait})()
    monkeypatch.setattr(environments.subprocess, "Popen", lambda *_, **__: process)

    def fail_cleanup(_):
        raise OSError("synthetic-private-cleanup")

    monkeypatch.setattr(environments, "_stop_process_group", fail_cleanup)
    with pytest.raises(KeyboardInterrupt if cancel else EnvironmentPreparationError) as raised:
        environments._run(["fictional"], time.monotonic() + 1)
    if cancel:
        assert raised.value is failure
    else:
        assert raised.value.code == "environment_cleanup_failed"
        assert "synthetic-private" not in str(raised.value)


def test_expired_preparation_budget_stops_before_installation(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path))
    monkeypatch.setattr(environments, "_run", lambda *_: pytest.fail("An expired preparation cannot install code"))
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(tmp_path / "environments", "fictional-reports", (*local_closure, candidate), timeout=1e-9)
    assert raised.value.code == "environment_timeout"
    assert not tuple((tmp_path / "environments").glob("*/ready.json"))


def test_durability_failure_cannot_publish_a_usable_environment(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path, "6.0.0"))
    original = environments._installation_hash

    def fail_durability(path, *, durable=False):
        assert not (path / "ready.json").exists()
        if durable:
            raise OSError("synthetic-private-filesystem-detail")
        return original(path)

    monkeypatch.setattr(environments, "_installation_hash", fail_durability)
    root = tmp_path / "environments"
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, candidate))
    assert raised.value.code == "environment_failed"
    assert "synthetic-private" not in str(raised.value)
    assert [path.name for path in root.iterdir()] == ["locks"]


def test_root_filesystem_failures_are_sanitized_before_any_install(tmp_path, monkeypatch):
    candidate = approve(make_wheel(tmp_path))
    root = tmp_path / "environments"
    original = Path.mkdir

    def fail(path, *args, **kwargs):
        if path == root:
            raise OSError("synthetic-private-directory-detail")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail)
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (candidate,))
    assert raised.value.code == "environment_failed" and "synthetic-private" not in str(raised.value)


def test_real_api_worker_upgrade_failed_preparation_disable_reenable_and_reopen(tmp_path, local_closure, store, monkeypatch):
    """Exact installed workers only declare protocol data; host retains the ledger."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import (
        Interface,
        Plugin,
        PluginContract,
        PluginError,
        PluginRegistry,
        RemoteLedgerInterface,
    )

    identifier = "fictional-reports"
    token = "fictional-api-worker-secret-" * 3
    monkeypatch.setenv("FICTIONAL_REPORTS_CREDENTIAL", token)
    template = Plugin(identifier, "Fictional API", interface=Interface(adapter=RemoteLedgerInterface(token_setting="FICTIONAL_REPORTS_CREDENTIAL")), contract=PluginContract(lifecycle="live"))
    entry = SimpleNamespace(name=identifier, group="whab.plugins", load=lambda: template)
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: (entry,))
    monkeypatch.setattr("whab.app.installed_entrypoints", lambda: (entry,))
    configuration = Settings(tmp_path / "runtime", 5173, "sandbox", "", "", "", False, "", "", (identifier,), ui="")

    def wheel(version, *, commands=True, setting="FICTIONAL_REPORTS_CREDENTIAL"):
        source = (
            "import os\n"
            "from whab.plugins import Interface, Plugin, PluginContract, RemoteLedgerInterface\n"
            "assert 'FICTIONAL_REPORTS_CREDENTIAL' not in os.environ\n"
            f"plugin=Plugin({identifier!r},'Fictional API',interface=Interface(adapter=RemoteLedgerInterface(token_setting={setting!r},commands={commands!r})),contract=PluginContract(lifecycle='live'))\n"
        )
        return make_wheel(tmp_path, version, source=source)

    def application():
        return create_app(store=store, settings=configuration, plugins=PluginRegistry((template,)))

    app = application()
    first = second = None
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        path = f"/remote/v1/budgets/{app.state.ledger_service.budget_id}"
        headers = {"Authorization": "Bearer " + token}
        ledger = app.state.ledger_service
        body = {"operation": "add_account", "workspace": "personal", "payload": {"name": "Fictional wallet", "type": "depository", "balance": 125}, "expected_revision": ledger.revision, "request_id": "stable-retry"}
        original = client.post(path + "/commands", json=body, headers=headers)
        assert original.status_code == 200

        def upgrade(version, **kwargs):
            approvals = (*local_closure, approve(wheel(version, **kwargs)))
            return client.portal.call(app.state.live_plugins.upgrade, identifier, approvals)

        assert upgrade("1.0.0") == 2
        first = app.state.budget_runtime._active.services["plugins"].workers[identifier]
        original_binding = app.state.budget_runtime._active.services["interfaces"].binding
        assert first.worker.info["version"] == "1.0.0" and first.worker.interface().commands
        assert client.post(path + "/commands", json=body, headers=headers).json() == original.json()
        with pytest.raises(PluginError) as failed:
            upgrade("3.0.0", setting="UNCONFIGURED_TOKEN")
        assert failed.value.plugin_id == identifier
        assert first.worker.healthy and not original_binding._closed
        assert app.state.budget_runtime.status()["generation"] == 2
        assert client.get(path + "/queries/accounts?workspace=personal", headers=headers).status_code == 200
        entered, finish = Event(), Event()
        original_query = original_binding.query

        def held_query(*args):
            entered.set()
            assert finish.wait(30)
            return original_query(*args)

        original_binding.query = held_query
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(client.get, path + "/queries/accounts?workspace=personal", headers=headers)
            assert entered.wait(5)
            approvals = (*local_closure, approve(wheel("2.0.0", commands=False)))
            change = client.portal.start_task_soon(app.state.live_plugins.upgrade, identifier, approvals)
            client.portal.call(app.state.budget_runtime.wait_for_phase, "draining")
            assert first.worker.healthy and not original_binding._closed and not change.done()
            finish.set()
            assert request.result(timeout=5).status_code == 200
            assert change.result(timeout=10) == 3
        second = app.state.budget_runtime._active.services["plugins"].workers[identifier]
        assert not first.worker.healthy and original_binding._closed
        assert second.worker.info["version"] == "2.0.0" and not second.worker.interface().commands
        assert client.post(path + "/commands", json=body, headers=headers).status_code == 405
        assert app.state.ledger_service is ledger and len(store.accounts("personal")) == 1
        client.portal.call(app.state.live_plugins.activate, ())
        assert not second.worker.healthy
        assert client.get(path + "/queries/accounts?workspace=personal", headers=headers).status_code == 404
        client.portal.call(app.state.live_plugins.activate, (identifier,))
        assert client.post(path + "/commands", json=body, headers=headers).status_code == 405
        assert token not in repr(app.state.budget_runtime._active.services["plugins"].workers[identifier].worker.info)
        assert "fictional_reports" not in sys.modules
    app = application()
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert client.get(path + "/queries/accounts?workspace=personal", headers=headers).status_code == 200
        assert client.post(path + "/commands", json=body, headers=headers).status_code == 405
        assert app.state.budget_runtime._active.services["plugins"].workers[identifier].worker.info["version"] == "2.0.0"
        assert len(store.accounts("personal")) == 1
    assert first and second and not first.worker.healthy and not second.worker.healthy


_CATEGORY_SOURCE = '''
import json
from uuid import uuid4
from whab.plugins import Plugin, PluginContract, Categorization
VERSION = VERSION_LITERAL
class Engine:
    defaults = {'trees': 1}
    def __init__(self, context):
        self.context = context
    def index(self):
        raw = self.context.read_file('models.json')
        if raw and VERSION == '3.0.0':
            raise RuntimeError('synthetic-private-model-readiness-detail')
        result = json.loads(raw) if raw else {'active': None, 'models': []}
        return {**result, 'workerVersion': VERSION}
    def put(self, index):
        self.context.write_file('models.json', json.dumps(index).encode())
    def train(self, rows, parameters):
        yield {'stage': 'Training fictional model ' + VERSION, 'progress': 50}
        return {'id': str(uuid4()), 'version': VERSION}
    def save(self, model, name):
        index = self.index()
        self.context.write_file('models/' + model['id'] + '.bin', ('model ' + VERSION).encode())
        record = {**model, 'name': name}
        self.put({**index, 'models': [*index['models'], record]})
        self.context.save_state({'trainedBy': VERSION})
        return record
    def select(self, identifier):
        self.put({**self.index(), 'active': identifier})
    def delete(self, identifier):
        index = self.index()
        self.put({**index, 'active': None, 'models': [row for row in index['models'] if row['id'] != identifier]})
        self.context.delete_file('models/' + identifier + '.bin')
    def rename(self, identifier, name):
        index = self.index()
        self.put({**index, 'models': [{**row, 'name': name} if row['id'] == identifier else row for row in index['models']]})
    def predict(self, rows):
        return []
    def close(self):
        pass
plugin = Plugin('fictional-reports', 'Fictional categorizer', categorization=Categorization(Engine),
                contract=PluginContract(lifecycle='live'))
'''


def test_real_category_versions_recover_complete_effects_preserve_training_and_reopen(tmp_path, local_closure, store, monkeypatch):
    """Actual distinct approved code artifacts; no parent monkeypatch crosses IPC."""
    import asyncio
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import PluginRegistry
    from whab.plugins.categorization_worker import WorkerCategorizer
    from whab.services.categorization import local_context, local_files

    namespace = {}
    exec(_CATEGORY_SOURCE.replace('VERSION_LITERAL', repr('host')), namespace)
    template = namespace['plugin']
    entry = SimpleNamespace(name=template.id, group='whab.plugins', load=lambda: template)
    monkeypatch.setattr('whab.plugins.management.installed_entrypoints', lambda: (entry,))
    monkeypatch.setattr('whab.app.installed_entrypoints', lambda: (entry,))
    settings = Settings(tmp_path / 'runtime', 5173, 'sandbox', '', '', '', False, '', '', (template.id,), ui='')
    home = settings.home / 'data/plugin-state/sandbox'
    local_context(home, 'default', template.id).write_file('unknown.bin', b'preserve unknown synthetic bytes')
    app = create_app(store=store, settings=settings, plugins=PluginRegistry((template,)))
    ledger = app.state.ledger_service

    def settle(client):
        async def wait():
            if app.state.live_plugins._tasks:
                await asyncio.gather(*tuple(app.state.live_plugins._tasks), return_exceptions=True)
        client.portal.call(wait)

    def upgrade(client, version):
        wheel = make_wheel(tmp_path, version, source=_CATEGORY_SOURCE.replace('VERSION_LITERAL', repr(version)))
        response = client.post('/api/plugins/management/upgrade', json={
            'pluginId': template.id,
            'wheels': [{'path': str(item.path), 'sha256': item.sha256} for item in (*local_closure, approve(wheel))]})
        assert response.status_code == 202, response.text
        settle(client)
        return next(row for row in client.get('/api/plugins/runtime').json()['operations'] if row['id'] == response.json()['id'])

    def action(client, operation, workspace='personal', **values):
        response = client.post('/api/categorization', json={'workspace': workspace,
            'payload': {'pluginId': template.id, 'action': operation, **values}})
        assert response.status_code == 200, response.text
        return response.json()

    with TestClient(app, base_url='https://127.0.0.1:5173') as client:
        account = ledger.add_account('personal', {'name': 'Fictional stable ledger', 'type': 'depository', 'balance': 321})
        revision = ledger.revision
        assert upgrade(client, '1.0.0')['state'] == 'complete'
        assert ledger.revision == revision
        first = app.state.budget_runtime._active.services['plugins'].workers[template.id]
        engine = ledger.categorization.engines[template.id]
        assert isinstance(engine, WorkerCategorizer) and first.worker.healthy
        assert action(client, 'status')['workerVersion'] == '1.0.0'
        run = action(client, 'train')
        assert action(client, 'step', session=run['session'])['progress'] == 50
        deferred = upgrade(client, '2.0.0')
        assert deferred['state'] == 'failed' and deferred['error']['code'] == 'continuation_busy'
        assert first.worker.healthy
        trained = action(client, 'step', session=run['session'])['model']
        action(client, 'select', modelId=trained['id'])
        # Demo has a separate child engine and own model files.
        demo = action(client, 'train', workspace='demo')
        action(client, 'step', workspace='demo', session=demo['session'])
        demo_model = action(client, 'step', workspace='demo', session=demo['session'])['model']
        action(client, 'select', workspace='demo', modelId=demo_model['id'])
        native = local_files(home, 'default', template.id)
        assert native['unknown.bin'] == b'preserve unknown synthetic bytes'
        assert native['models/' + trained['id'] + '.bin'] == b'model 1.0.0'
        assert native['demo/models/' + demo_model['id'] + '.bin'] == b'model 1.0.0'
        # Cancel an actually advanced child iterator before changing code.
        cancelled = action(client, 'train')
        action(client, 'step', session=cancelled['session'])
        assert action(client, 'cancel', session=cancelled['session']) == {'canceled': True}
        original_first = environments._installation_hash(first.environment.path)
        assert upgrade(client, '3.0.0')['state'] == 'failed'
        assert first.worker.healthy and action(client, 'status')['active'] == trained['id']
        revision = ledger.revision
        assert upgrade(client, '2.0.0')['state'] == 'complete'
        assert app.state.ledger_service is ledger and ledger.revision == revision
        second = app.state.budget_runtime._active.services['plugins'].workers[template.id]
        assert second.worker.healthy and not first.worker.healthy and engine.closed
        assert environments._installation_hash(first.environment.path) == original_first
        assert action(client, 'status')['workerVersion'] == '2.0.0'
        assert action(client, 'status')['active'] == trained['id']
        assert action(client, 'status', workspace='demo')['active'] == demo_model['id']
        action(client, 'rename', modelId=trained['id'], name='Renamed after upgrade')
        assert ledger.accounts('personal')[0]['id'] == account
        assert 'fictional_reports' not in sys.modules
    # A new host restores the durable v2 environment and recovers the full native
    # model/index/state from its journal before starting either child engine.
    native_root = home / 'plugin-data' / template.id
    (native_root / 'models.json').unlink()
    app = create_app(store=store, settings=settings, plugins=PluginRegistry((template,)))
    ledger = app.state.ledger_service
    with TestClient(app, base_url='https://127.0.0.1:5173') as client:
        status = action(client, 'status')
        assert status['workerVersion'] == '2.0.0' and status['active'] == trained['id']
        assert status['models'][0]['name'] == 'Renamed after upgrade'
        assert local_context(home, 'default', template.id).state == {'trainedBy': '1.0.0'}
        assert ledger.accounts('personal')[0]['id'] == account
        assert action(client, 'status', workspace='demo')['active'] == demo_model['id']

        # A real process death publishes no additional namespace decision. There
        # is no automatic child restart; reopening validates the same v2 artifact.
        active = ledger.categorization.engines[template.id]
        manager = active.factory.effects
        before_crash = manager.journal.store.path.read_bytes()
        child = active.factory.worker._process
        child.kill()
        assert child.wait(timeout=5) == -signal.SIGKILL
        failed = client.post('/api/categorization', json={'workspace': 'personal',
            'payload': {'pluginId': template.id, 'action': 'status'}})
        assert failed.status_code == 400 and 'synthetic-private' not in failed.text
        assert manager.journal.store.path.read_bytes() == before_crash
        assert not active.factory.worker.healthy
    app = create_app(store=store, settings=settings, plugins=PluginRegistry((template,)))
    ledger = app.state.ledger_service
    with TestClient(app, base_url='https://127.0.0.1:5173') as client:
        assert action(client, 'status')['active'] == trained['id']
        assert action(client, 'status')['workerVersion'] == '2.0.0'
