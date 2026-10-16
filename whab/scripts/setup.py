"""Create missing local configuration without replacing user-provided credentials."""

import os
import re
import secrets
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from dotenv import dotenv_values

from whab.tls import prepare_local_tls

ROOT = Path(__file__).resolve().parent.parent
home = Path(os.environ.get("WHAB_HOME", ROOT)).expanduser().resolve()
home.mkdir(parents=True, exist_ok=True, mode=0o700)
path = home / ".env"
if not path.exists():
    text = (ROOT / ".env.example").read_text()
    text = text.replace("TOKEN_ENCRYPTION_KEY=", f"TOKEN_ENCRYPTION_KEY={secrets.token_hex(32)}")
else:
    text = path.read_text()
    text = re.sub(r"^PORT=3001$", "PORT=5173", text, flags=re.MULTILINE)
path.write_text(text)
os.chmod(path, 0o600)
print("Local configuration is ready. Existing Plaid credentials were preserved.")
values = {**dotenv_values(path), **os.environ}
try:
    certificate, _ = prepare_local_tls(
        home, values.get("WHAB_TLS_CERT_FILE"), values.get("WHAB_TLS_KEY_FILE"),
    )
except (ValueError, OSError):
    raise SystemExit("HTTPS setup could not complete. Preserve existing TLS files and configure a valid certificate/key pair.") from None
print(f"HTTPS certificate: {certificate}")
authority = home / "tls" / "ca.crt"
trusted_authority = None
if authority.exists():
    try:
        candidate = x509.load_pem_x509_certificate(authority.read_bytes())
        leaf = x509.load_pem_x509_certificate(certificate.read_bytes())
        leaf.verify_directly_issued_by(candidate)
        trusted_authority = candidate
    except (OSError, ValueError, InvalidSignature):
        pass
if trusted_authority is not None:
    fingerprint = trusted_authority.fingerprint(hashes.SHA256()).hex(":")
    print(f"Local CA certificate: {authority}")
    print(f"CA SHA-256 fingerprint: {fingerprint}")
    print("Manually trust this CA for SSL on this device before opening WHAB. On macOS, open the CA certificate in Keychain Access and set its SSL trust to Always Trust.")
else:
    print("Ensure your supplied certificate is trusted by your browser and matches the hostname you use.")
print("Setup does not change your operating system's trust settings. Keep TLS private keys on this device.")
