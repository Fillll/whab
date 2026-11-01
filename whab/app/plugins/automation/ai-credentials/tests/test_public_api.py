"""Consumers use opaque shared connections without foreign namespace access."""

from dataclasses import replace

import pytest
from whab_plugin_ai_credentials import api

from whab.plugins import StoreError


def test_shared_connection_facade_and_invalid_references(context, monkeypatch):
    vault = api.Vault(context)
    vault.write("openai", {"api_key": "fictional-secret"})
    reference = vault.references()["openai"]
    other_files = {}
    consumer = api.Vault(
        replace(
            context,
            read_file=other_files.get,
            write_file=lambda n, r: other_files.__setitem__(n, r),
        )
    )
    assert consumer.read("openai") == {}
    assert api.credential(consumer, "openai", reference) == "fictional-secret"
    assert other_files == {}
    monkeypatch.setattr(api.providers, "models", lambda *a: [{"id": "fictional"}])
    assert api.models(vault, "openai") == [{"id": "fictional"}]
    monkeypatch.setattr(api.providers, "complete", lambda *a, **k: "fictional-response")
    assert (
        api.complete(
            consumer,
            "openai",
            "model",
            {},
            "guidance",
            instructions="rules",
            connection_id=reference,
        )
        == "fictional-response"
    )
    for provider, key in (("unsupported", reference), ("openai", "invalid")):
        with pytest.raises(StoreError):
            api.connection(consumer, provider, key)



def test_connection_rejects_a_reference_for_another_provider(context):
    vault = api.Vault(context)
    vault.write("openai", {"api_key": "fictional-key"})
    reference = vault.references()["openai"]
    with pytest.raises(StoreError, match="another provider"):
        api.credential(vault, "anthropic", reference)
