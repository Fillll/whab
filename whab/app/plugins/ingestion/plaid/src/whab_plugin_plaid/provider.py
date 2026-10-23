"""Data-only Plaid provider engine; host owns keys and durable financial writes."""

import json

from whab.plugins import ProviderFailure, ProviderWebhook

from .service import PlaidError, PlaidRemoteError, PlaidService


class _SyncData:
    """Detached child-local accumulator, never a native persistence capability."""

    def __init__(self, item_id=None, cursor=None, institution_name='Connected institution'):
        self.item_id, self.cursor, self.institution_name = item_id, cursor, institution_name
        self.status = None

    def get_item(self, item_id):
        if item_id != self.item_id:
            return None
        return {'id': item_id, 'cursor': self.cursor, 'institutionName': self.institution_name}

    def apply_sync(self, item_id, changes, cursor, accounts, historical_complete, *, expected_cursor):
        if item_id != self.item_id or expected_cursor != self.cursor:
            raise ValueError('Invalid detached provider checkpoint.')
        return {'changes': changes, 'cursor': cursor, 'accounts': accounts,
                'historical_complete': historical_complete}

    def set_item_status(self, item_id, status, _error):
        if item_id != self.item_id:
            raise ValueError('Invalid detached provider checkpoint.')
        self.status = status


class PlaidProvider(PlaidService):
    """Reuse transport/mapping/verification with plaintext tokens only during RPC."""

    _requires_encryption = False

    def __init__(self, context, client=None):
        super().__init__(_SyncData(), {**context.config, 'PLAID_ENV': context.environment}, client)
        self.config = {**{key: value for key, value in self.config.items() if key != 'plaidConfigured'},
                       'configured': self.config['plaidConfigured']}
        self._access_token = None

    def require_configured(self):
        if not self.config['configured']:
            raise ProviderFailure('not_configured')

    def token(self, item_id):
        if self._access_token is None or item_id != self.store.item_id:
            raise ProviderFailure('transport')
        return self._access_token

    async def connection_token(self, item_id, access_token):
        self.store, self._access_token = _SyncData(item_id), access_token
        try:
            return await super().link_token(item_id)
        except PlaidError:
            raise ProviderFailure('transport') from None
        finally:
            self._access_token = None

    async def exchange_token(self, public_token):
        self.require_configured()
        try:
            data = await self._call('/item/public_token/exchange', {'public_token': public_token})
            return {'item_id': data['item_id'], 'access_token': data['access_token']}
        except (PlaidRemoteError, KeyError):
            raise ProviderFailure('transport') from None

    async def read_sync(self, item_id, access_token, cursor, institution_name):
        self.store, self._access_token = _SyncData(item_id, cursor, institution_name), access_token
        try:
            return await super().sync(item_id)
        except PlaidError:
            raise ProviderFailure('reconnect' if self.store.status == 'reconnect' else 'transport') from None
        finally:
            self._access_token = None

    async def remove_token(self, access_token):
        self.require_configured()
        try:
            await self._call('/item/remove', {'access_token': access_token})
        except PlaidRemoteError:
            raise ProviderFailure('transport') from None

    async def webhook_event(self, raw, headers):
        verification = next((value for name, value in headers.items() if name.lower() == 'plaid-verification'), None)
        if not await self.verify_webhook(raw, verification):
            return ProviderWebhook(False, 401)
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError):
            return ProviderWebhook(False, 400)
        if not isinstance(event, dict) or not isinstance(event.get('item_id'), str):
            return ProviderWebhook(False, 400)
        error = event.get('error') or {}
        return ProviderWebhook(True, item_id=event['item_id'],
            sync=event.get('webhook_type') == 'TRANSACTIONS' and event.get('webhook_code') == 'SYNC_UPDATES_AVAILABLE',
            reconnect=event.get('webhook_type') == 'ITEM' and (
                event.get('webhook_code') in {'PENDING_DISCONNECT', 'PENDING_EXPIRATION'}
                or event.get('webhook_code') == 'ERROR' and isinstance(error, dict) and error.get('error_code') == 'ITEM_LOGIN_REQUIRED'))
