"""Owner-only machine credentials, never stored in a portable budget namespace."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from uuid import UUID, uuid4

from whab.plugins import StoreError

LOCK = RLock()
PROVIDERS = {"chatgpt": "OpenAI subscription", "openai": "OpenAI API key",
             "anthropic": "Anthropic API key", "openrouter": "OpenRouter API key"}


def identifier(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except ValueError:
        return False


def document(raw):
    try:
        if len(raw) > 1024 * 1024:
            raise ValueError()
        value = json.loads(raw)
        if type(value) is not dict:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise StoreError("Saved LLM settings are damaged. Preserve the budget and local credentials before restoring them.") from None


class Vault:
    def __init__(self, context):
        self.context = context
        self.root = Path(os.environ.get("LLM_CATEGORIZATION_CREDENTIALS_DIR",
                                        str(Path.home() / ".config/whab/llm-categorization"))).expanduser()

    def references(self):
        raw = self.context.read_file("connections.json")
        values = document(raw) if raw else {}
        if any(key not in PROVIDERS or not identifier(value) for key, value in values.items()):
            raise StoreError("Saved LLM connection references are invalid.")
        return values

    def path(self, key):
        if not identifier(key) or self.root.is_symlink() or (self.root / f"{key}.json").is_symlink():
            raise StoreError("Invalid local LLM credential path.")
        return self.root / f"{key}.json"

    def read(self, provider):
        key = self.references().get(provider)
        path = self.path(key) if key else None
        return document(path.read_bytes()) if path and path.exists() else {}

    def write(self, provider, value):
        if provider not in PROVIDERS:
            raise StoreError("Choose a supported LLM provider.")
        with LOCK:
            refs = self.references()
            key = refs.get(provider, str(uuid4()))
            path = self.path(key)
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.root.chmod(0o700)
            with NamedTemporaryFile(dir=self.root, delete=False) as handle:
                temporary = Path(handle.name)
                try:
                    os.chmod(temporary, 0o600)
                    handle.write(json.dumps(value, allow_nan=False).encode())
                    handle.flush()
                    os.fsync(handle.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            if provider not in refs:
                self.context.write_file("connections.json", json.dumps({**refs, provider: key}).encode())

    def clear(self, provider):
        key = self.references().get(provider)
        if key:
            self.path(key).unlink(missing_ok=True)
