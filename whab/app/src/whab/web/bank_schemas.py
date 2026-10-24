"""Bank connection request bodies; secrets remain server-side."""

from typing import Annotated

from pydantic import Field, StringConstraints

from whab.web.schemas import Body, Id, Label


class LinkBody(Body):
    itemId: Id | None = None


FlowId = Annotated[str, StringConstraints(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$")]


class ExchangeBody(Body):
    flowId: FlowId | None = None
    publicToken: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    institutionId: Id | None = None
    institutionName: Label | None = None
