"""Local configuration. Secret values are never included in public status responses."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Runtime files belong to the working directory, never to site-packages or src/.
# WHAB_HOME supports launching an installed wheel from elsewhere.
ROOT = Path.cwd()


@dataclass(frozen=True)
class Settings:
    home: Path
    port: int
    plaid_env: str
    plaid_client_id: str = field(repr=False)
    plaid_secret: str = field(repr=False)
    encryption_key: str = field(repr=False)
    allow_live: bool
    webhook_url: str
    redirect_uri: str
    plugins: tuple[str, ...] = ("plaid",)
    storage: str = "sqlite"
    ui: str = "web"

    @classmethod
    def load(cls):
        home = Path(os.environ.get("WHAB_HOME", ROOT)).expanduser().resolve()
        load_dotenv(home / ".env", override=False)
        environment = os.getenv("WHAB_ENV", os.getenv("PLAID_ENV", "sandbox"))
        if environment not in {"sandbox", "production"}:
            raise ValueError("WHAB_ENV (or legacy PLAID_ENV) must be sandbox or production.")
        port = int(os.getenv("PORT", "5173"))
        if not 1024 <= port <= 65535:
            raise ValueError("PORT must be between 1024 and 65535.")
        return cls(
            home=home,
            port=port,
            plaid_env=environment,
            plaid_client_id=os.getenv("PLAID_CLIENT_ID", ""),
            plaid_secret=os.getenv("PLAID_SECRET", ""),
            encryption_key=os.getenv("TOKEN_ENCRYPTION_KEY", ""),
            allow_live=os.getenv("ALLOW_LIVE_PLAID", "false").lower() == "true",
            webhook_url=os.getenv("PLAID_WEBHOOK_URL", ""),
            redirect_uri=os.getenv("PLAID_REDIRECT_URI", ""),
            plugins=tuple(
                name.strip()
                for name in os.getenv("WHAB_PLUGINS", "plaid").split(",")
                if name.strip()
            ),
            storage=os.getenv("WHAB_STORAGE", "sqlite").strip(),
            ui=os.getenv("WHAB_UI", "web").strip(),
        )

    def enabled_plugins(self):
        """The selected storage is required even when optional plugins are disabled."""
        enabled = self.plugins
        for name in (self.storage, self.ui):
            if name not in enabled and (name or name == self.storage):
                enabled = (*enabled, name)
        return enabled

    def storage_configuration(self):
        """Storage factories receive only their own prefixed configuration."""
        prefix = self.storage.upper().replace("-", "_") + "_"
        return {key: value for key, value in os.environ.items() if key.startswith(prefix)}

    def bank_configuration(self, plugin_id):
        """Only the selected provider's environment namespace enters its factory."""
        if plugin_id == "plaid":
            return self.plaid_environment()
        prefix = plugin_id.upper().replace("-", "_") + "_"
        return {key: value for key, value in os.environ.items() if key.startswith(prefix)}

    def plaid_environment(self):
        """Pass credentials to the Plaid service internally, without serialization."""
        return {
            "PLAID_ENV": self.plaid_env,
            "PLAID_CLIENT_ID": self.plaid_client_id,
            "PLAID_SECRET": self.plaid_secret,
            "TOKEN_ENCRYPTION_KEY": self.encryption_key,
            "ALLOW_LIVE_PLAID": "true" if self.allow_live else "false",
            "PLAID_WEBHOOK_URL": self.webhook_url,
            "PLAID_REDIRECT_URI": self.redirect_uri,
        }
