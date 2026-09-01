"""The third contract run: DirectusCorpus against a live instance.

Manual, and skipped unless CORPUS_STAGING_BASE_URL / CORPUS_STAGING_API_KEY
are set. Its job is to keep the *schema constant* honest — the seeded runs
cannot notice a field rename on the real CMS, because they seed the rows
themselves.

    uv run pytest -m staging --override-ini="addopts="
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from corpus.errors import DocumentNotFound
from corpus.models import Article
from corpus.query import ArticleQuery, EditionQuery
from corpus_directus.client import DirectusCorpus

BASE_URL = os.getenv("CORPUS_STAGING_BASE_URL", "")
API_KEY = os.getenv("CORPUS_STAGING_API_KEY", "")

pytestmark = [
    pytest.mark.staging,
    pytest.mark.skipif(
        not (BASE_URL and API_KEY),
        reason="set CORPUS_STAGING_BASE_URL and CORPUS_STAGING_API_KEY to run",
    ),
]


@pytest.fixture
async def corpus():
    instance = DirectusCorpus(base_url=BASE_URL, api_key=API_KEY)
    yield instance
    await instance.aclose()


async def test_credentials_and_connectivity(corpus):
    await corpus.ping()


async def test_the_article_schema_still_parses(corpus):
    page = await corpus.search_articles(ArticleQuery(page_size=5))
    assert page.items, "no published articles found on the staging instance"

    article = await corpus.get_article(page.items[0].id)
    assert isinstance(article, Article)
    assert article.headline and article.body
    assert len(article.publish_date) == 10


async def test_pagination_walks_without_repeating(corpus):
    seen: list[str] = []
    cursor = None
    for _ in range(3):
        page = await corpus.search_articles(ArticleQuery(page_size=5), cursor)
        seen.extend(ref.id for ref in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == len(set(seen))


async def test_the_edition_schema_still_parses(corpus):
    # Date-bounded on purpose. This archive holds 19 205 editions back to 1971,
    # so an unbounded walk at a small page size is thousands of requests. The
    # window still spans several pages, so paging is exercised, not skipped.
    today = date.today()
    editions = await corpus.list_editions(
        EditionQuery(date_from=today - timedelta(days=60), date_to=today, require_pdf=True),
        page_size=20,
    )
    if not editions:
        pytest.skip("no editions with a PDF on the staging instance")

    edition = await corpus.get_edition(editions[0].id)
    assert edition.pdf is not None
    assert len(edition.date) == 10


async def test_a_missing_document_is_not_found(corpus):
    with pytest.raises(DocumentNotFound):
        await corpus.get_article("00000000-0000-4000-8000-000000000000")
