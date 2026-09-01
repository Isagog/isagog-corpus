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

#: The front page. Its fields live on the cover *article*, not on the edition
#: row: `referenceHeadline` is the display headline il manifesto prints on the
#: cover, which is routinely not the cover story's own `headline`.
COVER_FIELDS: Mapping[str, str] = {
    "article_id": "id",
    "headline": "referenceHeadline",
    "kicker": "articleKicker",
    "image": "articleFeaturedImage.image",
}

#: `directus_files`. Projecting these through the image relation is what makes
#: an `AssetRef` arrive complete in one request — a caller deriving a file
#: extension never has to fetch the bytes to learn the type.
FILE_FIELDS: Mapping[str, str] = {
    "id": "id",
    "mime": "type",
    "filename": "filename_download",
    "size": "filesize",
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
    #: Reverse relation from an article to its edition. Verified against
    #: pulse.ilmanifesto.it on 2026-09-01: `filter[articleEdition][_eq]=<uuid>`
    #: returns that edition's articles. An instance that names it differently
    #: overrides it; one that cannot express the axis sets it to None, and both
    #: `ArticleQuery(edition_id=...)` and covers are then refused rather than
    #: guessed.
    article_edition_field: str | None = "articleEdition"
    cover_fields: Mapping[str, str] = COVER_FIELDS
    file_fields: Mapping[str, str] = FILE_FIELDS
    #: Equality filters that single out the cover row within an edition. A
    #: mapping rather than one field so an instance marking its cover with two
    #: conditions stays a constant instead of a subclass.
    cover_filter: Mapping[str, str] = {"articlePositionCover": "1"}
    #: Equality filters narrowing the editions collection to one series.
    #: Empty by default: an instance holding a single series must not have one
    #: silently imposed on it. See `MANIFESTO_WP_SCHEMA` for why an instance
    #: might need it.
    edition_filter: Mapping[str, str] = {}
    #: `id_format="uuid"` is a real constraint on this CMS; a differently
    #: configured instance turns it off rather than forking the parser.
    id_is_uuid: bool = True

    def article_field(self, name: str) -> str:
        return self.article_fields[name]

    def edition_field(self, name: str) -> str:
        return self.edition_fields[name]

    def cover_field(self, name: str) -> str:
        return self.cover_fields[name]

    def file_field(self, name: str) -> str:
        return self.file_fields[name]

    @property
    def supports_covers(self) -> bool:
        """A cover is reached by filtering articles down to one edition, so an
        instance that cannot express that axis has no covers to declare."""
        return self.article_edition_field is not None and bool(self.cover_fields)


MANIFESTO_SCHEMA = DirectusSchema()

#: pulse.ilmanifesto.it holds FOUR imported edition series, and they overlap:
#:
#:     mema           7188   1971-04-28 -> 2008-11-10
#:     athenaPre2002  2129   1995-01-17 -> 2001-12-30
#:     athena         5723   2001-02-06 -> 2023-12-31
#:     wp             4165   2013-03-27 -> today
#:
#: Measured 2026-09-01. Overlapping series mean a date can resolve to more than
#: one edition — every date in 2018-2023 does — and nothing in the row says
#: which is authoritative. `wp` is the live series: it alone is still being
#: written, and across its whole range it has 4165 editions on 4165 distinct
#: dates, i.e. no ambiguity at all.
#:
#: A consumer that wants "the edition of this date" to be a single answer takes
#: this schema. One that genuinely wants the deep historical archive takes
#: MANIFESTO_SCHEMA and decides for itself.
MANIFESTO_WP_SCHEMA = DirectusSchema(edition_filter={"syncSource": "wp"})
