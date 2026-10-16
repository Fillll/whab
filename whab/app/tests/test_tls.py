"""Real synthetic certificates exercise fail-closed local HTTPS provisioning."""

import ipaddress
import socket
import ssl
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from whab import __main__ as cli
from whab.config import Settings
from whab.tls import prepare_local_tls, tls_paths, validate_tls


def test_local_certificates_are_valid_private_and_preserved(tmp_path):
    cert_path, key_path = prepare_local_tls(tmp_path)
    authority_path = tmp_path / "tls" / "ca.crt"
    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    authority = x509.load_pem_x509_certificate(authority_path.read_bytes())
    certificate.verify_directly_issued_by(authority)
    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == ["localhost"]
    assert names.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address("127.0.0.1")]
    assert certificate.not_valid_after_utc > datetime.now(UTC) + timedelta(days=364)
    for name in ("server.key", "ca.key"):
        assert stat.S_IMODE((tmp_path / "tls" / name).stat().st_mode) == 0o600
    original = {path: path.read_bytes() for path in (tmp_path / "tls").iterdir()}
    assert prepare_local_tls(tmp_path, "tls/server.crt", "tls/server.key") == (cert_path, key_path)
    assert all(path.read_bytes() == raw for path, raw in original.items())
    # The generated chain can be trusted explicitly without disabling verification.
    ssl.create_default_context(cafile=authority_path)
    validate_tls(cert_path, key_path)


def test_local_ca_is_reused_without_overwriting_its_key(tmp_path):
    cert, key = prepare_local_tls(tmp_path)
    authority = (tmp_path / "tls" / "ca.key").read_bytes()
    cert.unlink()
    key.unlink()
    prepare_local_tls(tmp_path)
    assert (tmp_path / "tls" / "ca.key").read_bytes() == authority


def test_generated_certificate_supports_a_verified_tls_connection(tmp_path):
    cert, key = prepare_local_tls(tmp_path)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    client_context = ssl.create_default_context(cafile=tmp_path / "tls/ca.crt")
    server_socket, client_socket = socket.socketpair()
    server_socket.settimeout(5)
    client_socket.settimeout(5)

    def serve():
        with server_context.wrap_socket(server_socket, server_side=True) as connection:
            connection.sendall(b"verified local HTTPS")

    with ThreadPoolExecutor(max_workers=1) as executor:
        served = executor.submit(serve)
        with client_context.wrap_socket(client_socket, server_hostname="127.0.0.1") as connection:
            assert connection.recv(100) == b"verified local HTTPS"
        served.result(timeout=5)


def test_ca_certificate_is_not_accepted_as_a_server_certificate(tmp_path):
    prepare_local_tls(tmp_path)
    with pytest.raises(ValueError, match="HTTPS requires"):
        validate_tls(tmp_path / "tls/ca.crt", tmp_path / "tls/ca.key")


def test_tls_paths_are_runtime_relative_and_must_be_paired(tmp_path):
    assert tls_paths(tmp_path) == (tmp_path / "tls/server.crt", tmp_path / "tls/server.key")
    assert tls_paths(tmp_path, "custom/cert", "custom/key") == (tmp_path / "custom/cert", tmp_path / "custom/key")
    with pytest.raises(ValueError, match="both WHAB_TLS"):
        tls_paths(tmp_path, "custom/cert")


