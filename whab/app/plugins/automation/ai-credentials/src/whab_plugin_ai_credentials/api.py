"""Public v1 connection API. Opaque references are portable; secrets stay local.

Consumers may retain a connection UUID returned by the settings transport.
Vault(context) also preserves the original categorizer's connection references.
No consumer imports provider, OAuth or credential-file implementations.
"""

from dataclasses import replace

from whab.plugins import StoreError

from . import providers
from .private import PROVIDERS, Vault, identifier
from .private import document as document

EFFORTS = providers.EFFORTS


def connection(vault, provider, connection_id=""):
    if provider not in PROVIDERS or (connection_id and not identifier(connection_id)):
        raise StoreError("Choose a saved AI provider connection.")
    if not connection_id:
        return vault
    # Only opaque references cross the public plugin boundary.
    import json

    context = replace(
        vault.context, read_file=lambda _: json.dumps({provider: connection_id}).encode()
    )
    return Vault(context)


def credential(vault, provider, connection_id=""):
    return providers.credential(connection(vault, provider, connection_id), provider)


def models(vault, provider):
    return providers.models(vault, provider)


def complete(
    vault, provider, model, data, guidance, effort="default", *, instructions, connection_id=""
):
    return providers.complete(
        connection(vault, provider, connection_id),
        provider,
        model,
        data,
        guidance,
        effort,
        instructions=instructions,
    )


def settings_router(context, *, route_id="ai-credentials", prompt_rules=""):
    """Build the guarded connection UI transport, including the legacy alias."""
    from .routes import router

    return router(context, route_id=route_id, prompt_rules=prompt_rules)
