"""Local configuration. Secret values are never included in public status responses."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database_path: Path
    port: int
    plaid_env: str
    plaid_client_id: str = field(repr=False)
    plaid_secret: str = field(repr=False)
    encryption_key: str = field(repr=False)
    allow_live: bool
    webhook_url: str
    redirect_uri: str

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env", override=False)
        environment = os.getenv("PLAID_ENV", "sandbox")
        if environment not in {"sandbox", "production"}:
            raise ValueError("PLAID_ENV must be sandbox or production.")
        port = int(os.getenv("PORT", "5173"))
        if not 1024 <= port <= 65535:
            raise ValueError("PORT must be between 1024 and 65535.")
        return cls(
            # Preserve legacy filenames so a rename never starts an empty budget.
            database_path=ROOT / "data" / f"ihab-{environment}.sqlite",
            port=port,
            plaid_env=environment,
            plaid_client_id=os.getenv("PLAID_CLIENT_ID", ""),
            plaid_secret=os.getenv("PLAID_SECRET", ""),
            encryption_key=os.getenv("TOKEN_ENCRYPTION_KEY", ""),
            allow_live=os.getenv("ALLOW_LIVE_PLAID", "false").lower() == "true",
            webhook_url=os.getenv("PLAID_WEBHOOK_URL", ""),
            redirect_uri=os.getenv("PLAID_REDIRECT_URI", ""),
        )

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
