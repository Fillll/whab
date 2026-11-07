"""Optional provider reports remain bounded and compatible with old engines."""

from dataclasses import replace

import pytest

from tests.domain.test_investments import snapshot
from tests.plugins.test_provider_worker import Provider, call, opened, plugin
from whab.plugins import BankAdapter
from whab.plugins.provider_worker import ProviderDispatcher, read_provider_result
from whab.plugins.wire import WireError
from whab.plugins.worker_adapters import adapter_info, validate_adapter_info


class InvestmentProvider(Provider):
    async def read_investments(self, item_id, access_token, start_date, end_date, account_id):
        assert item_id == "item" and access_token == "fictional-access"
        return snapshot()


def investment_plugin(factory=InvestmentProvider):
    return replace(plugin(factory), bank=BankAdapter(factory, supports_investments=True))


def test_optional_provider_method_survives_typed_worker_and_metadata_transport():
    descriptor = investment_plugin()
    info = adapter_info(descriptor)
    assert info["bank"]["supports_investments"] is True
    validate_adapter_info(info)
    worker = ProviderDispatcher(descriptor)
    try:
        opened(worker)
        value = call(
            worker,
            "read_investments",
            {
                "item_id": "item",
                "access_token": "fictional-access",
                "start_date": "2024-11-07",
                "end_date": "2026-11-07",
                "account_id": None,
            },
        )
        assert value["positions"][0]["value"] == 125000
        assert (
            read_provider_result("read_investments", value, provider_id="fictional-bank") == value
        )
    finally:
        worker.close()


def test_declared_investments_requires_the_method_but_old_engines_do_not():
    worker = ProviderDispatcher(investment_plugin(Provider))
    try:
        with pytest.raises(WireError):
            opened(worker)
    finally:
        worker.close()
    with pytest.raises(ValueError):
        BankAdapter(Provider, supports_investments=1)


@pytest.mark.parametrize(
    "change",
    [
        {"access_token": ""},
        {"account_id": ""},
        {"start_date": "2027-01-01"},
        {"extra": True},
    ],
)
def test_invalid_report_requests_cannot_reach_the_engine(change):
    worker = ProviderDispatcher(investment_plugin())
    try:
        opened(worker)
        value = {
            "item_id": "item",
            "access_token": "fictional-access",
            "start_date": "2024-11-07",
            "end_date": "2026-11-07",
            "account_id": None,
            **change,
        }
        with pytest.raises(WireError):
            call(worker, "read_investments", value)
    finally:
        worker.close()


def test_old_descriptor_does_not_advertise_or_accept_investment_queries():
    descriptor = plugin()
    assert "supports_investments" not in adapter_info(descriptor)["bank"]
    worker = ProviderDispatcher(descriptor)
    try:
        opened(worker)
        with pytest.raises(WireError):
            call(
                worker,
                "read_investments",
                {
                    "item_id": "item",
                    "access_token": "access",
                    "start_date": "2024-11-07",
                    "end_date": "2026-11-07",
                    "account_id": None,
                },
            )
    finally:
        worker.close()
