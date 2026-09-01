"""Directus row dicts → corpus models. Pure functions, one per shape.

This is where CMS hygiene happens: HTML stripped, dates folded to a day,
absent optional text folded to "". The two failure kinds are kept apart on
purpose — an absent key is `missing_field`, a key whose value cannot be used
is `bad_value` — because the consumers' retry tables distinguish them.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from typing import Any

from corpus.errors import InvalidDocument
from corpus.models import Article, ArticleRef, AssetRef, Edition, EditionRef
from corpus.normalize import normalize_date, require_text, strip_html

from corpus_directus.schema import (
    REQUIRED_ARTICLE_FIELDS,
    REQUIRED_EDITION_FIELDS,
    DirectusSchema,
)

logger = logging.getLogger(__name__)

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def article_from_row(row: Mapping[str, Any], schema: DirectusSchema) -> Article:
    _require_keys(row, schema, REQUIRED_ARTICLE_FIELDS, schema.article_field)
    field = schema.article_field
    return Article(
        id=_article_id(_pluck(row, field("id")), schema),
        slug=_slug(_pluck(row, field("slug"))),
        publish_date=normalize_date(_pluck(row, field("publish_date")), "publish_date"),
        author=strip_html(_pluck(row, field("author"))),
        headline=require_text(_pluck(row, field("headline")), "headline"),
        kicker=strip_html(_pluck(row, field("kicker"))),
        body=require_text(_pluck(row, field("body")), "body"),
        section=_optional_text(_pluck(row, field("section"))),
    )


def article_ref_from_row(row: Mapping[str, Any], schema: DirectusSchema) -> ArticleRef:
    field = schema.article_field
    _require_keys(row, schema, ("id",), field)
    publish_date = _pluck(row, field("publish_date"))
    return ArticleRef(
        id=_article_id(_pluck(row, field("id")), schema),
        slug=_optional_text(_pluck(row, field("slug"))),
        status=_optional_text(_pluck(row, field("status"))),
        publish_date=normalize_date(publish_date, "publish_date") if publish_date else None,
        section=_optional_text(_pluck(row, field("section"))),
    )


def edition_from_row(row: Mapping[str, Any], schema: DirectusSchema) -> Edition:
    _require_keys(row, schema, REQUIRED_EDITION_FIELDS, schema.edition_field)
    field = schema.edition_field
    return Edition(
        id=str(_pluck(row, field("id"))),
        date=normalize_date(_pluck(row, field("date")), "edition_date"),
        slug=_optional_text(_pluck(row, field("slug"))),
        title=_optional_text(_pluck(row, field("title"))),
        articles=_nested_articles(row, schema),
        pdf=_pdf_ref(row, schema),
    )


def edition_ref_from_row(row: Mapping[str, Any], schema: DirectusSchema) -> EditionRef:
    _require_keys(row, schema, REQUIRED_EDITION_FIELDS, schema.edition_field)
    field = schema.edition_field
    nested = row.get("articles")
    return EditionRef(
        id=str(_pluck(row, field("id"))),
        date=normalize_date(_pluck(row, field("date")), "edition_date"),
        article_count=len(nested) if isinstance(nested, list) else None,
        pdf=_pdf_ref(row, schema),
    )


def _nested_articles(row: Mapping[str, Any], schema: DirectusSchema) -> tuple[Article, ...]:
    """Published nested rows only; one malformed article never costs the edition."""
    nested = row.get("articles")
    if not isinstance(nested, list):
        return ()
    articles: list[Article] = []
    for nested_row in nested:
        if not isinstance(nested_row, Mapping):
            continue
        if nested_row.get(schema.article_field("status")) != schema.published_status:
            continue
        try:
            articles.append(article_from_row(nested_row, schema))
        except InvalidDocument as err:
            logger.warning(
                "skipping article %s in edition %s: %s",
                nested_row.get("id"),
                row.get("id"),
                err,
            )
    return tuple(articles)


def _pdf_ref(row: Mapping[str, Any], schema: DirectusSchema) -> AssetRef | None:
    file_id = _pluck(row, schema.edition_field("pdf"))
    return AssetRef(id=str(file_id)) if file_id else None


def _require_keys(
    row: Mapping[str, Any],
    schema: DirectusSchema,
    names: tuple[str, ...],
    resolve,
) -> None:
    for name in names:
        path = resolve(name).split(".")
        if path[0] not in row:
            raise InvalidDocument(f"row is missing {resolve(name)!r}", kind="missing_field")


def _article_id(value: Any, schema: DirectusSchema) -> str:
    if value is None or value == "":
        raise InvalidDocument("'id' is missing or null in CMS", kind="bad_value")
    text = str(value)
    if schema.id_is_uuid:
        try:
            uuid.UUID(text)
        except ValueError:
            raise InvalidDocument("article id must be a valid UUID", kind="bad_value") from None
    return text


def _slug(value: Any) -> str:
    if not value or not isinstance(value, str):
        raise InvalidDocument("'slug' is missing or null in CMS", kind="bad_value")
    if not _SLUG.match(value):
        raise InvalidDocument(
            "slug must be lowercase letters, numbers, and hyphens only", kind="bad_value"
        )
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = strip_html(str(value))
    return text or None


def _pluck(row: Mapping[str, Any], dotted: str) -> Any:
    """Resolve `articleSection.name` against a nested row."""
    value: Any = row
    for part in dotted.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value
