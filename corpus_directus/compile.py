"""`ArticleQuery`/`EditionQuery` → Directus request params.

Pure functions over a schema: no I/O, no client, no state. Every parameter
this module can emit was observed at one of the twelve production call sites,
except the keyset `_or` group, which replaces offset paging — offset paging
over a collection being written during an edition evening skips and repeats
rows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from corpus.cursor import decode_cursor
from corpus.errors import CapabilityNotSupported
from corpus.query import ArticleOrder, ArticleQuery, EditionQuery

from corpus_directus.schema import DirectusSchema

#: Fields projected for a full article, and for a listing row.
_ARTICLE_PROJECTION = (
    "id",
    "slug",
    "status",
    "publish_date",
    "author",
    "headline",
    "kicker",
    "body",
    "section",
)
_ARTICLE_REF_PROJECTION = ("id", "slug", "status", "publish_date", "section")
_EDITION_PROJECTION = ("id", "date", "status", "slug", "title", "pdf")


def article_projection(schema: DirectusSchema) -> str:
    return ",".join(schema.article_field(name) for name in _ARTICLE_PROJECTION)


def article_ref_projection(schema: DirectusSchema) -> str:
    return ",".join(schema.article_field(name) for name in _ARTICLE_REF_PROJECTION)


def edition_projection(schema: DirectusSchema, *, with_articles: bool) -> str:
    fields = [schema.edition_field(name) for name in _EDITION_PROJECTION]
    if with_articles:
        fields += [f"articles.{schema.article_field(n)}" for n in _ARTICLE_PROJECTION]
    else:
        fields.append(f"articles.{schema.article_field('id')}")
    return ",".join(fields)


def chunk_ids(values: Sequence[str], size: int) -> tuple[tuple[str, ...], ...]:
    """Directus filters travel in the URL, so an id set has a length limit."""
    return tuple(tuple(values[start : start + size]) for start in range(0, len(values), size))


def compile_article_query(
    query: ArticleQuery,
    schema: DirectusSchema,
    *,
    cursor: str | None = None,
    ids: Sequence[str] | None = None,
) -> dict[str, str]:
    """`ids` overrides `query.ids` so the client can walk one chunk at a time."""
    field = schema.article_field
    descending = query.order is ArticleOrder.PUBLISH_DATE_DESC
    date_field = field("publish_date")

    params: dict[str, str] = {
        "fields": article_ref_projection(schema),
        "limit": str(query.page_size),
        "sort": _sort(date_field, field("id"), descending),
    }

    if query.status is not None:
        params[f"filter[{field('status')}][_eq]"] = query.status
    if query.require_publish_date:
        params[f"filter[{date_field}][_nnull]"] = "true"

    selected_ids = tuple(ids) if ids is not None else query.ids
    if selected_ids:
        params[f"filter[{field('id')}][_in]"] = ",".join(selected_ids)
    if query.slugs:
        params[f"filter[{field('slug')}][_in]"] = ",".join(query.slugs)
    if query.sections:
        params[f"filter{_path(field('section'))}[_in]"] = ",".join(query.sections)
    if query.edition_id is not None:
        if schema.article_edition_field is None:
            raise CapabilityNotSupported(
                "filtering articles by edition requires DirectusSchema.article_edition_field"
            )
        params[f"filter[{schema.article_edition_field}][_eq]"] = query.edition_id
    if query.published_from is not None:
        params[f"filter[{date_field}][_gte]"] = query.published_from.isoformat()
    if query.published_to is not None:
        # The CMS stores a timestamp; an inclusive upper bound on the *day*
        # must therefore cover its last second.
        params[f"filter[{date_field}][_lte]"] = f"{query.published_to.isoformat()}T23:59:59"

    if cursor is not None:
        params.update(_keyset(decode_cursor(cursor), date_field, field("id"), descending))
    return params


def compile_edition_query(
    query: EditionQuery, schema: DirectusSchema, *, page: int = 1, page_size: int = 100
) -> dict[str, str]:
    date_field = schema.edition_field("date")
    params: dict[str, str] = {
        "fields": edition_projection(schema, with_articles=False),
        "sort": date_field,
        "limit": str(page_size),
        "page": str(page),
    }
    if query.date_exact is not None:
        params[f"filter[{date_field}][_eq]"] = query.date_exact.isoformat()
    if query.date_from is not None:
        params[f"filter[{date_field}][_gte]"] = query.date_from.isoformat()
    if query.date_to is not None:
        params[f"filter[{date_field}][_lte]"] = query.date_to.isoformat()
    if query.require_pdf:
        # The null check belongs on the relation, not on the file id inside it:
        # `editionPdf.pdf` filters as `filter[editionPdf][_null]=false`.
        relation = schema.edition_field("pdf").split(".")[0]
        params[f"filter[{relation}][_null]"] = "false"
    return params


def _sort(date_field: str, id_field: str, descending: bool) -> str:
    prefix = "-" if descending else ""
    return f"{prefix}{date_field},{prefix}{id_field}"


def _path(dotted: str) -> str:
    """`articleSection.name` → `[articleSection][name]`."""
    return "".join(f"[{part}]" for part in dotted.split("."))


def _keyset(
    position: Mapping[str, object], date_field: str, id_field: str, descending: bool
) -> dict[str, str]:
    """Rows after `position` in the requested order.

    The tiebreaker is an *exclusion set*, not `id > last`, because Directus
    refuses ordering operators on a `uuid` column:

        Invalid query. "uuid" field type does not contain the "_lt" filter
        operator.

    `_eq`, `_neq`, `_in` and `_nin` are the only comparisons every Directus
    column type accepts, so "after this row" is expressed as "older than this
    instant, or at this instant but not one of the ids already served". That
    keeps the boundary stable while the collection is being written — which is
    the whole reason for keyset paging — without asking the backend for an
    operator it does not have.

    `publish_date` alone is not unique on this archive: an edition stamps many
    articles within the same second, so dropping the tiebreaker entirely would
    skip or repeat exactly those rows.
    """
    last_date = str(position.get("d", ""))
    op = "_lt" if descending else "_gt"
    excluded = position.get("x")
    if not isinstance(excluded, list) or not excluded:
        # Nothing served at this instant yet: a strict comparison is exact, and
        # a one-branch _or would only make the URL longer.
        return {f"filter[{date_field}][{op}]": last_date}
    return {
        f"filter[_or][0][{date_field}][{op}]": last_date,
        f"filter[_or][1][_and][0][{date_field}][_eq]": last_date,
        f"filter[_or][1][_and][1][{id_field}][_nin]": ",".join(str(i) for i in excluded),
    }
