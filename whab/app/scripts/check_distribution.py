"""Build and exercise installed wheels outside the checkout, using fictional data only.

Run with the development environment's Python. A temporary target installation
uses its dependencies but cannot resolve WHAB modules from the source checkout.
"""

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SMOKE = """
from pathlib import Path
import importlib.metadata as metadata
import importlib
import json
import sys
from dataclasses import replace
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
expected = json.loads(sys.argv[2])
import whab
from whab.app import create_app
from whab.config import Settings
from whab.plugins import LogoImage, load_plugins
from whab_plugin_plaid.logos import logo_key
from fastapi.testclient import TestClient

target = Path(sys.argv[1]).resolve()
assert Path(whab.__file__).resolve().is_relative_to(target)
assert metadata.version("whab") == whab.__version__ == expected["whab"]["version"]
assert not metadata.distribution("whab").entry_points
assert {entry.name for entry in metadata.entry_points(group="whab.plugins")} == set(expected) - {"whab"}
for identifier, record in expected.items():
    assert metadata.version(record["distribution"]) == record["version"], identifier
    if identifier == "whab":
        continue
    entries = tuple(entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == identifier)
    assert len(entries) == 1, identifier
    assert entries[0].dist.metadata["Name"] == record["distribution"], identifier
    module = importlib.import_module(record["module"])
    assert Path(module.__file__).resolve().is_relative_to(target), identifier
assert {"server", "client"} <= {entry.name for entry in metadata.entry_points(group="whab.plugins")}
assert load_plugins(("server",)).interface_plugins()[0].id == "server"
assert load_plugins(("client",)).storage_plugin("client").id == "client"
for name in (record["distribution"] for record in expected.values()):
    distribution = metadata.distribution(name)
    assert distribution.metadata["License-Expression"] == "MIT", name
    assert distribution.metadata.get_all("License-File") == ["LICENSE"], name
    license_text = distribution.read_text("licenses/LICENSE")
    assert license_text and license_text.startswith("MIT License"), name
    assert "Permission is hereby granted, free of charge" in license_text, name
settings = Settings.load()
assert settings.home == Path.cwd()
assert settings.storage == "sqlite" and settings.ui == "web"
assert settings.plugins == ("plaid", "example", "bank-logos", "ynab")
registry = load_plugins(settings.enabled_plugins())
assert [p.id for p in registry.describe()] == ["bank-logos", "example", "plaid", "sqlite", "web", "ynab"]
assert all(p.lifecycle == "restart" and p.legacy for p in registry.describe() if p.id != "example")
assert next(p for p in registry.describe() if p.id == "example").lifecycle == "live"
assert not next(p for p in registry.describe() if p.id == "example").legacy
remote = load_plugins(("client",)).describe()[0]
assert remote.authority == "remote" and not remote.legacy
assert {row.name for row in remote.capabilities} == {"ledger.read", "ledger.mutate"}
assert registry.bank_logo("Chase") == ("bank-logos", "chase")
assert registry.bank_logo("Demo Bank") is None
assert logo_key("https://plaid-merchant-logos.plaid.com/walmart_1100.png") == "merchant-walmart_1100"
assert next(p for p in registry.describe() if p.id == "plaid").has_merchant_logos
app = create_app(settings=settings, plugins=registry)
assert app.version == whab.__version__
with TestClient(app, base_url="https://127.0.0.1:5173") as client:
    for page in ("budget", "transactions", "insights", "accounts"):
        response = client.get(f"/budgets/demo/{page}/current-month/usd")
        assert response.status_code == 200, page
        assert f"WHAB · {whab.__version__}" in response.text
    for asset in ("styles.css", "theme.js", "app.js", "budget-edits.js", "client.js", "favicon.svg", "logo.svg"):
        assert client.get("/budgets/demo/static/" + asset).status_code == 200, asset
    assert client.get("/budgets/home/plugins/plaid/static/connect.js").status_code == 200
    assert client.get("/budgets/home/plugins/plaid/static/oauth.js").status_code == 200
    assert client.get("/budgets/home/imports/ynab/static/import.js").status_code == 200
    personal = client.get("/budgets/home/accounts/current-month/usd")
    assert personal.status_code == 200 and 'data-file-import="ynab"' in personal.text
    tracking = client.post('/api/accounts', json={
        'workspace': 'personal', 'name': 'Fictional brokerage', 'type': 'investment',
        'subtype': 'brokerage', 'balance': 12300,
    })
    assert tracking.status_code == 200
    tracking_id = tracking.json()['accountId']
    view = client.get('/api/workspace?workspace=personal').json()
    assert view['summary']['readyToAssign'] == 0
    assert view['accountSummary']['netWorth'] == 12300
    assert client.patch('/api/accounts/' + tracking_id, json={
        'workspace': 'personal', 'type': 'retirement', 'subtype': '401k', 'onBudget': False,
    }).status_code == 200
    assert '401(k)' in client.get('/budgets/home/accounts/current-month/usd').text
    assert any(p["id"] == "ynab" and p["has_file_import"] for p in client.get("/api/plugins").json()["plugins"])
    assert client.get("/api/plugins/bank-logos/bank-logos/unknown").status_code == 404
    assert client.get("/api/plugins/plaid/merchant-logos/unknown").status_code == 404
    assert any(p["id"] == "bank-logos" and p["has_bank_logos"] for p in client.get("/api/plugins").json()["plugins"])
    management = client.get("/api/plugins/management").json()
    for row in management["plugins"]:
        if row["id"] in expected:
            assert row["version"] == expected[row["id"]]["version"], row["id"]
            assert row["distribution"] == expected[row["id"]]["distribution"], row["id"]
    logos = next(row for row in management["plugins"] if row["id"] == "bank-logos")
    assert logos["category"] == "interface"
    logo_settings = client.get("/api/plugins/bank-logos/settings")
    assert logo_settings.status_code == 200 and logo_settings.json()["insights"]
    assert any(row["title"] == "Cache duration" and row["value"] == "Indefinite" for row in logo_settings.json()["insights"])
    assert [(row["id"], row["label"]) for row in logo_settings.json()["actions"]] == [("flush-cache", "Flush cache")]
    assert client.patch('/api/accounts/' + tracking_id, json={
        'workspace': 'personal', 'bankWebsite': 'https://bank.example.org',
    }).status_code == 200
    saved_accounts = client.get('/api/workspace?workspace=personal').json()['accounts']
    image = LogoImage(bytes.fromhex('89504e470d0a1a0a') + b'fictional', 'image/png')
    provider = importlib.import_module('whab_plugin_bank_logos.plugin')
    with patch.object(provider, 'fetch_logo', return_value=image) as fetch:
        preview = client.post('/api/plugins/bank-logo-preview', json={'website': 'bank.example.org'}).json()
        assert preview['available'] and preview['website'] == 'https://bank.example.org'
        logo_url = preview['logoUrl']
        assert client.get(logo_url).content == image.data and fetch.call_count == 1
        cache = settings.home / 'cache' / 'plugins' / 'bank-logos'
        assert tuple(cache.glob('domain-*.json'))
        flushed = client.post('/api/plugins/bank-logos/settings/actions/flush-cache', json={})
        assert flushed.status_code == 200
        assert any(row['title'] == 'Cached logos' and row['value'] == '0' for row in flushed.json()['settings']['insights'])
        assert not tuple(cache.glob('domain-*.json'))
        assert client.get('/api/workspace?workspace=personal').json()['accounts'] == saved_accounts
        # The saved public hostname survives flushing, so the same image URL can fetch again.
        assert client.get(logo_url).content == image.data and fetch.call_count == 2
        assert client.get(logo_url).content == image.data and fetch.call_count == 2
        assert tuple(cache.glob('domain-*.json'))
    assert client.get("/api/plugins/web/settings").json() == {"insights": [], "actions": []}
    assert client.post("/api/plaid/link-token", json={}).status_code == 400
    export = client.get("/api/plugins/example/export?workspace=demo")
    assert export.status_code == 200
    assert "text/csv" in export.headers["content-type"]
    created = client.post("/api/budget/new", json={"name": "Installed wheel budget"})
    assert created.status_code == 200
    assert client.get("/api/budget").json()["name"] == "Installed wheel budget"
    assert client.get(created.json()["url"]).status_code == 200
    from whab.budgets.container import decode_container
    container = decode_container(client.get("/api/budget/export").content)
    assert container.manifest["name"] == "Installed wheel budget"
    assert container.legacy_ledger is None
    assert container.files["plugins/sqlite/budget.sqlite"].startswith(b"SQLite format 3")
    assert client.post("/api/budgets/default/activate", json={}).status_code == 200
assert (settings.home / "data" / "whab-sandbox.sqlite").is_file()
json_settings = replace(settings, home=settings.home / "json-reference", storage="json", plugins=())
with TestClient(create_app(settings=json_settings), base_url="https://127.0.0.1:5173") as client:
    created = client.post("/api/accounts", json={
        "workspace": "personal", "name": "Fictional JSON checking", "type": "depository", "balance": 12345,
    })
    assert created.status_code == 200
    account_id = created.json()["accountId"]
    transaction = client.post("/api/transactions", json={
        "workspace": "personal", "accountId": account_id, "date": "2026-10-01",
        "merchant": "Fictional JSON purchase", "amount": 125, "categoryId": None,
    })
    assert transaction.status_code == 200
    assert client.get("/api/workspace?workspace=personal").json()["accounts"][0]["balance"] == 12220
    exported = decode_container(client.get("/api/budget/export").content)
    assert exported.manifest["storage"] == "json" and "plugins/json/budget.json" in exported.files
    demo = decode_container(client.get("/api/budget/export?workspace=demo").content)
    assert demo.manifest["storage"] == "json" and "plugins/json/budget.json" in demo.files
with TestClient(create_app(settings=json_settings), base_url="https://127.0.0.1:5173") as client:
    account = client.get("/api/workspace?workspace=personal").json()["accounts"][0]
    assert account["id"] == account_id and account["balance"] == 12220
assert (json_settings.home / "data" / "whab-sandbox.json").is_file()
assert not tuple(json_settings.home.rglob("*.sqlite"))
print("Installed wheels: independent versions, MIT licenses, pages, assets and plugin discovery passed.")
"""

