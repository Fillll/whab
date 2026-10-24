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
    (app / "plugins/catalog.toml").write_text('''verification=[]
[publisher]
maintainer="Fillll"
repository="https://github.com/Fillll/whab"
documentation_ref="b4c555cc437357a833a858d37d46938f93e6a48f"
security="https://github.com/Fillll/whab/security/advisories/new"
security_policy="https://github.com/Fillll/whab/security/policy"
security_verified_date="2026-10-23"
support_policy="Fictional test policy"
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
    assert len(outputs) == 2 and all("fictional" in text for text in outputs.values())
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
    ("categorization=engine", "categorization-host"),
])
def test_supported_live_families_are_inspected_without_executing_factories(metadata_tool, static_repository, hooks, support):
    root, _, module = static_repository
    path = module / "__init__.py"
    path.write_text("raise RuntimeError('must not execute')\nplugin=Plugin('fictional','Fictional'," + hooks +
                    ",contract=PluginContract(lifecycle='live'))\n")
    assert metadata_tool.descriptor_metadata(path, "plugin", root)["live_support"] == support


@pytest.mark.parametrize("hooks, contract", [
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
    with pytest.raises(ValueError, match="supported stateless worker"):
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
        "web", "sqlite", "json", "plaid", "bank-logos", "ynab", "server", "client", "random-forest-categorization",
    }
    assert json.loads(json.dumps(generated))["plugins"]
