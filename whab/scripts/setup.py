"""Create missing local configuration without replacing user-provided credentials."""

import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
path = ROOT / ".env"
if not path.exists():
    text = (ROOT / ".env.example").read_text()
    text = text.replace("TOKEN_ENCRYPTION_KEY=", f"TOKEN_ENCRYPTION_KEY={secrets.token_hex(32)}")
else:
    text = path.read_text()
    text = re.sub(r"^PORT=3001$", "PORT=5173", text, flags=re.MULTILINE)
path.write_text(text)
os.chmod(path, 0o600)
print("Local configuration is ready. Existing Plaid credentials were preserved.")
