"""A minimal Directus over a `CorpusSeed`, served through respx.

It interprets the `filter[...]` grammar rather than pattern-matching URLs, so
running the contract suite against `DirectusCorpus` actually exercises query
compilation, keyset pagination and the projection — not just the happy path of
a hand-written response.

Directus field names are hardcoded here on purpose: reading them from
`MANIFESTO_SCHEMA` would let a schema bug cancel itself out.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import httpx
from corpus.models import PUBLISHED
from corpus.testing.fixtures import CorpusSeed

_BRACKETS = re.compile(r"\[([^\]]*)\]")

#: Directus refuses ordering operators on a `uuid` column:
#:
#:     Invalid query. "uuid" field type does not contain the "_lt" filter operator.
#:
#: The stub models that, because a stub more permissive than the real backend
#: is how keyset pagination stayed green here while 400ing in production.
_UUID_FIELDS = frozenset({"id"})
_ORDERING_OPS = frozenset({"_lt", "_lte", "_gt", "_gte"})


class InvalidQuery(Exception):
    """What Directus answers with 400 + code INVALID_QUERY."""


def article_row(seeded) -> dict[str, Any]:
    article = seeded.article
    return {
        "id": article.id,
        "slug": article.slug,
        "status": seeded.status,
        "datePublished": f"{article.publish_date}T06:00:00Z",
        "author": article.author or None,
        "headline": f"<p>{article.headline}</p>",
        "articleKicker": article.kicker or None,
        "articleBody": f"<div>{article.body}</div>",
        "articleSection": {"name": article.section} if article.section else None,
    }


def edition_row(seed: CorpusSeed, edition, *, nested: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": edition.id,
        "editionDate": edition.date,
        "status": edition.status,
        "slug": edition.slug,
        "title": edition.title,
        "editionPdf": {"pdf": edition.pdf.id} if edition.pdf else None,
    }
    members = [s for s in seed.articles if s.edition_id == edition.id]
    row["articles"] = (
        [article_row(s) for s in members] if nested else [s.article.id for s in members]
    )
    return row


class DirectusStub:
    """Callable respx side effect. `requests` records what was asked for."""

    def __init__(self, seed: CorpusSeed) -> None:
        self.seed = seed
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        params = dict(request.url.params)

        if path == "/users/me":
            return httpx.Response(200, json={"data": {"id": "stub-user"}})
        if match := re.fullmatch(r"/assets/(.+)", path):
            payload = self.seed.assets.get(match.group(1))
            if payload is None:
                return _not_found()
            return httpx.Response(200, content=payload)
        if match := re.fullmatch(r"/items/articles/(.*)", path):
            return self._one(self._article_rows(), match.group(1))
        if path == "/items/articles":
            return self._list(self._article_rows(), params)
        if match := re.fullmatch(r"/items/editions/(.*)", path):
            return self._one(self._edition_rows(nested=True), match.group(1))
        if path == "/items/editions":
            return self._list(self._edition_rows(nested=False), params)
        return _not_found()

    # --- collections ------------------------------------------------------
    def _article_rows(self) -> list[dict[str, Any]]:
        return [article_row(s) for s in self.seed.articles]

    def _edition_rows(self, *, nested: bool) -> list[dict[str, Any]]:
        return [edition_row(self.seed, e, nested=nested) for e in self.seed.editions]

    def _one(self, rows: list[dict[str, Any]], row_id: str) -> httpx.Response:
        for row in rows:
            if row["id"] == row_id:
                return httpx.Response(200, json={"data": row})
        return _not_found()

    def _list(self, rows: list[dict[str, Any]], params: Mapping[str, str]) -> httpx.Response:
        tree = _filter_tree(params)
        try:
            selected = [row for row in rows if _matches(tree, row)]
        except InvalidQuery as err:
            return httpx.Response(
                400,
                json={
                    "errors": [
                        {
                            "message": f"Invalid query. {err}",
                            "extensions": {"reason": str(err), "code": "INVALID_QUERY"},
                        }
                    ]
                },
            )
        selected = _sorted(selected, params.get("sort"))

        limit = int(params.get("limit", 100))
        page = int(params.get("page", 1))
        start = (page - 1) * limit
        return httpx.Response(200, json={"data": selected[start : start + limit]})


def _not_found() -> httpx.Response:
    return httpx.Response(
        404, json={"errors": [{"message": "not found", "extensions": {"code": "FORBIDDEN"}}]}
    )


def _filter_tree(params: Mapping[str, str]) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for key, value in params.items():
        if not key.startswith("filter["):
            continue
        tokens = _BRACKETS.findall(key)
        node = tree
        for token in tokens[:-1]:
            node = node.setdefault(token, {})
        node[tokens[-1]] = value
    return tree


def _matches(node: Mapping[str, Any], row: Mapping[str, Any], path: tuple[str, ...] = ()) -> bool:
    for key, sub in node.items():
        if key == "_or":
            if not any(_matches(branch, row) for branch in sub.values()):
                return False
        elif key == "_and":
            if not all(_matches(branch, row) for branch in sub.values()):
                return False
        elif key.startswith("_"):
            if path and path[-1] in _UUID_FIELDS and key in _ORDERING_OPS:
                raise InvalidQuery(
                    f'"uuid" field type does not contain the "{key}" filter operator'
                )
            if not _apply(key, _pluck(row, path), sub):
                return False
        elif not _matches(sub, row, (*path, key)):
            return False
    return True


def _apply(op: str, value: Any, literal: str) -> bool:
    match op:
        case "_eq":
            return str(value) == literal
        case "_neq":
            return str(value) != literal
        case "_in":
            return value is not None and str(value) in literal.split(",")
        case "_nin":
            return value is not None and str(value) not in literal.split(",")
        case "_gte":
            return value is not None and str(value) >= literal
        case "_lte":
            return value is not None and str(value) <= literal
        case "_gt":
            return value is not None and str(value) > literal
        case "_lt":
            return value is not None and str(value) < literal
        case "_nnull":
            return (value is not None) is (literal == "true")
        case "_null":
            return (value is None) is (literal == "true")
        case _:
            raise AssertionError(f"stub does not implement Directus operator {op!r}")


def _pluck(row: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = row
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _sorted(rows: list[dict[str, Any]], sort: str | None) -> list[dict[str, Any]]:
    if not sort:
        return rows
    for term in reversed(sort.split(",")):
        descending = term.startswith("-")
        field = term.lstrip("-")
        rows = sorted(rows, key=lambda r: str(_pluck(r, (field,))), reverse=descending)
    return rows


def published_ids(seed: CorpusSeed) -> set[str]:
    return {s.article.id for s in seed.articles if s.status == PUBLISHED}
