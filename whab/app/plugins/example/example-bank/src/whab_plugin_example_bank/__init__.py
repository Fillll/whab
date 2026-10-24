"""Offline provider normalization; host owns encryption, continuations and writes."""

from copy import deepcopy

from whab.plugins import (
    BankAdapter,
    BankProviderContext,
    Capability,
    Plugin,
    PluginContract,
    ProviderFailure,
    ProviderWebhook,
)

ITEM = "fictional-item"
ACCOUNT = "fictional-checking"
PENDING = "fictional-pending"
POSTED = "fictional-posted"
LINK_TOKEN = "fictional-link-token"
PUBLIC_TOKEN = "fictional-public-token"
_ACCESS = "fictional-access-token"


class OfflineTransport:
    """A deterministic mock transport. There is no URL or network implementation.

    An actual adapter can replace this object with its reviewed provider client.
    Provider responses still pass through the host's bounded DTO validation.
    """

    async def request(self, operation, values):
        if operation == "link":
            return {"link_token": LINK_TOKEN}
        if operation == "exchange":
            if values["public_token"] != PUBLIC_TOKEN:
                raise ProviderFailure("transport")
            return {"item_id": ITEM, "access_token": _ACCESS}
        if operation == "sync":
            cursor = values["cursor"]
            if cursor not in {None, "pending-v1", "posted-v1"}:
                raise ProviderFailure("reconnect")
            pending = cursor is None
            added = [] if cursor == "posted-v1" else [{
                "id": PENDING if pending else POSTED, "accountId": ACCOUNT,
                "date": "2026-10-01", "merchant": "Fictional grocer", "amount": 725,
                "suggestedCategoryId": None, "pending": pending, "source": "example-bank",
                "inferredTransfer": False, "pendingTransactionId": None if pending else PENDING,
            }]
            return {"changes": {"added": added, "modified": [], "removed": [PENDING] if cursor == "pending-v1" else []},
                    "cursor": "pending-v1" if pending else "posted-v1", "accounts": [{
                        "id": ACCOUNT, "itemId": ITEM, "name": "Fictional checking",
                        "institution": "Fictional Bank", "type": "depository", "currency": "USD",
                        "source": "example-bank", "balance": 10000, "mask": "0000",
                    }], "historical_complete": True}
        if operation == "remove":
            return None
        raise ProviderFailure("transport")

    async def close(self):
        return None


class FictionalProvider:
    """Pure engine with injectable async transport and no host/backend handles."""

    def __init__(self, context: BankProviderContext, transport=None):
        self.transport = transport if transport is not None else OfflineTransport()
        self.closed = False
        self.config = {"environment": context.environment, "configured": context.environment == "sandbox",
                       "liveEnabled": False, "missingConfig": [] if context.environment == "sandbox" else ["SANDBOX_ONLY"]}

    def _ready(self):
        if self.closed or not self.config["configured"]:
            raise ProviderFailure("not_configured")

    def _connection(self, item_id, access_token):
        if item_id != ITEM or access_token != _ACCESS:
            raise ProviderFailure("reconnect")

    async def connection_token(self, item_id, access_token):
        self._ready()
        if item_id is not None or access_token is not None:
            self._connection(item_id, access_token)
        return (await self.transport.request("link", {"item_id": item_id, "access_token": access_token}))["link_token"]

    async def exchange_token(self, public_token):
        self._ready()
        return deepcopy(await self.transport.request("exchange", {"public_token": public_token}))

    async def read_sync(self, item_id, access_token, cursor, institution_name):
        self._ready()
        self._connection(item_id, access_token)
        return deepcopy(await self.transport.request("sync", {"cursor": cursor}))

    async def remove_token(self, access_token):
        self._ready()
        self._connection(ITEM, access_token)
        await self.transport.request("remove", {"access_token": access_token})

    async def webhook_event(self, raw, headers):
        # No fictional unauthenticated request may trigger financial work.
        return ProviderWebhook(False, 401)

    async def close(self):
        if not self.closed:
            await self.transport.close()
            self.closed = True


plugin = Plugin("example-bank", "Offline fictional bank", bank=BankAdapter(FictionalProvider),
    contract=PluginContract(lifecycle="live", authority="local", provides=(
        Capability("ingestion.bank"), Capability("lifecycle.background"),
    )))
