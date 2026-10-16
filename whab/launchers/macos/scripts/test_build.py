"""Verify that app bundles take source, never private runtime or development state."""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("whab_macos_build", Path(__file__).with_name("build.py"))
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class BundleSourceBoundaryTests(unittest.TestCase):
    def test_includes_setup_template_and_independent_plugin_sources(self):
        for path in (
            ".env.example", "pyproject.toml", "LICENSE", "README.md", "scripts/setup.py",
            "scripts/install.py", "src/whab/__init__.py",
            "plugins/interface/web/pyproject.toml", "plugins/interface/web/README.md",
            "plugins/interface/web/src/whab_web_plugin/templates/base.html",
        ):
            with self.subTest(path=path):
                self.assertTrue(build.source_allowed(Path(path)))

    def test_excludes_runtime_secrets_and_generated_developer_state(self):
        for path in (
            ".env", ".env.production", "data/whab-production.sqlite", "tls/ca.key",
            "tls/ca.crt", "budgets/private/budget.json", "work/python/bin/python3.14",
            ".venv/lib/python3.14/site-packages/private.py", "tests/private_fixture.py",
            "plugins/ingestion/plaid/.env", "plugins/ingestion/plaid/tests/private.py",
            "src/whab/__pycache__/app.pyc", "src/whab/private.key",
            "plugins/ingestion/plaid/src/whab_plaid_plugin/.env.local",
            "src/whab.egg-info/PKG-INFO",
        ):
            with self.subTest(path=path):
                self.assertFalse(build.source_allowed(Path(path)))


if __name__ == "__main__":
    unittest.main()
