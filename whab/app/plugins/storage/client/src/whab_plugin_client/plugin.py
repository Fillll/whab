"""HTTPS client for the public command protocol, with safe explicit retries."""

import json
from collections.abc import Mapping
from copy import deepcopy
from threading import RLock
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from whab.domain.portable import validate_portable_budget
from whab.plugins import UNSET, Plugin, StorageContext, StoreError


def _connection(url, budget_id):
    try:
        if type(url) is not str or len(url) > 4096 or any(character.isspace() for character in url):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment
        ):
            raise ValueError
        parsed.port  # Validate malformed or out-of-range ports before constructing a client.
        origin = str(httpx.URL(url).copy_with(path=""))
    except (ValueError, httpx.InvalidURL):
        raise StoreError("Client plugin requires an HTTPS origin in CLIENT_URL.") from None
    if type(budget_id) is not str or not budget_id or len(budget_id) > 200 or "\x00" in budget_id:
        raise StoreError("Client plugin requires a valid CLIENT_BUDGET_ID.")
    return origin, budget_id


def _connection_file(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        if type(raw) is not bytes or len(raw) > 32768:
            raise ValueError
        value = json.loads(raw, object_pairs_hook=unique)
        if (
            type(value) is not dict or value.keys() != {"formatVersion", "url", "budgetId"}
            or type(value["formatVersion"]) is not int or value["formatVersion"] != 1
        ):
            raise ValueError
    except (ValueError, UnicodeError):
        raise StoreError("This file does not contain a supported remote budget connection.") from None
    return _connection(value["url"], value["budgetId"])


def connection_configuration(files):
    """Describe a data-only connection so the host can bind credentials explicitly."""
    if not isinstance(files, Mapping) or files.keys() != {"connection.json"}:
        raise StoreError("Remote budget files require exactly one connection.json file.")
    origin, budget_id = _connection_file(files["connection.json"])
    return {"CLIENT_URL": origin, "CLIENT_BUDGET_ID": budget_id}


class RemoteStore:
    """Storage compatibility for public ledger operations, never private bank state."""

    def __init__(self, url: str, token: str, budget_id: str, *, transport=None):
        self._url, self._budget_id = _connection(url, budget_id)
        if len(token) < 32:
            raise StoreError("Client plugin requires CLIENT_TOKEN with at least 32 characters.")
        self._client = httpx.Client(
            base_url=self._url, headers={"Authorization": "Bearer " + token},
            timeout=15, follow_redirects=False, trust_env=False, transport=transport,
        )
        self._path = "/remote/v1/budgets/" + quote(budget_id, safe="")
        self._revision = None
        self._pending = None
        self._lock = RLock()

    def close(self):
        self._client.close()

    def _response(self, response, *, command=False, operation=None):
        if response.status_code == 409:
            if command:
                self._pending = None
            self._revision = None
            raise StoreError("Remote budget changed. Refresh and review the change before retrying.")
        if response.status_code in {400, 401, 403, 404, 422}:
            if command:
                self._pending = None
            if operation in {"assign_next_month", "set_rollover_mode"} and response.status_code in {400, 404, 422}:
                raise StoreError("Remote server rejected this planning change. Check the inputs and update the server if it does not support this feature.")
            raise StoreError("Remote budget request rejected. Check connection settings and inputs.")
        if response.status_code != 200:
            raise StoreError("Remote budget is unavailable. Retry the same change to confirm its result.")
        try:
            body = response.json()
            if (
                type(body) is not dict or body.get("budgetId") != self._budget_id
                or type(body.get("revision")) is not str or not 1 <= len(body["revision"]) <= 200
                or "data" not in body
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise StoreError("Remote budget returned an invalid response.") from None
        self._revision = body["revision"]
        return body["data"]

    def _query(self, operation, workspace, month=None):
        params = {"workspace": workspace}
        if month is not None:
            params["month"] = month
        with self._lock:
            try:
                response = self._client.get(self._path + "/queries/" + operation, params=params)
            except httpx.HTTPError:
                raise StoreError("Remote budget connection failed.") from None
            return self._response(response)

    def _command(self, operation, workspace, payload):
        with self._lock:
            intent = {"operation": operation, "workspace": workspace, "payload": payload}
            if self._pending is not None:
                if any(self._pending[key] != value for key, value in intent.items()):
                    raise StoreError("Confirm the previous remote change by retrying it before making another.")
                body = self._pending
            else:
                if self._revision is None:
                    self._query("workspace", workspace)
                body = {
                    **deepcopy(intent), "request_id": str(uuid4()),
                    "expected_revision": self._revision,
                }
                self._pending = body
            try:
                response = self._client.post(self._path + "/commands", json=body)
            except httpx.HTTPError:
                raise StoreError("Remote change result is unknown. Retry the same change to confirm it.") from None
            data = self._response(response, command=True, operation=operation)
            self._pending = None
            return data

    def accounts(self, workspace):
        return self._query("accounts", workspace)

    def categories(self, workspace):
        return self._query("categories", workspace)

    def transactions(self, workspace, month=None):
        return self._query("transactions", workspace, month)

    def workspace(self, workspace, month=None):
        return self._query("workspace", workspace, month)

    def export_budget(self, workspace="personal"):
        return validate_portable_budget(self._query("portable", workspace))

    def export_budget_files(self, workspace="personal"):
        """A remote budget file is a connection reference, with no ledger download."""
        if workspace != "personal":
            raise StoreError("Remote budget files require the personal workspace.")
        return {"connection.json": json.dumps({
            "formatVersion": 1, "url": self._url, "budgetId": self._budget_id,
        }).encode("utf-8")}

    def restore_budget_files(self, workspace, files):
        """Validate file identity against credentials the host has already bound.

        Imported connection data never redirects a machine's existing token.
        The host can separately unlock credentials encrypted for this target.
        """
        if workspace != "personal":
            raise StoreError("Remote budget files require the personal workspace.")
        connection = connection_configuration(files)
        if connection["CLIENT_URL"] != self._url or connection["CLIENT_BUDGET_ID"] != self._budget_id:
            raise StoreError("This connection does not match the configured remote budget. Configure credentials for this exact server and budget before opening it.")

    def prepare_budget_files(self, workspace, files, *, existing=False):
        """Attach a reference without contacting or replacing the remote ledger."""
        self.restore_budget_files(workspace, files)

    def add_account(self, workspace, account):
        return self._command("add_account", workspace, account)

    def update_account(self, workspace, account_id, changes):
        return self._command("update_account", workspace, {**changes, "accountId": account_id})

    def add_category(self, workspace, category):
        return self._command("add_category", workspace, category)

    def add_transaction(self, workspace, transaction, source="manual"):
        if source != "manual":
            raise StoreError("Remote clients only create manual transactions.")
        return self._command("add_transaction", workspace, transaction)

    def edit_transaction(self, workspace, transaction_id, category_id=UNSET, reviewed=None):
        payload = {"transactionId": transaction_id}
        if category_id is not UNSET:
            payload["categoryId"] = category_id
        if reviewed is not None:
            payload["reviewed"] = reviewed
        return self._command("edit_transaction", workspace, payload)

    def assign(self, workspace, category_id, month, assigned):
        return self._command("assign", workspace, {
            "categoryId": category_id, "month": month, "assigned": assigned,
        })

    def assign_next_month(self, workspace, month, currency, assigned):
        return self._command("assign_next_month", workspace, {
            "month": month, "currency": currency, "assigned": assigned,
        })

    def set_rollover_mode(self, workspace, month, currency, mode):
        return self._command("set_rollover_mode", workspace, {
            "month": month, "currency": currency, "mode": mode,
        })

    def items(self):
        return []

    def _private(self, *args, **kwargs):
        raise StoreError("Bank connections run on the budget server. Disable bank plugins on this client.")

    get_item = save_item = set_item_status = disconnect_item = apply_sync = _private


def create_storage(context: StorageContext) -> RemoteStore:
    return RemoteStore(
        context.config.get("CLIENT_URL", ""), context.config.get("CLIENT_TOKEN", ""),
        context.config.get("CLIENT_BUDGET_ID", ""),
    )


plugin = Plugin(
    id="client", display_name="Remote budget client", storage=create_storage,
    budget_connection=connection_configuration,
)
