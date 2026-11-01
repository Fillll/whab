"""Validation for the categorizer's existing portable profiles."""
import json
from uuid import UUID

from whab.plugins import StoreError


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

