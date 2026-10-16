"""Exercise checkout setup without touching the user's runtime configuration."""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from whab.tls import prepare_local_tls

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def clean_checkout(tmp_path):
    application = tmp_path / "checkout" / "whab"
    scripts = application / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/setup.py", scripts / "setup.py")
    shutil.copyfile(ROOT / ".env.example", application / ".env.example")
    return application


def run_setup(application, cwd, home=None):
    environment = {key: value for key, value in os.environ.items() if key not in {
        "WHAB_HOME", "WHAB_TLS_CERT_FILE", "WHAB_TLS_KEY_FILE",
    }}
    if home is not None:
        environment["WHAB_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(application / "scripts/setup.py")],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )


def test_fresh_checkout_generates_private_sandbox_configuration(clean_checkout, tmp_path):
    result = run_setup(clean_checkout, tmp_path)

    configuration = clean_checkout / ".env"
    values = dict(
        line.split("=", 1)
        for line in configuration.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    assert stat.S_IMODE(configuration.stat().st_mode) == 0o600
    assert values["PLAID_ENV"] == "sandbox"
    assert values["ALLOW_LIVE_PLAID"] == "false"
    assert values["PLAID_CLIENT_ID"] == values["PLAID_SECRET"] == ""
    assert len(bytes.fromhex(values["TOKEN_ENCRYPTION_KEY"])) == 32
    assert values["TOKEN_ENCRYPTION_KEY"] not in result.stdout + result.stderr
    assert not (tmp_path / ".env").exists()
    assert (clean_checkout / "tls" / "server.crt").exists()
    for key in ("server.key", "ca.key"):
        assert stat.S_IMODE((clean_checkout / "tls" / key).stat().st_mode) == 0o600
    assert "Manually trust this CA" in result.stdout
    assert "does not change your operating system's trust" in result.stdout


def test_setup_preserves_existing_credentials_and_key_bytes(clean_checkout, tmp_path):
    configuration = clean_checkout / ".env"
    original = (
        b"# Synthetic configuration only\n"
        b"PORT=5173\n"
        b"PLAID_CLIENT_ID=fictional-client\n"
        b"PLAID_SECRET='fictional-secret-with-#-and-='\n"
        b"TOKEN_ENCRYPTION_KEY=" + b"12" * 32 + b"\n"
    )
    configuration.write_bytes(original)
    configuration.chmod(0o644)

    result = run_setup(clean_checkout, tmp_path)

    assert configuration.read_bytes() == original
    assert stat.S_IMODE(configuration.stat().st_mode) == 0o600
    assert "fictional-client" not in result.stdout + result.stderr
    assert "fictional-secret" not in result.stdout + result.stderr


def test_setup_migrates_only_the_legacy_port(clean_checkout, tmp_path):
    configuration = clean_checkout / ".env"
    original = b"PORT=3001\nPLAID_SECRET=fictional-port-3001\nTOKEN_ENCRYPTION_KEY=" + b"34" * 32
    configuration.write_bytes(original)

    run_setup(clean_checkout, tmp_path)

    assert configuration.read_bytes() == original.replace(b"PORT=3001\n", b"PORT=5173\n")


def test_supplied_certificate_does_not_prompt_trust_of_an_unrelated_local_ca(clean_checkout, tmp_path):
    prepare_local_tls(clean_checkout)
    cert, key = prepare_local_tls(tmp_path / "supplied")
    (clean_checkout / ".env").write_text(f"WHAB_TLS_CERT_FILE={cert}\nWHAB_TLS_KEY_FILE={key}\n")
    result = run_setup(clean_checkout, tmp_path)
    assert "Ensure your supplied certificate is trusted" in result.stdout
    assert "Manually trust this CA" not in result.stdout
    assert "Local CA certificate:" not in result.stdout


def test_setup_uses_the_explicit_runtime_home(clean_checkout, tmp_path):
    home = tmp_path / "runtime"
    result = run_setup(clean_checkout, tmp_path, home=home)
    assert (home / ".env").exists() and (home / "tls/server.crt").exists()
    assert not (clean_checkout / ".env").exists() and not (clean_checkout / "tls").exists()
    assert str(home / "tls/ca.crt") in result.stdout
