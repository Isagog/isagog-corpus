"""Vendor-neutral domain models: the archive of record.

Frozen, tuple-valued, and deliberately carrying memaflow2's `ArticleInput`
field names — those names are a frozen Temporal-history contract there, so
adopting them makes that migration wire-identical.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

#: The one status literal the port names. Everything else stays an open string:
#: statuses are editorially configurable per instance and per CMS.
PUBLISHED = "published"


class Article(BaseModel):
    """A fully hydrated article from the archive of record.

    Text fields are CMS-hygiene-normalised: HTML stripped, dates YYYY-MM-DD,
    absent kicker/author folded to "". No pipeline policy applied here.
    """

    model_config = ConfigDict(frozen=True)

    id: str  # backend-native id, opaque to consumers
    slug: str
    publish_date: str  # YYYY-MM-DD
    author: str  # "" when absent
    headline: str
    kicker: str  # "" when absent
    body: str  # plain text
    section: str | None = None
    language: str | None = None  # BCP-47; None = backend default


class ArticleRef(BaseModel):
    """Cheap projection for listings and routing — never the full body."""

    model_config = ConfigDict(frozen=True)

    id: str
    slug: str | None = None
    status: str | None = None
    publish_date: str | None = None
    section: str | None = None


class AssetRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    filename: str | None = None
    mime: str | None = None
    size: int | None = None


class Edition(BaseModel):
    """A dated issue of the publication."""

    model_config = ConfigDict(frozen=True)

    id: str
    date: str  # YYYY-MM-DD
    slug: str | None = None
    title: str | None = None
    articles: tuple[Article, ...] = ()
    pdf: AssetRef | None = None


class EditionRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    date: str
    article_count: int | None = None
    pdf: AssetRef | None = None


class ArticlePage(BaseModel):
    """One page of a listing. `next_cursor` is opaque; None = last page."""

    model_config = ConfigDict(frozen=True)

    items: tuple[ArticleRef, ...]
    next_cursor: str | None = None
