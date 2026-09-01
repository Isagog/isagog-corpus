"""The query model, derived from the twelve production call sites.

Every axis maps to a capability: a backend that cannot express one says so in
`CorpusCapabilities` rather than silently returning the wrong rows.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from corpus.models import PUBLISHED


class ArticleOrder(StrEnum):
    PUBLISH_DATE_DESC = "publish_date_desc"
    PUBLISH_DATE_ASC = "publish_date_asc"


class ArticleQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    ids: tuple[str, ...] = ()  # by id set
    slugs: tuple[str, ...] = ()  # by slug
    edition_id: str | None = None  # articles of one edition
    sections: tuple[str, ...] = ()
    published_from: date | None = None
    published_to: date | None = None
    status: str | None = PUBLISHED
    require_publish_date: bool = True
    order: ArticleOrder = ArticleOrder.PUBLISH_DATE_DESC
    page_size: int = Field(default=100, gt=0)  # transport hint, not a limit


class EditionQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    date_from: date | None = None
    date_to: date | None = None
    date_exact: date | None = None
    require_pdf: bool = False
