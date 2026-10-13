"""Allowlisted public connection status, including the legacy Plaid response keys."""

import re


def public_bank_status(bank, environment):
    config = bank.config if bank is not None else {}
    missing = config.get("missingConfig", [])
    return {
        "environment": environment,
        "configured": config.get("configured", config.get("plaidConfigured")) is True,
        "liveEnabled": config.get("liveEnabled") is True,
        "missingConfig": [
            value
            for value in missing
            if type(value) is str and re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", value)
        ]
        if type(missing) in {list, tuple}
        else [],
        "enabled": bank is not None,
    }
