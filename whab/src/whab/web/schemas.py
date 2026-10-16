"""Strict validation for the local ledger HTTP interface."""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from whab.domain.accounts import normalize_account
from whab.domain.money import currency_code

Workspace = Literal["demo", "personal"]
Id = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Cents = Annotated[StrictInt, Field(ge=-1_000_000_000_000, le=1_000_000_000_000)]
PositiveCents = Annotated[StrictInt, Field(ge=0, le=1_000_000_000_000)]


def valid_month(value):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value) or not "1900-01" <= value <= "2200-12":
        raise ValueError("Invalid month")
    return value


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReviewBody(Body):
    workspace: Workspace
    categoryId: Id | None = None
    reviewed: StrictBool | None = None


class TransactionBody(Body):
    workspace: Workspace
    accountId: Id
    date: str
    merchant: Label
    amount: Cents
    categoryId: Id | None
    note: Annotated[str, Field(max_length=1000)] = ""

    @field_validator("date")
    @classmethod
    def actual_date(cls, value):
        if (
            date.fromisoformat(value).isoformat() != value
            or not "1900-01-01" <= value <= "2200-12-31"
        ):
            raise ValueError("Invalid date")
        return value

    @field_validator("amount")
    @classmethod
    def nonzero(cls, value):
        if value == 0:
            raise ValueError("Amount must not be zero")
        return value


class AssignmentBody(Body):
    workspace: Workspace
    month: str
    assigned: PositiveCents
    _month = field_validator("month")(valid_month)


class CategoryBody(Body):
    workspace: Workspace
    name: Label
    group: Label | None = None
    parentId: Id | None = None
    target: PositiveCents
    currency: str = "USD"
    _currency = field_validator("currency")(currency_code)


class AccountBody(Body):
    workspace: Workspace
    name: Label
    type: str
    balance: Cents
    currency: str = "USD"
    onBudget: StrictBool | None = None
    subtype: str | None = None
    _currency = field_validator("currency")(currency_code)

    @model_validator(mode="after")
    def classification(self):
        record = self.model_dump(exclude={"workspace"})
        if "onBudget" not in self.model_fields_set:
            record.pop("onBudget")
        normalized = normalize_account(record)
        self.onBudget = normalized["onBudget"]
        return self


class AccountClassificationBody(Body):
    workspace: Workspace
    type: str | None = None
    onBudget: StrictBool | None = None
    subtype: str | None = None

    @model_validator(mode="after")
    def classification(self):
        changes = self.model_fields_set - {"workspace"}
        if not changes:
            raise ValueError("No account changes provided")
        if "type" in changes:
            normalize_account(self.model_dump(include=changes))
        if "onBudget" in changes and self.onBudget is None:
            raise ValueError("Invalid account budget scope")
        return self


class PluginSelectionBody(Body):
    plugins: Annotated[
        list[Annotated[str, StringConstraints(min_length=1, max_length=64)]], Field(max_length=100)
    ]