@pytest.mark.parametrize("damage", ["missing", "mismatched", "expired", "corrupt", "permissions"])
def test_missing_or_invalid_tls_never_falls_back_to_http(tmp_path, damage):
    cert, key = prepare_local_tls(tmp_path / "first")
    if damage == "missing":
        key.unlink()
    elif damage == "mismatched":
        _, other_key = prepare_local_tls(tmp_path / "second")
        key.write_bytes(other_key.read_bytes())
    elif damage == "expired":
        original = x509.load_pem_x509_certificate(cert.read_bytes())
        private = serialization.load_pem_private_key(key.read_bytes(), password=None)
        now = datetime.now(UTC)
        expired = (
            x509.CertificateBuilder().subject_name(original.subject).issuer_name(original.subject)
            .public_key(private.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2)).not_valid_after(now - timedelta(days=1))
            .sign(private, hashes.SHA256())
        )
        cert.write_bytes(expired.public_bytes(serialization.Encoding.PEM))
    elif damage == "corrupt":
        cert.write_bytes(b"not a certificate")
    else:
        key.chmod(0o644)
    with pytest.raises(ValueError, match="HTTPS requires"):
        validate_tls(cert, key)
    with pytest.raises(ValueError, match="HTTPS requires"):
        prepare_local_tls(tmp_path / "first")


def test_custom_tls_is_validated_without_creating_or_replacing_files(tmp_path):
    cert, key = prepare_local_tls(tmp_path / "source")
    before = key.read_bytes()
    assert prepare_local_tls(tmp_path / "runtime", cert, key) == (cert, key)
    assert not (tmp_path / "runtime").exists() and key.read_bytes() == before
    with pytest.raises(ValueError, match="HTTPS requires"):
        prepare_local_tls(tmp_path / "runtime", "missing.crt", "missing.key")


def test_incomplete_local_ca_is_preserved_and_rejected(tmp_path):
    directory = tmp_path / "tls"
    directory.mkdir()
    (directory / "ca.crt").write_bytes(b"preserve this invalid certificate")
    with pytest.raises(ValueError, match="authority is incomplete"):
        prepare_local_tls(tmp_path)
    assert (directory / "ca.crt").read_bytes() == b"preserve this invalid certificate"
    assert not (directory / "server.key").exists()


def test_cli_uses_native_tls_and_never_trusts_forwarded_scheme(tmp_path, monkeypatch):
    cert, key = prepare_local_tls(tmp_path)
    configuration = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", tls_cert_file=cert, tls_key_file=key)
    monkeypatch.setattr(cli.Settings, "load", lambda: configuration)
    monkeypatch.setattr(sys, "argv", ["whab"])
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    cli.main()
    assert calls[0]["ssl_certfile"] == str(cert) and calls[0]["ssl_keyfile"] == str(key)
    assert calls[0]["proxy_headers"] is False and calls[0]["forwarded_allow_ips"] == ""
    assert calls[0]["ssl_version"] == ssl.PROTOCOL_TLS_SERVER


def test_cli_refuses_missing_tls_before_creating_a_budget(tmp_path, monkeypatch, capsys):
    home = tmp_path / "runtime"
    cert, key = tls_paths(home)
    configuration = Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), tls_cert_file=cert, tls_key_file=key)
    monkeypatch.setattr(cli.Settings, "load", lambda: configuration)
    path = tmp_path / "Household.whab"
    monkeypatch.setattr(sys, "argv", ["whab", "--budget", str(path), "--create", "Household"])
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("No listener before TLS validation"))
    # main() only sets WHAB_BUDGET after successful creation in this path.
    monkeypatch.delenv("WHAB_BUDGET", raising=False)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2 and "HTTPS requires" in capsys.readouterr().err
    assert not home.exists() and not path.exists()


def test_settings_resolve_supplied_tls_paths_without_reading_keys(tmp_path, monkeypatch):
    for name in ("WHAB_BUDGET", "WHAB_BUDGET_PASSWORD_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WHAB_HOME", str(tmp_path))
    monkeypatch.setenv("WHAB_TLS_CERT_FILE", "supplied/cert.pem")
    monkeypatch.setenv("WHAB_TLS_KEY_FILE", "supplied/key.pem")
    config = Settings.load()
    assert config.tls_cert_file == tmp_path / "supplied/cert.pem"
    assert config.tls_key_file == tmp_path / "supplied/key.pem"
    assert not list(tmp_path.iterdir())
    monkeypatch.delenv("WHAB_TLS_KEY_FILE")
    with pytest.raises(ValueError, match="both WHAB_TLS"):
        Settings.load()
