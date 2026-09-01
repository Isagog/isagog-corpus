"""All Directus vocabulary, in one frozen object.

Two instances with the same schema already exist in the wild
(`pulse.ilmanifesto.it` and the legacy `directus.ilmanifesto.it`), and a
customer with different field names is the next one. Retargeting either is a
`DirectusSchema` constant — not another codebase.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

ARTICLE_FIELDS: Mapping[str, str] = {
    "id": "id",
    "slug": "slug",
    "status": "status",
    "publish_date": "datePublished",
    "author": "author",
    "headline": "headline",
    "kicker": "articleKicker",
    "body": "articleBody",
    "section": "articleSection.name",
}

EDITION_FIELDS: Mapping[str, str] = {
    "id": "id",
    "date": "editionDate",
    "status": "status",
    "slug": "slug",
    "title": "title",
    "pdf": "editionPdf.pdf",
}

#: Fields a row must carry for the corresponding model to be constructible.
REQUIRED_ARTICLE_FIELDS = ("id", "slug", "publish_date", "headline", "body")
REQUIRED_EDITION_FIELDS = ("id", "date")


class DirectusSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    articles_collection: str = "articles"
    editions_collection: str = "editions"
    assets_path: str = "assets"
    auth_probe_path: str = "/users/me"
    article_fields: Mapping[str, str] = ARTICLE_FIELDS
    edition_fields: Mapping[str, str] = EDITION_FIELDS
    published_status: str = "published"
    #: Reverse relation from an article to its edition. No production call site
    #: filters that way, so it stays undeclared until an instance names it —
    #: `ArticleQuery(edition_id=...)` is refused rather than guessed.
    article_edition_field: str | None = None
    #: `id_format="uuid"` is a real constraint on this CMS; a differently
    #: configured instance turns it off rather than forking the parser.
    id_is_uuid: bool = True

    def article_field(self, name: str) -> str:
        return self.article_fields[name]

    def edition_field(self, name: str) -> str:
        return self.edition_fields[name]


MANIFESTO_SCHEMA = DirectusSchema()
