"""Host-owned HTTPS client transport and retained command outcome state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from hmac import compare_digest
from threading import RLock
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from whab.domain.errors import StoreError
from whab.domain.portable import validate_portable_budget
from whab.domain.transactions import UNSET


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
        if type(token) is not str or not 32 <= len(token) <= 4096 or any(not 33 <= ord(char) <= 126 for char in token):
            raise StoreError("Client plugin requires CLIENT_TOKEN with at least 32 characters.")
        self._client = httpx.Client(
            base_url=self._url, headers={"Authorization": "Bearer " + token},
            timeout=15, follow_redirects=False, trust_env=False, transport=transport,
        )
        self._path = "/remote/v1/budgets/" + quote(budget_id, safe="")
        self._revision = None
        self._pending = None
        self._lock = RLock()
        self._credential = token
        self.operation_scope = None
        self.pending_journal = None
        self._captured = None

    def matches_binding(self, url, token, budget_id):
        """Host-local comparison; neither this proof nor credentials enter IPC."""
        origin, identifier = _connection(url, budget_id)
        return (origin == self._url and identifier == self._budget_id
                and type(token) is str and compare_digest(token, self._credential))

    @contextmanager
    def _admit(self):
        if self.operation_scope is None:
            yield
        else:
            # The host controller pins one immutable declaration and checks its
            # enabled state. Retain that lease until the remote outcome settles.
            with self.operation_scope():
                yield

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
            if operation == "categorization":
                raise StoreError("Category model settings require WHAB 2.19 or newer on the budget host.")
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

    def _query(self, operation, workspace, month=None, *, parameters=None):
        params = {"workspace": workspace}
        if month is not None:
            params["month"] = month
        with self._admit(), self._lock:
            try:
                response = (self._client.post(self._path + "/queries", json={
                    "operation": operation, "workspace": workspace, "parameters": parameters,
                }) if parameters is not None else self._client.get(self._path + "/queries/" + operation, params=params))
            except httpx.HTTPError:
                raise StoreError("Remote budget connection failed.") from None
            return self._response(response)

    def _command(self, operation, workspace, payload):
        with self._admit(), self._lock:
            if self.pending_journal is not None:
                from whab.plugins.effect_store import EffectError

                try:
                    unresolved = self.pending_journal.snapshot()["pending"] is not None
                except EffectError:
                    raise StoreError("The remote outcome journal requires recovery. Reads remain available; do not repeat or discard its change.") from None
                if unresolved or self._captured is not None:
                    raise StoreError("A remote change has an unknown outcome. Reads remain available. Use Finish original request with its exact original change.")
            intent = {"operation": operation, "workspace": workspace, "payload": payload}
            metadata = getattr(self, "_history_context", None)
            # Ordinary commands retain their original wire shape for older hosts.
            # Category provenance already travels in its explicit payload field.
            if metadata and (metadata.get('batchId') or metadata.get('actor', 'User') not in {'User', payload.get('categoryAgent')}):
                intent["history"] = {k: v for k, v in metadata.items() if k in {"actor", "actorKind", "batchId"}}
            if self._pending is not None:
                if {key: value for key, value in self._pending.items() if key not in {"request_id", "expected_revision"}} != intent:
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
            if self.pending_journal is not None:
                try:
                    self.pending_journal.begin(body)
                except Exception:
                    raise StoreError("The remote intent could not be durably recorded. No new request was sent; recover its private outcome journal.") from None
                return self._send_original(body)
            try:
                response = self._client.post(self._path + "/commands", json=body)
            except httpx.HTTPError:
                raise StoreError("Remote change result is unknown. Retry the same change to confirm it.") from None
            data = self._response(response, command=True, operation=operation)
            self._pending = None
            return data

    def _send_original(self, body):
        from whab.plugins.remote_selection import command_fingerprint

        try:
            response = self._client.post(self._path + "/commands", json=body)
        except httpx.HTTPError:
            raise StoreError("Remote change result is unknown. Reads remain available. Finish only its exact original request; do not discard or reconnect.") from None
        try:
            data = self._response(response, command=True, operation=body["operation"])
        except StoreError:
            # In particular 409 after a server restart cannot prove whether an
            # earlier attempt committed before its receipt was lost.
            self._pending = deepcopy(body)
            raise StoreError("The original remote outcome remains unknown. Reads remain available; preserve this request and obtain independent authority evidence.") from None
        self._captured = (command_fingerprint(body), data, self._revision)
        try:
            self.pending_journal.acknowledge(body, self._revision, data)
        except Exception:
            raise StoreError("The remote response was captured but its local acknowledgment requires recovery. Finish the original request; do not create a new one.") from None
        self._pending, self._captured = None, None
        return data

    def finish_original(self, operation, workspace, payload, history=None):
        """Explicit completion, never a receipt lookup or automatic retry.

        The host validates the public command first. Reconstruct only a body
        matching the durable fingerprint, original request ID and original CAS.
        A response rejection never discards or refreshes that uncertain intent.
        """
        from whab.plugins.remote_selection import command_fingerprint

        with self._admit(), self._lock:
            if self.pending_journal is None:
                raise StoreError("This legacy client has no durable original request to finish.")
            try:
                record = self.pending_journal.snapshot()
            except Exception:
                raise StoreError("Recover the private remote outcome journal before finishing its request.") from None
            pending = record["pending"]
            known = pending or (record["acknowledged"]["request"] if record["acknowledged"] else None)
            if known is None:
                raise StoreError("There is no retained remote request to finish.")
            body = {"operation": operation, "workspace": workspace, "payload": deepcopy(payload),
                    "request_id": known["request_id"], "expected_revision": known["expected_revision"]}
            if history is not None:
                body["history"] = history
            if command_fingerprint(body) != known["fingerprint"]:
                raise StoreError("This change does not match the exact original remote request. Preserve it; do not substitute a new request or revision.")
            if pending is None:
                self._pending, self._captured = None, None
                return {"complete": True, "acknowledged": True}
            if self._captured is not None and self._captured[0] == known["fingerprint"]:
                _, data, revision = self._captured
                try:
                    self.pending_journal.acknowledge(body, revision, data)
                except Exception:
                    raise StoreError("The captured remote acknowledgment still requires recovery. Do not resend or discard the original change.") from None
                self._pending, self._captured = None, None
            else:
                self._send_original(body)
            # Only completion status is retained across host restart. Never
            # fabricate an original financial return value from metadata hashes.
            return {"complete": True, "acknowledged": True}

    def accounts(self, workspace):
        return self._query("accounts", workspace)

    def category_tree(self, workspace):
        return self._query("category_tree", workspace)

    def categories(self, workspace):
        return self._query("categories", workspace)

    def transaction_classification_status(self, workspace):
        # Existing category discovery works on older authorities too; only the
        # explicit canonical kind opts an authority into the new invariant.
        rows = self.categories(workspace)
        return {"supported": type(rows) is list and any(type(row) is dict and row.get("systemKind") in {"income", "transfer"} for row in rows), "pending": False}

    def migrate_transaction_classifications(self, workspace):
        # The authority migrates before returning its category discovery. Never
        # send a second migration intent across an uncertain remote connection.
        if not self.transaction_classification_status(workspace)["supported"]:
            raise StoreError("Update the remote budget authority to WHAB 2.73 transaction classification support.")
        return {"changed": False}

    def features(self, workspace):
        return self._query("features", workspace)

    def describe_operations(self, workspace):
        return self._query("describe", workspace)

    def feature_query(self, workspace, payload):
        return self._query("feature", workspace, parameters=payload)

    def feature_command(self, workspace, payload):
        return self._command("feature", workspace, payload)

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

    def add_category_group(self, workspace, name):
        return self._command("add_category_group", workspace, {"name": name})

    def move_category(self, workspace, category_id, parent_id):
        return self._command("move_category", workspace, {"categoryId": category_id, "parentId": parent_id})

    def supports_atomic_bulk_edits(self):
        return True

    def preview_bulk_edits(self, workspace, operation, edits):
        return self._query('preview_bulk_edits', workspace, parameters={'operation': operation, 'edits': edits})

    def edit_transactions(self, workspace, edits):
        return self._command('edit_transactions', workspace, {'edits': edits})

    def classify_accounts(self, workspace, edits):
        return self._command('classify_accounts', workspace, {'edits': edits})

    def supports_category_renaming(self):
        return True

    def rename_category(self, workspace, category_id, name):
        return self._command("rename_category", workspace, {"categoryId": category_id, "name": name})

    def supports_category_deletion(self):
        """The addressed authority enforces deletion and audit support."""
        return True

    def delete_category(self, workspace, category_id):
        return self._command('delete_category', workspace, {'categoryId': category_id})

    def category_deletion_status(self, workspace, category_id):
        return self._query('category_deletion_status', workspace, parameters={'categoryId': category_id})

    def delete_category_group(self, workspace, parent_id):
        return self._command('delete_category_group', workspace, {'parentId': parent_id})

    def add_category(self, workspace, category):
        return self._command("add_category", workspace, category)

    def add_transaction(self, workspace, transaction, source="manual"):
        if source != "manual":
            raise StoreError("Remote clients only create manual transactions.")
        return self._command("add_transaction", workspace, transaction)

    def category_suggestions(self, workspace):
        try:
            return self._query("category_suggestions", workspace)
        except StoreError:
            # Existing ledger browsing remains usable with an older authority.
            return {}

    def categorize(self, workspace, payload):
        return self._command("categorization", workspace, payload)

    def edit_transaction_details(self, workspace, transaction_id, changes):
        return self._command("edit_transaction", workspace, {"transactionId": transaction_id, **changes})

    def supports_account_lifecycle(self):
        """The addressed authority validates durable lifecycle support."""
        return True

    def supports_category_agent(self):
        """Forward explicitly attributed edits; older authorities reject the field."""
        return True

    def edit_transaction(self, workspace, transaction_id, category_id=UNSET, reviewed=None, note=UNSET, suggestion_id=None):
        payload = {"transactionId": transaction_id}
        if suggestion_id is not None:
            payload["suggestionId"] = suggestion_id
        if category_id is not UNSET:
            payload["categoryId"] = category_id
        if reviewed is not None:
            payload["reviewed"] = reviewed
        if note is not UNSET:
            payload["note"] = note
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

    @contextmanager
    def history_scope(self, metadata):
        with self._lock:
            previous = getattr(self, '_history_context', None)
            self._history_context = metadata
            try:
                yield
            finally:
                self._history_context = previous

    def budget_history(self, workspace, limit=50, before=None, target=None, snapshot=False):
        return self._query('history', workspace, parameters={'limit': limit, 'before': before, 'target': target, 'snapshot': snapshot})

    def history_preview(self, workspace, action, target=None):
        return self._query('history_preview', workspace, parameters={'action': action, 'target': target})

    def history_apply(self, workspace, action, target, fingerprint):
        return self._command('history_apply', workspace, {'action': action, 'target': target, 'fingerprint': fingerprint})
