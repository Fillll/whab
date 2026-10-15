"""Accounts, transactions, allocations, and the existing CSV interface."""

import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from whab.domain.budget import select_currency
from whab.domain.dates import current_month
from whab.domain.money import currency_code
from whab.domain.transactions import UNSET
from whab.web.csv import safe_cell
from whab.web.schemas import (
    AccountBody,
    AssignmentBody,
    CategoryBody,
    ReviewBody,
    TransactionBody,
    Workspace,
    valid_month,
)


def create_router(store):
    router = APIRouter()

    @router.get("/api/workspace")
    def workspace_data(workspace: Workspace, month: str | None = None, currency: str | None = None):
        month = month or current_month()
        try:
            valid_month(month)
        except ValueError as error:
            raise HTTPException(400, "Invalid month") from error
        data = store.workspace(workspace, month)
        return select_currency(data, currency) if currency is not None else data

    @router.get("/api/export")
    def export(workspace: Workspace, currency: str | None = None):
        if currency is not None:
            currency_code(currency)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "date",
                "account",
                "merchant",
                "amount_cents",
                "category",
                "pending",
                "reviewed",
                "source",
                "note",
                "transfer",
                "currency",
            ]
        )
        accounts = {row["id"]: row["name"] for row in store.accounts(workspace)}
        currencies = {row["id"]: row.get("currency", "USD") for row in store.accounts(workspace)}
        categories = {row["id"]: row["name"] for row in store.categories(workspace)}

        for transaction in store.transactions(workspace):
            if currency is not None and currencies[transaction["accountId"]] != currency:
                continue
            writer.writerow(
                [
                    transaction["date"],
                    safe_cell(accounts.get(transaction["accountId"])),
                    safe_cell(transaction["merchant"]),
                    transaction["amount"],
                    safe_cell(categories.get(transaction["categoryId"])),
                    transaction["pending"],
                    transaction["reviewed"],
                    transaction["source"],
                    safe_cell(transaction["note"]),
                    transaction.get("isTransfer", False),
                    currencies[transaction["accountId"]],
                ]
            )
        return Response(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="whab-{workspace}.csv"'},
        )

    @router.patch("/api/transactions/{transaction_id}")
    def review(transaction_id: str, body: ReviewBody):
        if not body.model_fields_set.intersection({"categoryId", "reviewed"}):
            raise HTTPException(400, "No changes provided")
        category = body.categoryId if "categoryId" in body.model_fields_set else UNSET
        store.edit_transaction(
            body.workspace, transaction_id, category_id=category, reviewed=body.reviewed
        )
        return {"ok": True}

    @router.post("/api/transactions")
    def add_transaction(body: TransactionBody):
        store.add_transaction(body.workspace, body.model_dump(exclude={"workspace"}))
        return {"ok": True}

    @router.put("/api/budget/{category_id}")
    def assignment(category_id: str, body: AssignmentBody):
        store.assign(body.workspace, category_id, body.month, body.assigned)
        return {"ok": True}

    @router.post("/api/categories")
    def category(body: CategoryBody):
        store.add_category(body.workspace, body.model_dump(exclude={"workspace"}))
        return {"ok": True}

    @router.post("/api/accounts")
    def account(body: AccountBody):
        account_id = store.add_account(body.workspace, body.model_dump(exclude={"workspace"}))
        return {"ok": True, "accountId": account_id}

    return router
