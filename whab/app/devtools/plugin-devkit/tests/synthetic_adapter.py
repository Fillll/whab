"""Reviewed fictional double for detecting defects in the kit itself.

This intentionally small adapter models only the exercised public ledger calls.
It is not a runtime plugin or an example of safe storage persistence.
"""

import json
from copy import deepcopy
from uuid import uuid4

from whab.plugins import StoreError


class SyntheticLedger:
    def __init__(self, context):
        self.path = context.home / f"synthetic-{context.environment}.json"
        self.values = json.loads(self.path.read_text()) if self.path.exists() else {
            name: {"accounts": {}, "categories": {}, "transactions": {}}
            for name in ("personal", "demo")
        }
        self.closed = False

    def close(self):
        self.closed = True

    def _save(self):
        self.path.write_text(json.dumps(self.values))

    def accounts(self, workspace):
        return deepcopy(list(self.values[workspace]["accounts"].values()))

    def categories(self, workspace):
        return deepcopy(list(self.values[workspace]["categories"].values()))

    def transactions(self, workspace):
        return deepcopy(list(self.values[workspace]["transactions"].values()))

    def workspace(self, workspace, month):
        currencies = {row["currency"] for row in self.accounts(workspace)} | {"USD"}
        accounts = {row["id"]: row for row in self.accounts(workspace)}
        return {"month": month, "summariesByCurrency": {
            currency: {"spent": sum(
                row["amount"] for row in self.transactions(workspace)
                if accounts[row["accountId"]]["currency"] == currency
            )} for currency in sorted(currencies)
        }}

    def add_account(self, workspace, record):
        if type(record["balance"]) is not int:
            raise StoreError("Invalid synthetic money.")
        identifier = str(uuid4())
        self.values[workspace]["accounts"][identifier] = {
            **record, "id": identifier, "currency": record.get("currency", "USD"),
        }
        self._save()
        return identifier

    def add_category(self, workspace, record):
        identifier = str(uuid4())
        self.values[workspace]["categories"][identifier] = {
            **record, "id": identifier, "currency": record.get("currency", "USD"),
        }
        self._save()
        return identifier

    def add_transaction(self, workspace, record):
        rows = self.values[workspace]
        account = rows["accounts"][record["accountId"]]
        category = rows["categories"][record["categoryId"]]
        if category["currency"] != account["currency"]:
            raise StoreError("Synthetic currency mismatch.")
        identifier = record.get("id") or str(uuid4())
        rows["transactions"][identifier] = {
            **record, "id": identifier, "reviewed": False, "note": "",
        }
        account["balance"] -= record["amount"]
        self._save()
        return identifier

    def edit_transaction(self, workspace, transaction, category_id=None, reviewed=None, note=None):
        record = self.values[workspace]["transactions"][transaction]
        if reviewed is not None:
            record["reviewed"] = reviewed
        if note is not None:
            record["note"] = note
        self._save()

    def assign(self, workspace, category_id, month, assigned):
        if category_id not in self.values[workspace]["categories"]:
            raise StoreError("Synthetic category missing.")
