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
from corpus.errors import DocumentNotFound, InvalidDocument
from corpus.models import Article
from corpus.query import ArticleQuery, EditionQuery
from corpus_directus.client import DirectusCorpus
from corpus_directus.schema import MANIFESTO_WP_SCHEMA

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


async def test_the_cover_schema_still_parses(corpus):
    """The one test that notices a `referenceHeadline` rename before a
    consumer's caption column fills up with nulls."""
    today = date.today()
    editions = await corpus.list_editions(
        EditionQuery(date_from=today - timedelta(days=14), date_to=today), page_size=20
    )
    if not editions:
        pytest.skip("no recent editions on the staging instance")

    for ref in sorted(editions, key=lambda e: e.date, reverse=True):
        try:
            cover = await corpus.get_edition_cover(ref.id)
        except DocumentNotFound:
            continue  # an edition the CMS never gave a front page
        assert cover.headline, "cover headline came back empty"
        if cover.image is not None:
            assert cover.image.mime, "the file record did not expand — projection drift"
            assert cover.image.size, "the file record did not expand — projection drift"
        return
    pytest.skip("no recent edition carries a cover")


#: Each comparison costs a cover query plus a full article fetch, so the walk
#: stops as soon as it has seen enough to be meaningful.
_HEADLINE_SAMPLE = 5


async def test_the_cover_headline_is_not_the_article_headline(corpus):
    """The distinction the EditionCover model exists for. If this starts
    failing everywhere, the instance changed what `referenceHeadline` means."""
    today = date.today()
    editions = await corpus.list_editions(
        EditionQuery(date_from=today - timedelta(days=14), date_to=today), page_size=20
    )
    compared = 0
    differed = 0
    for ref in sorted(editions, key=lambda e: e.date, reverse=True):
        if compared >= _HEADLINE_SAMPLE:
            break
        try:
            cover = await corpus.get_edition_cover(ref.id)
        except (DocumentNotFound, InvalidDocument):
            continue
        if cover.article_id is None:
            continue
        story = await corpus.get_article(cover.article_id)
        compared += 1
        differed += cover.headline != story.headline
    if compared < 3:
        pytest.skip("not enough recent covers linked to an article")
    assert differed, "no cover differed from its story headline — check the schema mapping"


async def test_a_missing_document_is_not_found(corpus):
    with pytest.raises(DocumentNotFound):
        await corpus.get_article("00000000-0000-4000-8000-000000000000")


@pytest.fixture
async def wp_corpus():
    """The single-series view: editions narrowed to the live `wp` import."""
    instance = DirectusCorpus(base_url=BASE_URL, api_key=API_KEY, schema=MANIFESTO_WP_SCHEMA)
    yield instance
    await instance.aclose()


async def test_the_default_schema_still_sees_overlapping_series(corpus):
    """The reason MANIFESTO_WP_SCHEMA exists. If this ever stops finding a
    collision, the instance was cleaned up and the narrowing may be dropped."""
    editions = await corpus.list_editions(
        EditionQuery(date_from=date(2020, 1, 1), date_to=date(2020, 1, 31))
    )
    if not editions:
        pytest.skip("no 2020 editions on the staging instance")
    dates = [e.date for e in editions]
    assert len(dates) != len(set(dates)), "expected overlapping edition series in 2020"


async def test_the_wp_schema_gives_one_edition_per_date(wp_corpus):
    """The property the whole single-series design rests on: within `wp`, a
    date resolves to exactly one edition — checked across four eras."""
    for start, end in (
        (date(2013, 4, 1), date(2013, 4, 30)),
        (date(2018, 6, 1), date(2018, 6, 30)),
        (date(2020, 1, 1), date(2020, 1, 31)),
        (date(2026, 8, 1), date(2026, 8, 31)),
    ):
        editions = await wp_corpus.list_editions(EditionQuery(date_from=start, date_to=end))
        dates = [e.date for e in editions]
        assert dates, f"no wp editions between {start} and {end}"
        assert len(dates) == len(set(dates)), f"duplicate edition dates in {start:%Y-%m}"
