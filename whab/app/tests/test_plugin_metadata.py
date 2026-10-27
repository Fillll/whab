"""Static source metadata never executes entry-point modules or their imports."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest


@pytest.fixture
def metadata_tool(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/plugin_metadata.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("fictional_static_metadata_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def static_repository(tmp_path):
    source = Path(__file__).resolve().parents[1]
    root = tmp_path / "fictional-repository"
    app = root / "whab/app"
    app.mkdir(parents=True)
    (app / "pyproject.toml").write_text('[project.optional-dependencies]\nbundled=["whab_plugin_fictional>=1,<2"]\n')
    sdk = app / "src/whab/plugins"
    sdk.mkdir(parents=True)
    shutil.copy2(source / "src/whab/plugins/contracts.py", sdk / "contracts.py")
    (sdk.parent / "__init__.py").write_text('__version__ = "2.25.0"\n')
    project = app / "plugins/insight/fictional"
    module = project / "src/whab_plugin_fictional"
    module.mkdir(parents=True)
    (project / "README.md").write_text("# Fictional\n")
    (project / "pyproject.toml").write_text('''[project]
name="whab_plugin_fictional"
version="1.0.0"
requires-python=">=3.14"
dependencies=["whab>=2.22,<3"]
[project.entry-points."whab.plugins"]
fictional="whab_plugin_fictional:plugin"
[tool.whab.plugin]
api-version=1
capabilities=[{name="reporting.insights",version=1,composition="additive"}]
requires=[]
conflicts=[]
lifecycle="restart"
legacy=true
authority="unknown"
state-version=1
state-upgrades=[]
support="maintained"
''')
    (module / "__init__.py").write_text("plugin=Plugin('fictional','Fictional',insights=insights)\n")
    (module / "plugin.json").write_text(json.dumps({"schema_version": 1, "id": "fictional", "category": "insight", "dependencies": []}))
    (app / "plugins/catalog.toml").write_text('''verification=[]
[publisher]
maintainer="Fillll"
repository="https://github.com/Fillll/whab"
documentation_ref="b4c555cc437357a833a858d37d46938f93e6a48f"
security="https://github.com/Fillll/whab/security/advisories/new"
security_policy="https://github.com/Fillll/whab/security/policy"
security_verified_date="2026-10-23"
support_policy="Fictional test policy"
[marketplace]
source_ref="b4c555cc437357a833a858d37d46938f93e6a48f"
''')
    (root / "whab/docs").mkdir()
    return root, project, module


@pytest.mark.parametrize("poison", ["raise", "touch"])
def test_inventory_and_generation_never_execute_plugin_top_level(metadata_tool, static_repository, tmp_path, poison):
    root, _, module = static_repository
    marker = tmp_path / "must-not-exist"
    injected = "raise RuntimeError('fictional top-level execution')\n" if poison == "raise" else f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    (module / "__init__.py").write_text(injected + (module / "__init__.py").read_text())
    before = dict(sys.modules)
    assert metadata_tool.source_inventory(root)[0]["id"] == "fictional"
    outputs = metadata_tool.generated_outputs(root)
    assert len(outputs) == 3 and all("fictional" in text for text in outputs.values())
    assert not marker.exists()
    assert "whab_plugin_fictional" not in sys.modules
    assert before == sys.modules


def test_static_declaration_drift_and_generated_version_drift_fail(metadata_tool, static_repository):
    root, project, _ = static_repository
    for path, text in metadata_tool.generated_outputs(root).items():
        path.write_text(text)
    assert metadata_tool.validate_generated(root) == []
    metadata = project / "pyproject.toml"
    metadata.write_text(metadata.read_text().replace('version="1.0.0"', 'version="1.0.1"'))
    assert len(metadata_tool.validate_generated(root)) == 2
    metadata.write_text(metadata.read_text().replace('lifecycle="restart"', 'lifecycle="live"'))
    with pytest.raises(ValueError, match="declarations differ"):
        metadata_tool.source_inventory(root)


def test_opaque_dynamic_descriptor_is_refused_without_evaluation(metadata_tool, static_repository, tmp_path):
    root, _, module = static_repository
    marker = tmp_path / "must-not-exist"
    (module / "__init__.py").write_text(f"plugin = eval(\"__import__('pathlib').Path({str(marker)!r}).touch()\")\n")
    with pytest.raises(ValueError, match="explicit supported descriptor"):
        metadata_tool.source_inventory(root)
    assert not marker.exists()


def test_explicit_contract_requirements_conflicts_and_default_authority_are_static(metadata_tool, static_repository):
    root, _, module = static_repository
    path = module / "__init__.py"
    path.write_text("plugin=Plugin('fictional','Fictional',insights=insights,contract=PluginContract(\n"
                    "requires=(Requirement('ledger.read',2,3),Requirement('custom.source'),),\n"
                    "conflicts=('custom.writer',),state_version=3,state_upgrades=((1,2),(2,3)),))\n")
    row = metadata_tool.descriptor_metadata(path, "plugin", root)
    assert row["authority"] == "local" and row["legacy"] is False
    assert row["requires"] == [{"name": "ledger.read", "min_version": 2, "max_version": 3},
                               {"name": "custom.source", "min_version": 1}]
    assert row["conflicts"] == ["custom.writer"]
    assert row["state_upgrades"] == [[1, 2], [2, 3]]


def test_static_boolean_cannot_impersonate_numeric_sdk_version(metadata_tool, static_repository):
    root, project, _ = static_repository
    path = project / "pyproject.toml"
    path.write_text(path.read_text().replace("api-version=1", "api-version=true"))
    with pytest.raises(ValueError, match="declarations differ"):
        metadata_tool.source_inventory(root)


@pytest.mark.parametrize("hooks, support", [
    ("bank_logos=branding,settings=settings,branding_factory=fresh", "stateless-optional-worker"),
    ("file_import=parser", "stateless-optional-worker"),
    ("insights=insights,export=export", "stateless-report-worker"),
    ("categorization=engine", "categorization-worker"),
])
def test_supported_live_families_are_inspected_without_executing_factories(metadata_tool, static_repository, hooks, support):
    root, _, module = static_repository
    path = module / "__init__.py"
    path.write_text("raise RuntimeError('must not execute')\nplugin=Plugin('fictional','Fictional'," + hooks +
                    ",contract=PluginContract(lifecycle='live'))\n")
    assert metadata_tool.descriptor_metadata(path, "plugin", root)["live_support"] == support


@pytest.mark.parametrize("extra", ["", ",authority='remote'", ",state_version=2"])
def test_local_storage_worker_static_contract_is_explicit(metadata_tool, static_repository, extra):
    root, _, module = static_repository
    path = module / "__init__.py"
    path.write_text("raise RuntimeError('must not execute')\nplugin=Plugin('fictional','Fictional',storage=factory,"
                    "contract=PluginContract(lifecycle='live',provides=(Capability('storage.worker'),"
                    "Capability('storage.authority'),Capability('storage.commands'))" + extra + "))\n")
    if extra:
        with pytest.raises(ValueError):
            metadata_tool.descriptor_metadata(path, "plugin", root)
    else:
        assert metadata_tool.descriptor_metadata(path, "plugin", root)["live_support"] == "local-storage-worker"


@pytest.mark.parametrize("hooks, contract", [
    ("categorization=engine", ",state_version=2"),
    ("file_import=parser,settings=settings", ""),
    ("file_import=parser,branding_factory=fresh", ""),
    ("bank_logos=branding,bind=binder", ""),
    ("bank_logos=branding", ",state_version=2"),
    ("insights=insights", ",provides=(Capability('reporting.insights',2),)"),
])
def test_unsupported_live_shapes_cannot_claim_worker_compatibility(metadata_tool, static_repository, hooks, contract):
    root, _, module = static_repository
    path = module / "__init__.py"
    path.write_text("plugin=Plugin('fictional','Fictional'," + hooks +
                    ",contract=PluginContract(lifecycle='live'" + contract + "))\n")
    with pytest.raises(ValueError, match="supported capability adapter"):
        metadata_tool.descriptor_metadata(path, "plugin", root)


def test_static_source_reader_rejects_private_links_and_bounds(metadata_tool, static_repository, tmp_path, monkeypatch):
    root, project, module = static_repository
    private = tmp_path / "private.py"
    private.write_text("raise RuntimeError('private contents must not be read')")
    path = module / "__init__.py"
    path.unlink()
    path.symlink_to(private)
    with pytest.raises(ValueError, match="symlink"):
        metadata_tool.source_inventory(root)
    path.unlink()
    path.write_text("x" * 100)
    monkeypatch.setattr(metadata_tool, "MAX_SOURCE_BYTES", 10)
    with pytest.raises(ValueError, match="bound"):
        metadata_tool.source_text(project / "pyproject.toml", root)


def test_actual_catalog_has_exact_static_contracts_for_supported_packages(metadata_tool):
    records = metadata_tool.source_inventory()
    generated = metadata_tool.build_catalog()
    assert {row["id"] for row in records if row["support"] == "maintained"} == {
        "web", "sqlite", "json", "plaid", "bank-logos", "ynab", "server", "client", "random-forest-categorization", "llm-categorization",
    }
    assert json.loads(json.dumps(generated))["plugins"]


@pytest.mark.parametrize("interface", [
    "Interface(adapter=RemoteLedgerInterface())",
    "Interface(adapter=RemoteLedgerInterface(token_setting='FICTIONAL_TOKEN',commands=False))",
])
def test_literal_api_adapter_is_classified_without_import_or_factory_execution(metadata_tool, static_repository, interface):
    root, _, module = static_repository
    path = module / "__init__.py"
    path.write_text("raise RuntimeError('source must never execute')\nplugin=Plugin('fictional','Fictional',interface=" + interface + ",contract=PluginContract(lifecycle='live'))\n")
    row = metadata_tool.descriptor_metadata(path, "plugin", root)
    assert row["live_support"] == "declarative-api-worker"
    assert row["capabilities"] == [{"name": "interface.api", "version": 1, "composition": "additive"}]


@pytest.mark.parametrize("interface", [
    "Interface(factory=RemoteLedgerInterface())", "Interface(factory)",
    "Interface(adapter=dynamic())", "Interface(adapter=RemoteLedgerInterface(protocol_version=True))",
    "Interface(adapter=RemoteLedgerInterface(protocol_version=2))",
    "Interface(adapter=RemoteLedgerInterface(token_setting='PRIVATE.token'))",
    "Interface(adapter=RemoteLedgerInterface(commands=1))",
    "Interface(adapter=RemoteLedgerInterface(queries=False,commands=False))",
    "Interface(adapter=RemoteLedgerInterface(unknown=True))",
])
def test_api_factories_dynamic_or_invalid_adapter_shapes_cannot_claim_live_support(metadata_tool, static_repository, interface):
    root, _, module = static_repository
    path = module / "__init__.py"
    path.write_text("raise RuntimeError('source must never execute')\nplugin=Plugin('fictional','Fictional',interface=" + interface + ",contract=PluginContract(lifecycle='live'))\n")
    with pytest.raises(ValueError, match="supported capability adapter"):
        metadata_tool.descriptor_metadata(path, "plugin", root)


@pytest.mark.parametrize("options", ["view_protocol=1", "view_protocol=1,budget_months=3,lightweight_accounts=True"])
def test_visual_participation_is_classified_without_importing_the_context_builder(metadata_tool, static_repository, options):
    root, _, module = static_repository
    path = module / "__init__.py"
    path.write_text("raise RuntimeError('source must never execute')\nplugin=Plugin('fictional','Fictional',"
                    "ui=UserInterface(templates,static,build_context," + options + "),contract=PluginContract(lifecycle='live'))\n")
    row = metadata_tool.descriptor_metadata(path, "plugin", root)
    assert row["live_support"] == "visual-ui-worker"
    assert row["capabilities"] == [{"name": "interface.ui", "version": 1, "composition": "singleton"}]


@pytest.mark.parametrize("options", ["", "view_protocol=0", "view_protocol=True", "view_protocol=dynamic()",
    "view_protocol=1,view_protocol=0", "view_protocol=1,render=callback", "view_protocol=1,budget_months=True",
    "view_protocol=1,lightweight_accounts=1", "view_protocol=1,transaction_page_size=1001", "view_protocol=1,unknown=True"])
def test_invalid_or_legacy_visual_descriptor_cannot_claim_worker_participation(metadata_tool, static_repository, options):
    root, _, module = static_repository
    path = module / "__init__.py"
    path.write_text("plugin=Plugin('fictional','Fictional',ui=UserInterface(templates,static,build_context,"
                    + options + "),contract=PluginContract(lifecycle='live'))\n")
    with pytest.raises(ValueError, match="supported capability adapter"):
        metadata_tool.descriptor_metadata(path, "plugin", root)


@pytest.mark.parametrize("identifier,adapter", [
    ("client", "RemoteLedgerClient()"),
    ("fictional", "RemoteLedgerClient(url_setting='FICTIONAL_URL',token_setting='FICTIONAL_TOKEN',budget_setting='FICTIONAL_BUDGET_ID')"),
])
def test_remote_client_literal_is_static_metadata_only(metadata_tool, static_repository, tmp_path, identifier, adapter):
    root, _, module = static_repository
    path, marker = module / "__init__.py", tmp_path / "must-not-execute"
    path.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
                    f"plugin=Plugin({identifier!r},'Fictional',storage={adapter},contract=PluginContract(authority='remote',lifecycle='live'))\n")
    row = metadata_tool.descriptor_metadata(path, "plugin", root)
    assert row["live_support"] == "declarative-remote-client-worker" and row["authority"] == "remote"
    assert row["capabilities"] == [{"name": name, "version": 1, "composition": "singleton"}
                                   for name in ("ledger.mutate", "ledger.read")]
    assert not marker.exists()


@pytest.mark.parametrize("adapter,extra,contract", [
    ("factory", "", ""), ("dynamic()", "", ""),
    ("RemoteLedgerClient(protocol_version=True)", "", ""),
    ("RemoteLedgerClient(protocol_version=2)", "", ""),
    ("RemoteLedgerClient(url_setting='https://fictional.example')", "", ""),
    ("RemoteLedgerClient(token_setting='OTHER_TOKEN')", "", ""),
    ("RemoteLedgerClient(token_setting='CLIENT_URL')", "", ""),
    ("RemoteLedgerClient(budget_setting=[])", "", ""),
    ("RemoteLedgerClient(private_token='must-not-evaluate')", "", ""),
    ("RemoteLedgerClient()", ",budget_connection=reader", ""),
    ("RemoteLedgerClient()", ",insights=reporter", ""),
    ("RemoteLedgerClient()", ",bind=binder", ""),
    ("RemoteLedgerClient()", "", ",state_version=2"),
    ("RemoteLedgerClient()", "", ",provides=(Capability('custom.feature'),)"),
])
def test_remote_factory_dynamic_invalid_or_mixed_shapes_cannot_claim_live(metadata_tool, static_repository, adapter, extra, contract):
    root, _, module = static_repository
    path = module / "__init__.py"
    path.write_text("raise RuntimeError('must not execute')\nplugin=Plugin('client','Client',storage=" + adapter + extra
                    + ",contract=PluginContract(authority='remote',lifecycle='live'" + contract + "))\n")
    with pytest.raises(ValueError, match="supported capability adapter"):
        metadata_tool.descriptor_metadata(path, "plugin", root)


@pytest.mark.parametrize("factory", ["BankAdapter(provider)", "BankAdapter(factory=provider)"])
def test_static_typed_bank_marker_and_private_top_level_code_are_never_executed(metadata_tool, static_repository, factory):
    root, _, module = static_repository
    path = module / '__init__.py'
    path.write_text("raise RuntimeError('must never execute provider')\n" +
        "plugin=Plugin('fictional','Fictional',bank=" + factory +
        ",bank_ui=ui,merchant_logos=logos,contract=PluginContract(lifecycle='live',authority='local'," +
        "provides=(Capability('lifecycle.background'),)))\n")
    row = metadata_tool.descriptor_metadata(path, 'plugin', root)
    assert row['live_support'] == 'bank-provider-worker'
    assert {item['name'] for item in row['capabilities']} == {'ingestion.bank', 'lifecycle.background', 'branding.merchant'}


@pytest.mark.parametrize('bank,extra,contract', [
    ('legacy_factory', '', ''), ('make_adapter()', '', ''), ('BankAdapter(make_factory())', '', ''),
    ('BankAdapter(None)', '', ''), ('BankAdapter(provider, extra=True)', '', ''),
    ('BankAdapter(provider)', ',settings=settings', ''), ('BankAdapter(provider)', ',bind=binder', ''),
    ('BankAdapter(provider)', ',categorization=engine', ''), ('BankAdapter(provider)', ',file_import=parser', ''),
    ('BankAdapter(provider)', '', ",authority='remote'"), ('BankAdapter(provider)', '', ',state_version=2'),
    ('BankAdapter(provider)', '', ',state_version=True'), ('BankAdapter(provider)', '', ',state_upgrades=((1,2),)'),
    ('BankAdapter(provider)', '', ",provides=(Capability('lifecycle.background',2),)"),
])
def test_unsupported_or_dynamic_bank_metadata_refuses_without_execution(metadata_tool, static_repository, bank, extra, contract):
    root, _, module = static_repository
    path = module / '__init__.py'
    declared = contract if 'provides=' in contract else ",provides=(Capability('lifecycle.background'),)" + contract
    path.write_text("raise RuntimeError('must never execute provider')\nplugin=Plugin('fictional','Fictional',bank=" + bank + extra +
                    ",contract=PluginContract(lifecycle='live'" + declared + "))\n")
    with pytest.raises(ValueError, match="supported capability adapter"):
        metadata_tool.descriptor_metadata(path, 'plugin', root)
