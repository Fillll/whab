"""Bank connection request bodies; secrets remain server-side."""

from typing import Annotated

from pydantic import Field

from whab.web.schemas import Body, Id, Label


class LinkBody(Body):
    itemId: Id | None = None


class ExchangeBody(Body):
    publicToken: Annotated[str, Field(min_length=1, max_length=500)]
    institutionId: Id | None = None
    institutionName: Label | None = None
