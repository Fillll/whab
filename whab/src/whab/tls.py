"""Device-local HTTPS certificates and fail-closed launch validation."""

import ipaddress
import os
import ssl
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def tls_paths(home, cert_file=None, key_file=None):
    """Resolve paired overrides against the runtime home, never the code directory."""
    if bool(cert_file) != bool(key_file):
        raise ValueError("Set both WHAB_TLS_CERT_FILE and WHAB_TLS_KEY_FILE.")
    home = Path(home).expanduser().resolve()

    def resolve(value, default):
        path = Path(value or default).expanduser()
        return (path if path.is_absolute() else home / path).resolve()

    return resolve(cert_file, "tls/server.crt"), resolve(key_file, "tls/server.key")


def validate_tls(cert_file, key_file, *, authority=False):
    """Validate server material without logging certificate/private-key contents."""
    try:
        cert_file, key_file = Path(cert_file), Path(key_file)
        if os.name != "nt" and stat.S_IMODE(key_file.stat().st_mode) & 0o077:
            raise ValueError("TLS private keys must not be accessible to other users.")
        certificates = x509.load_pem_x509_certificates(cert_file.read_bytes())
        if not certificates:
            raise ValueError("Missing certificate.")
        now = datetime.now(UTC)
        if any(not cert.not_valid_before_utc <= now < cert.not_valid_after_utc for cert in certificates):
            raise ValueError("Expired or not-yet-valid certificate.")
        try:
            constraints = certificates[0].extensions.get_extension_for_class(x509.BasicConstraints).value
        except x509.ExtensionNotFound:
            constraints = None
        if not authority and constraints is not None and constraints.ca:
            raise ValueError("A CA certificate cannot be the HTTPS server certificate.")
        try:
            purposes = certificates[0].extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        except x509.ExtensionNotFound:
            purposes = None
        if purposes is not None and ExtendedKeyUsageOID.SERVER_AUTH not in purposes:
            raise ValueError("Certificate does not authorize a TLS server.")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # Supplying a password callback avoids OpenSSL's interactive key prompt.
        context.load_cert_chain(cert_file, key_file, password=lambda: b"")
    except (OSError, ValueError, TypeError, ssl.SSLError):
        raise ValueError(
            "HTTPS requires a valid, current certificate and matching unencrypted private key "
            "readable only by its owner. Run scripts/setup.py or configure "
            "WHAB_TLS_CERT_FILE and WHAB_TLS_KEY_FILE."
        ) from None


def _write_new(path, data, mode):
    """Never replace keys or certificates that appeared during setup."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(data)


def prepare_local_tls(home, cert_file=None, key_file=None):
    """Provision a private local CA once; custom or existing material is preserved."""
    cert_path, key_path = tls_paths(home, cert_file, key_file)
    custom_paths = (cert_path, key_path) != tls_paths(home)
    if custom_paths or cert_path.exists() or key_path.exists():
        validate_tls(cert_path, key_path)
        return cert_path, key_path
    directory = Path(home).expanduser().resolve() / "tls"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    ca_path, ca_key_path = directory / "ca.crt", directory / "ca.key"
    now = datetime.now(UTC)
    if ca_path.exists() or ca_key_path.exists():
        try:
            validate_tls(ca_path, ca_key_path, authority=True)
            authority = x509.load_pem_x509_certificate(ca_path.read_bytes())
            authority_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
            if not authority.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise ValueError()
        except (ValueError, TypeError, x509.ExtensionNotFound):
            raise ValueError("The local HTTPS authority is incomplete or invalid; preserve it and configure valid TLS files.") from None
    else:
        authority_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "WHAB local device CA")])
        authority = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(authority_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(authority_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ), critical=True)
            .sign(authority_key, hashes.SHA256())
        )
        _write_new(ca_key_path, authority_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ), 0o600)
        _write_new(ca_path, authority.public_bytes(serialization.Encoding.PEM), 0o644)
    server_key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(authority.subject).public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(min(now + timedelta(days=365), authority.not_valid_after_utc))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()), critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(authority_key, hashes.SHA256())
    )
    _write_new(key_path, server_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ), 0o600)
    _write_new(cert_path, server.public_bytes(serialization.Encoding.PEM), 0o644)
    validate_tls(cert_path, key_path)
    return cert_path, key_path
