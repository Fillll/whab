"""Accounts, transactions, allocations, and the existing CSV interface."""

import csv
import io
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from whab.domain.budget import select_currency
from whab.domain.dates import current_month
from whab.domain.money import currency_code
from whab.domain.transactions import UNSET
from whab.services.ledger import RequestConflict, RevisionConflict
from whab.web.csv import safe_cell
from whab.web.schemas import (
    AccountBody,
    AccountClassificationBody,
    AssignmentBody,
    BulkReviewBody,
    CategoryBody,
    NextMonthAssignmentBody,
    ReviewBody,
    RolloverBody,
    TransactionBody,
    Workspace,
    valid_month,
)


def create_router(store):
    router = APIRouter()

    class CategorySelectionBody(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        workspace: Workspace
        transactionIds: list[str] = Field(min_length=1, max_length=100)
        categoryId: str | None

    class CategorySelectionSaveBody(CategorySelectionBody):
        revision: str
        requestId: str

    def category_edits(body):
        return [{'transactionId': identifier, 'categoryId': body.categoryId} for identifier in body.transactionIds]

    @router.post('/api/transactions/categories/preview')
    def preview_categories(body: CategorySelectionBody):
        return store.query('preview_bulk_edits', body.workspace, parameters={
            'operation': 'edit_transactions', 'edits': category_edits(body),
        })

    @router.post('/api/transactions/categories')
    def save_categories(body: CategorySelectionSaveBody):
        try:
            return store.command('edit_transactions', body.workspace, {'edits': category_edits(body)},
                                 request_id=body.requestId, expected_revision=body.revision)
        except RevisionConflict as error:
            return JSONResponse({'error': str(error), 'code': 'category_revision_conflict'}, status_code=409)
        except RequestConflict as error:
            return JSONResponse({'error': str(error), 'code': 'category_request_conflict'}, status_code=409)

    class TransferReadBody(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        workspace: Workspace
        operation: Literal['transfer_candidates', 'preview_transfer_match']
        parameters: dict

    class TransferApplyBody(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        workspace: Workspace
        transactionIds: list[str]
        action: Literal['match', 'unmatch']
        fingerprint: str
        revision: str
        requestId: str

    @router.post('/api/transfers/query')
    def transfer_query(body: TransferReadBody):
        return store.query(body.operation, body.workspace, parameters=body.parameters)

    @router.post('/api/transfers/apply')
    def transfer_apply(body: TransferApplyBody):
        try:
            return store.command('match_transfer', body.workspace, {
                'transactionIds': body.transactionIds, 'action': body.action, 'fingerprint': body.fingerprint,
            }, request_id=body.requestId, expected_revision=body.revision)
        except RevisionConflict as error:
            return JSONResponse({'error': str(error), 'code': 'transfer_revision_conflict'}, status_code=409)

    class HistoryPreviewBody(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        workspace: Workspace
        action: str
        target: str | None = None

    class HistoryApplyBody(HistoryPreviewBody):
        fingerprint: str
        revision: str
        requestId: str

    @router.get('/api/history')
    def budget_history(workspace: Workspace, limit: int = 50, before: str | None = None, target: str | None = None, snapshot: bool = False):
        return store.query('history', workspace, parameters={'limit': limit, 'before': before, 'target': target, 'snapshot': snapshot})

    @router.post('/api/history/preview')
    def history_preview(body: HistoryPreviewBody):
        return store.query('history_preview', body.workspace, parameters={'action': body.action, 'target': body.target})

    @router.post('/api/history/apply')
    def history_apply(body: HistoryApplyBody):
        try:
            return store.command('history_apply', body.workspace, {'action': body.action, 'target': body.target, 'fingerprint': body.fingerprint}, request_id=body.requestId, expected_revision=body.revision)
        except RevisionConflict as error:
            return JSONResponse({'error': str(error), 'code': 'history_revision_conflict'}, status_code=409)

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

    class CategorizationBody(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        workspace: Workspace
        payload: dict

    @router.get("/api/categorization/suggestions")
    def category_suggestions(workspace: Workspace):
        return store.category_suggestions(workspace)

    @router.post("/api/categorization")
    def categorize(body: CategorizationBody):
        return store.categorize(body.workspace, body.payload)

    @router.patch("/api/transactions/{transaction_id}")
    def review(transaction_id: str, body: ReviewBody):
        if not body.model_fields_set.intersection({"categoryId", "reviewed", "note", "accountId", "date", "merchant", "amount"}):
            raise HTTPException(400, "No changes provided")
        category = body.categoryId if "categoryId" in body.model_fields_set else UNSET
        store.edit_transaction(
            body.workspace, transaction_id, category_id=category, reviewed=body.reviewed,
            **({'category_agent': body.categoryAgent} if 'categoryAgent' in body.model_fields_set else {}),
            **({"details": body.model_dump(include=body.model_fields_set & {"accountId", "date", "merchant", "amount"})}
               if body.model_fields_set & {"accountId", "date", "merchant", "amount"} else {}),
            **({"suggestion_id": body.suggestionId} if body.suggestionId is not None else {}),
            **({"note": body.note} if "note" in body.model_fields_set else {}),
        )
        return {"ok": True}

    @router.put("/api/transactions/review")
    def bulk_review(body: BulkReviewBody):
        store.review_transactions(
            body.workspace, body.transactionIds, body.reviewed, request_id=body.requestId,
        )
        return {"ok": True}

    @router.post("/api/transactions")
    def add_transaction(body: TransactionBody):
        store.add_transaction(body.workspace, body.model_dump(exclude={"workspace", "requestId"}),
                              **({"request_id": body.requestId} if body.requestId is not None else {}))
        return {"ok": True}

    @router.put("/api/budget/funding/next-month")
    def next_month_assignment(body: NextMonthAssignmentBody):
        store.assign_next_month(body.workspace, body.month, body.currency, body.assigned)
        return {"ok": True}

    @router.put("/api/budget/settings/rollover")
    def rollover_mode(body: RolloverBody):
        store.set_rollover_mode(body.workspace, body.month, body.currency, body.mode)
        return {"ok": True}

    @router.put("/api/budget/{category_id}")
    def assignment(category_id: str, body: AssignmentBody):
        store.assign(body.workspace, category_id, body.month, body.assigned)
        return {"ok": True}

    @router.post("/api/categories")
    def category(body: CategoryBody):
        payload = body.model_dump(exclude={"workspace", "kind"})
        if body.kind == "income":
            payload["kind"] = body.kind
        store.add_category(body.workspace, payload)
        return {"ok": True}

    @router.post("/api/accounts")
    def account(body: AccountBody):
        excluded = {"workspace"} | ({"closed"} if "closed" not in body.model_fields_set else set())
        account_id = store.add_account(body.workspace, body.model_dump(exclude=excluded))
        return {"ok": True, "accountId": account_id}

    @router.patch("/api/accounts/{account_id}")
    def classify_account(account_id: str, body: AccountClassificationBody):
        store.update_account(
            body.workspace, account_id,
            body.model_dump(exclude={"workspace"}, exclude_unset=True),
        )
        return {"ok": True}

    @router.get("/api/accounts/{account_id}/settings")
    def account_settings(account_id: str, workspace: Workspace):
        return store.account_settings(workspace, account_id)

    return router
