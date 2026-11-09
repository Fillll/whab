"""Bank connection request bodies; secrets remain server-side."""

from typing import Annotated

from pydantic import Field, StringConstraints

from whab.web.schemas import Body, Id, Label


class LinkBody(Body):
    itemId: Id | None = None


class LinkStartBody(LinkBody):
    profileId: Id | None = None


class ProfileBody(Body):
    profileId: Id
    config: dict[str, str] = Field(max_length=100)


class DisconnectBody(Body):
    budgetIds: list[Id] = Field(max_length=1000)


FlowId = Annotated[str, StringConstraints(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$")]


class ExchangeBody(Body):
    flowId: FlowId | None = None
    publicToken: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    institutionId: Id | None = None
    institutionName: Label | None = None


class AccountSelection(Body):
    bankAccountId: Id
    accountId: Id | None
    since: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None


class SyncSetupBody(Body):
    itemId: Id
    accounts: list[AccountSelection] = Field(min_length=1, max_length=500)


class ScheduleBody(Body):
    interval_seconds: int | None


class BackgroundBody(Body):
    enabled: bool


class InvestmentsBody(Body):
    itemId: Id
    bankAccountId: Id | None = None
    startDate: Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2}$')] | None = None
    endDate: Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2}$')] | None = None
