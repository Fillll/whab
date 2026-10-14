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
)

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
    group: Label
    target: PositiveCents


class AccountBody(Body):
    workspace: Workspace
    name: Label
    type: Literal["depository", "credit"]
    balance: Cents


class PluginSelectionBody(Body):
    plugins: Annotated[list[Annotated[str, StringConstraints(min_length=1, max_length=64)]], Field(max_length=100)]
