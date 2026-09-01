"""`DirectusCorpus` — the Directus specialization of the port.

Transport policy lives here and only here:

* composition, not inheritance: the client *holds* an `httpx.AsyncClient` and
  is `@final`. Subclassing the HTTP client is how twelve forks of this code
  came to exist — every consumer could reach the query DSL, so every consumer
  grew its own dialect of it;
* `AsyncHTTPTransport(retries=0)`: the caller owns retries. Transport-level
  retries would double-count a Temporal attempt and hide failures from the
  retry policy that is supposed to see them;
* separate JSON and asset timeouts, because "slow" means two different things
  for a query and for a 40 MB PDF;
* keyset pagination compiled into opaque cursors — offset paging over a
  collection being written during an edition evening skips and repeats rows.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Any, final

import httpx
from corpus.base import Corpus
from corpus.capabilities import Capability, CorpusCapabilities
from corpus.cursor import decode_cursor, encode_cursor
from corpus.errors import (
    CorpusConfigError,
    CorpusError,
    CorpusUnavailable,
    DocumentNotFound,
    InvalidDocument,
)
from corpus.models import Article, ArticlePage, ArticleRef, Edition, EditionRef
from corpus.query import ArticleQuery, EditionQuery
from corpus.signals import ChangeSignal
from pydantic import SecretStr

from corpus_directus.compile import (
    article_projection,
    article_ref_projection,
    chunk_ids,
    compile_article_query,
    compile_edition_query,
    edition_projection,
)
from corpus_directus.errors import from_status, from_transport_error
from corpus_directus.inbound import parse_change
from corpus_directus.rows import (
    article_from_row,
    article_ref_from_row,
    edition_from_row,
    edition_ref_from_row,
)
from corpus_directus.schema import MANIFESTO_SCHEMA, DirectusSchema
from corpus_directus.settings import DEFAULT_TIMEOUTS, DirectusCorpusSettings, Timeouts

logger = logging.getLogger(__name__)

DEFAULT_EDITION_PAGE_SIZE = 100

_BACKEND_CAPABILITIES = frozenset(Capability) - {Capability.RESULT_WEBHOOK}


@final
class DirectusCorpus(Corpus):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | SecretStr,
        schema: DirectusSchema = MANIFESTO_SCHEMA,
        timeouts: Timeouts = DEFAULT_TIMEOUTS,
        limits: httpx.Limits | None = None,
        max_ids_per_query: int = 100,
        result_webhook: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not base_url:
            raise CorpusConfigError("DirectusCorpus requires a base_url")
        if not key:
            raise CorpusConfigError("DirectusCorpus requires an api_key")

        self._schema = schema
        self._timeouts = timeouts
        self._max_ids = max_ids_per_query
        self._capabilities = CorpusCapabilities(
            supported=(
                _BACKEND_CAPABILITIES | {Capability.RESULT_WEBHOOK}
                if result_webhook
                else _BACKEND_CAPABILITIES
            ),
            max_ids_per_query=max_ids_per_query,
            id_format="uuid" if schema.id_is_uuid else "opaque",
        )
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeouts.json,
            limits=limits or httpx.Limits(max_connections=20, max_keepalive_connections=10),
            transport=httpx.AsyncHTTPTransport(retries=0),
        )

    @classmethod
    def from_settings(cls, settings: DirectusCorpusSettings) -> DirectusCorpus:
        return cls(
            base_url=settings.base_url,
            api_key=settings.api_key,
            schema=settings.schema_,
            timeouts=settings.timeouts,
            max_ids_per_query=settings.max_ids_per_query,
            result_webhook=settings.result_webhook,
        )

    # --- identity ---------------------------------------------------------
    @property
    def capabilities(self) -> CorpusCapabilities:
        return self._capabilities

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Exposed for lifecycle and inspection only — never to build requests
        with. Everything that speaks Directus lives in this package."""
        return self._client

    # --- articles ---------------------------------------------------------
    async def get_article(self, article_id: str) -> Article:
        self._require(Capability.ARTICLES)
        row = await self._get_one(
            f"/items/{self._schema.articles_collection}/{article_id}",
            {"fields": article_projection(self._schema)},
            f"article {article_id}",
        )
        return article_from_row(row, self._schema)

    async def get_article_ref(self, article_id: str) -> ArticleRef:
        self._require(Capability.ARTICLES)
        row = await self._get_one(
            f"/items/{self._schema.articles_collection}/{article_id}",
            {"fields": article_ref_projection(self._schema)},
            f"article {article_id}",
        )
        return article_ref_from_row(row, self._schema)

    async def search_articles(self, query: ArticleQuery, cursor: str | None = None) -> ArticlePage:
        """Walks one id-chunk at a time.

        An id set longer than `max_ids_per_query` cannot travel in one URL, so
        the cursor carries the chunk index too. Rows are ordered within a
        chunk; across chunks the walk is exhaustive but not globally sorted —
        an id set is an unordered request by nature.
        """
        self._require(Capability.ARTICLE_LISTING)
        chunks = chunk_ids(query.ids, self._max_ids) if query.ids else ((),)
        position = decode_cursor(cursor) if cursor is not None else {}
        start = _chunk_index(position, len(chunks))
        inner: str | None = cursor if "d" in position else None

        for index in range(start, len(chunks)):
            chunk_cursor = inner if index == start else None
            params = compile_article_query(
                query,
                self._schema,
                cursor=chunk_cursor,
                ids=chunks[index] or None,
            )
            rows = await self._get_many(
                f"/items/{self._schema.articles_collection}", params, "article listing"
            )
            inner = None
            if not rows:
                continue
            items = tuple(article_ref_from_row(row, self._schema) for row in rows)
            return ArticlePage(
                items=items,
                # The tie group carries forward only while the boundary instant
                # is unchanged, so a cursor that jumped to a new chunk starts clean.
                next_cursor=self._next_cursor(
                    rows, query, index, len(chunks), position if chunk_cursor else {}
                ),
            )
        return ArticlePage(items=(), next_cursor=None)

    # --- editions ---------------------------------------------------------
    async def get_edition(self, edition_id: str) -> Edition:
        self._require(Capability.EDITIONS)
        row = await self._get_one(
            f"/items/{self._schema.editions_collection}/{edition_id}",
            {"fields": edition_projection(self._schema, with_articles=True)},
            f"edition {edition_id}",
        )
        return edition_from_row(row, self._schema)

    async def list_editions(
        self, query: EditionQuery, *, page_size: int = DEFAULT_EDITION_PAGE_SIZE
    ) -> tuple[EditionRef, ...]:
        """Offset-paged to exhaustion. Directus has no keyset order here that
        beats `editionDate`, and a truncated listing is how memaflow2 silently
        lost editions."""
        self._require(Capability.EDITIONS)
        editions: list[EditionRef] = []
        page = 1
        while True:
            params = compile_edition_query(query, self._schema, page=page, page_size=page_size)
            rows = await self._get_many(
                f"/items/{self._schema.editions_collection}", params, "edition listing"
            )
            editions.extend(_edition_refs(rows, self._schema))
            if len(rows) < page_size:
                return tuple(editions)
            page += 1

    # --- assets -----------------------------------------------------------
    async def stream_asset(self, asset_id: str) -> AsyncIterator[bytes]:
        self._require(Capability.ASSETS)
        path = f"/{self._schema.assets_path}/{asset_id}"
        context = f"asset {asset_id}"
        try:
            async with self._client.stream("GET", path, timeout=self._timeouts.asset) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise from_status(
                        response.status_code, context, response.headers.get("Retry-After")
                    )
                async for chunk in response.aiter_bytes():
                    yield chunk
        except CorpusError:
            raise
        except Exception as exc:
            raise from_transport_error(exc, context) from None

    # --- inbound ----------------------------------------------------------
    def parse_change(
        self,
        document_id: str,
        body: Mapping[str, Any] | None,
        *,
        received_at: datetime | None = None,
    ) -> ChangeSignal:
        self._require(Capability.CHANGE_SIGNALS)
        return parse_change(document_id, body, received_at=received_at)

    # --- health -----------------------------------------------------------
    async def ping(self) -> None:
        await self._get_json(self._schema.auth_probe_path, None, "auth probe")

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- internals --------------------------------------------------------
    async def _get_json(self, path: str, params: Mapping[str, str] | None, context: str) -> Any:
        try:
            response = await self._client.get(path, params=dict(params or {}))
        except Exception as exc:
            raise from_transport_error(exc, context) from None
        if response.status_code >= 400:
            raise from_status(
                response.status_code, context, response.headers.get("Retry-After")
            ) from None
        try:
            return response.json()
        except ValueError:
            raise InvalidDocument(f"{context}: response is not JSON", kind="bad_value") from None

    async def _get_one(
        self, path: str, params: Mapping[str, str], context: str
    ) -> Mapping[str, Any]:
        payload = await self._get_json(path, params, context)
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not data:
            # A 200 carrying no data is not the same event as a 404, and the
            # consumers' retry tables tell them apart.
            raise DocumentNotFound(f"{context}: no data returned", source="empty")
        if not isinstance(data, Mapping):
            raise InvalidDocument(f"{context}: expected an object", kind="bad_value")
        return data

    async def _get_many(
        self, path: str, params: Mapping[str, str], context: str
    ) -> list[Mapping[str, Any]]:
        payload = await self._get_json(path, params, context)
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if data is None:
            return []
        if not isinstance(data, list):
            raise InvalidDocument(f"{context}: expected a list", kind="bad_value")
        return [row for row in data if isinstance(row, Mapping)]

    def _next_cursor(
        self,
        rows: list[Mapping[str, Any]],
        query: ArticleQuery,
        chunk_index: int,
        chunk_count: int,
        previous: Mapping[str, Any],
    ) -> str | None:
        if len(rows) >= query.page_size:
            date_field = self._schema.article_field("publish_date")
            # The raw CMS value, not the normalised day: the keyset filter is
            # compared by the backend against its own column.
            boundary = str(rows[-1].get(date_field) or "")
            return encode_cursor(
                {
                    "c": chunk_index,
                    "d": boundary,
                    "x": self._tie_group(rows, previous, boundary, date_field),
                }
            )
        if chunk_index + 1 < chunk_count:
            return encode_cursor({"c": chunk_index + 1})
        return None

    def _tie_group(
        self,
        rows: list[Mapping[str, Any]],
        previous: Mapping[str, Any],
        boundary: str,
        date_field: str,
    ) -> list[str]:
        """The ids already served at the boundary instant.

        This is the keyset tiebreaker: Directus has no ordering operator for a
        uuid column, so the next page excludes these by id instead of asking
        for `id > last`. The set is reset whenever the boundary instant moves,
        so it stays as small as the number of articles sharing one timestamp.
        """
        id_field = self._schema.article_field("id")
        served = [
            str(row.get(id_field) or "")
            for row in rows
            if str(row.get(date_field) or "") == boundary
        ]
        if str(previous.get("d", "")) == boundary:
            carried = previous.get("x")
            if isinstance(carried, list):
                served = [*(str(value) for value in carried), *served]

        unique = list(dict.fromkeys(served))
        if len(unique) > self._max_ids:
            # Truncating would silently skip or repeat rows — the precise
            # defect keyset paging exists to prevent — so this fails loudly.
            raise CorpusUnavailable(
                f"{len(unique)} articles share the timestamp {boundary!r}, more than the "
                f"{self._max_ids} ids this backend can exclude in one URL",
                kind="http",
                retryable=False,
            )
        return unique


def _edition_refs(rows: list[Mapping[str, Any]], schema: DirectusSchema) -> list[EditionRef]:
    """One malformed edition never costs the listing — skip and log."""
    refs: list[EditionRef] = []
    for row in rows:
        try:
            refs.append(edition_ref_from_row(row, schema))
        except InvalidDocument as err:
            logger.warning("skipping edition %s: %s", row.get("id"), err)
    return refs


def _chunk_index(position: Mapping[str, Any], chunk_count: int) -> int:
    raw = position.get("c", 0)
    index = raw if isinstance(raw, int) else 0
    return index if 0 <= index < chunk_count else 0
