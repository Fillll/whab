"""Initialize a fresh CLI runtime without changing an existing installation."""

import os
import secrets
from pathlib import Path

from dotenv import dotenv_values

from whab.tls import prepare_local_tls


def initialize_home(home):
    home = Path(home).expanduser().resolve()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = home / ".env"
    if not path.exists():
        existing = (home / "data").exists() or (home / "budgets").exists()
        if existing:
            raise ValueError("This existing runtime has no configuration. Restore its original configuration and encryption key before starting.")
        text = f"TOKEN_ENCRYPTION_KEY={secrets.token_hex(32)}\nWHAB_UI=\nWHAB_PLUGINS=\n"
        # Exclusive creation refuses to overwrite another initializer's result.
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w") as output:
                output.write(text)
    values = {**dotenv_values(path), **os.environ}
    prepare_local_tls(home, values.get("WHAB_TLS_CERT_FILE"), values.get("WHAB_TLS_KEY_FILE"))