INDEPENDENT_VERSION = """
import importlib.metadata as metadata
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
expected = json.loads(sys.argv[2])
import whab
from whab.plugins.management import package_details
assert Path(whab.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
assert metadata.version("whab") == whab.__version__ == expected["host_version"]
distribution = metadata.distribution(expected["distribution"])
assert distribution.version == expected["plugin_version"]
entry, = distribution.entry_points
assert entry.group == "whab.plugins"
assert package_details(entry) == (expected["distribution"], expected["plugin_version"])
print("A plugin-only version update leaves the host version unchanged and reaches the plugin manager.")
"""


def run(*args, cwd, env):
    subprocess.run([sys.executable, *args], cwd=cwd, env=env, check=True)


def project_metadata(source):
    return tomllib.loads((source / "pyproject.toml").read_text())["project"]


def application_version(source):
    for statement in ast.parse((source / "src/whab/__init__.py").read_text()).body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in statement.targets
        ):
            return ast.literal_eval(statement.value)
    raise AssertionError("Application version source is missing")


def install_wheels(packages, target, *, cwd, env):
    run(
        "-m", "pip", "install", "--disable-pip-version-check", "--no-deps",
        "--target", str(target), *(str(package) for package in packages), cwd=cwd, env=env,
    )


def main():
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("WHAB_", "PLAID_", "SERVER_", "CLIENT_"))
        and key not in {"TOKEN_ENCRYPTION_KEY", "ALLOW_LIVE_PLAID", "PORT", "PYTHONPATH"}
    }
    env.update(
        PLAID_ENV="sandbox", ALLOW_LIVE_PLAID="false", WHAB_PLUGINS="plaid,example,bank-logos,ynab", PORT="5173"
    )
    with tempfile.TemporaryDirectory(prefix="whab-distribution-") as temporary:
        base = Path(temporary)
        source, wheels, installed, home = (
            base / name for name in ("source", "wheels", "installed", "home")
        )
        source.mkdir()
        home.mkdir()
        # Build from a minimal source copy so old build artifacts cannot leak into a wheel.
        ignore = shutil.ignore_patterns("__pycache__", "*.egg-info", "*.pyc")
        shutil.copytree(ROOT / "src", source / "src", ignore=ignore)
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(ROOT / name, source / name)
        projects = [source]
        expected = {"whab": {"distribution": "whab", "version": application_version(source)}}
        copied = {}
        plugin_ignore = shutil.ignore_patterns(
            "__pycache__", "*.egg-info", "*.pyc", "tests", "build", "dist", ".pytest_cache", ".ruff_cache"
        )
        for project in sorted((ROOT / "plugins").glob("*/*/pyproject.toml")):
            metadata = project_metadata(project.parent)
            entrypoints = metadata["entry-points"]["whab.plugins"]
            assert len(entrypoints) == 1, project
            identifier, entrypoint = next(iter(entrypoints.items()))
            package_name = "whab_plugin_" + identifier.replace("-", "_")
            assert metadata["name"] == package_name, project
            assert entrypoint.split(":", 1)[0].split(".", 1)[0] == package_name, project
            destination = base / "plugins" / identifier
            shutil.copytree(project.parent, destination, ignore=plugin_ignore)
            copied[identifier] = destination
            projects.append(destination)
            expected[identifier] = {
                "distribution": metadata["name"], "version": metadata["version"],
                "module": entrypoint.split(":", 1)[0].split(".", 1)[0],
            }
        assert set(expected) == {"whab", "web", "sqlite", "json", "plaid", "bank-logos", "ynab", "server", "client", "example", "random-forest-categorization"}
        optional = project_metadata(source)["optional-dependencies"]["bundled"]
        assert set(optional) == {
            f'{record["distribution"]}>={record["version"]},<2'
            for identifier, record in expected.items() if identifier not in {"whab", "example"}
        }
        run(
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(wheels),
            *(str(project) for project in projects),
            cwd=base,
            env=env,
        )
        packages = sorted(wheels.glob("*.whl"))
        assert len(packages) == len(projects), "Expected one host wheel and one wheel per plugin"
        install_wheels(packages, installed, cwd=base, env=env)
        run("-I", "-c", SMOKE, str(installed), json.dumps(expected), cwd=home, env=env)
        run(
            "-I",
            "-c",
            "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('whab',run_name='__main__')",
            str(installed),
            "--version",
            cwd=home,
            env=env,
        )
        # Rebuild only one copied plugin: its independent distribution metadata
        # must reach discovery without changing the host or another plugin.
        probe = copied["bank-logos"]
        original_version = expected["bank-logos"]["version"]
        major, minor, patch = (int(part) for part in original_version.split("."))
        probe_version = f"{major}.{minor}.{patch + 1}"
        project = probe / "pyproject.toml"
        project.write_text(project.read_text().replace(
            f'version = "{original_version}"', f'version = "{probe_version}"', 1,
        ))
        probe_wheels = base / "probe-wheels"
        run(
            "-m", "pip", "wheel", "--disable-pip-version-check", "--no-deps",
            "--wheel-dir", str(probe_wheels), str(probe), cwd=base, env=env,
        )
        host_wheel, = (package for package in packages if package.name.startswith("whab-"))
        probe_wheel, = probe_wheels.glob("*.whl")
        probe_target = base / "independent-version"
        install_wheels((host_wheel, probe_wheel), probe_target, cwd=base, env=env)
        run(
            "-I", "-c", INDEPENDENT_VERSION, str(probe_target), json.dumps({
                "host_version": expected["whab"]["version"],
                "distribution": expected["bank-logos"]["distribution"],
                "plugin_version": probe_version,
            }), cwd=home, env=env,
        )


if __name__ == "__main__":
    main()
