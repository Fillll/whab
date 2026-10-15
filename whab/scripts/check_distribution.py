"""Build and exercise installed wheels outside the checkout, using fictional data only.

Run with the development environment's Python. A temporary target installation
uses its dependencies but cannot resolve WHAB modules from the source checkout.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SMOKE = """
from pathlib import Path
import importlib.metadata as metadata
import sys
sys.path.insert(0, sys.argv[1])
import whab
from whab.app import create_app
from whab.config import Settings
from whab.plugins import load_plugins
from fastapi.testclient import TestClient

target = Path(sys.argv[1]).resolve()
assert Path(whab.__file__).resolve().is_relative_to(target)
assert metadata.version("whab") == whab.__version__
for name in ("whab", "whab-example-plugin"):
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
assert [p.id for p in registry.describe()] == ["plaid", "example", "bank-logos", "ynab", "sqlite", "web"]
assert registry.bank_logo("Chase") == ("bank-logos", "chase")
assert registry.bank_logo("Demo Bank") is None
app = create_app(settings=settings, plugins=registry)
assert app.version == whab.__version__
with TestClient(app, base_url="http://127.0.0.1:5173") as client:
    for page in ("budget", "transactions", "insights", "accounts"):
        response = client.get("/", params={"page": page, "workspace": "demo"})
        assert response.status_code == 200, page
        assert f"WHAB · {whab.__version__}" in response.text
    for asset in ("styles.css", "app.js", "favicon.svg", "logo.svg"):
        assert client.get("/static/" + asset).status_code == 200, asset
    assert client.get("/plugins/plaid/static/connect.js").status_code == 200
    assert client.get("/imports/ynab/static/import.js").status_code == 200
    personal = client.get("/?workspace=personal&page=accounts")
    assert personal.status_code == 200 and 'data-file-import="ynab"' in personal.text
    assert any(p["id"] == "ynab" and p["has_file_import"] for p in client.get("/api/plugins").json()["plugins"])
    assert client.get("/api/plugins/bank-logos/bank-logos/unknown").status_code == 404
    assert any(p["id"] == "bank-logos" and p["has_bank_logos"] for p in client.get("/api/plugins").json()["plugins"])
    management = client.get("/api/plugins/management").json()
    logos = next(row for row in management["plugins"] if row["id"] == "bank-logos")
    assert logos["category"] == "interface"
    options = client.get("/api/plugins/bank-logos/settings")
    assert options.status_code == 200 and options.json()["insights"]
    assert any(action["id"] == "flush-cache" for action in options.json()["actions"])
    flushed = client.post("/api/plugins/bank-logos/settings/actions/flush-cache", json={})
    assert flushed.status_code == 200 and flushed.json()["message"]
    assert client.get("/api/plugins/web/settings").json() == {"insights": [], "actions": []}
    assert client.post("/api/plaid/link-token", json={}).status_code == 400
    export = client.get("/api/plugins/example/export?workspace=demo")
    assert export.status_code == 200
    assert "text/csv" in export.headers["content-type"]
assert (settings.home / "data" / "whab-sandbox.sqlite").is_file()
print("Installed wheels: MIT licenses, pages, assets, version, runtime home and plugin entry point passed.")
"""


def run(*args, cwd, env):
    subprocess.run([sys.executable, *args], cwd=cwd, env=env, check=True)


def main():
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("WHAB_", "PLAID_"))
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
        shutil.copytree(
            ROOT / "plugins",
            source / "plugins",
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.egg-info", "*.pyc", "tests", "build", "dist", ".pytest_cache", ".ruff_cache"
            ),
        )
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(ROOT / name, source / name)
        example = base / "example"
        shutil.copytree(ROOT / "plugins/example/example", example, ignore=ignore)
        run(
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(wheels),
            str(source),
            str(example),
            cwd=base,
            env=env,
        )
        packages = sorted(str(path) for path in wheels.glob("*.whl"))
        assert len(packages) == 2, "Expected app and example plugin wheels"
        run(
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--target",
            str(installed),
            *packages,
            cwd=base,
            env=env,
        )
        run("-I", "-c", SMOKE, str(installed), cwd=home, env=env)
        run(
            "-I",
            "-c",
            "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('whab',run_name='__main__')",
            str(installed),
            "--version",
            cwd=home,
            env=env,
        )


if __name__ == "__main__":
    main()
