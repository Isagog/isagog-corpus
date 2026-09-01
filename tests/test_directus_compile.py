"""Query compilation: every grammar row from the inventory §2.3, as a table."""

from datetime import date

import pytest
from corpus.cursor import encode_cursor
from corpus.errors import CapabilityNotSupported
from corpus.models import PUBLISHED
from corpus.query import ArticleOrder, ArticleQuery, EditionQuery
from corpus_directus.compile import (
    article_projection,
    article_ref_projection,
    chunk_ids,
    compile_article_query,
    compile_cover_query,
    compile_edition_query,
    edition_projection,
)
from corpus_directus.schema import MANIFESTO_SCHEMA, DirectusSchema


def _params(query: ArticleQuery, **kwargs) -> dict[str, str]:
    return compile_article_query(query, MANIFESTO_SCHEMA, **kwargs)


@pytest.mark.unit
class TestProjections:
    def test_article_projection_is_generated_from_the_schema(self):
        fields = article_projection(MANIFESTO_SCHEMA).split(",")
        assert set(fields) == {
            "id",
            "slug",
            "status",
            "datePublished",
            "author",
            "headline",
            "articleKicker",
            "articleBody",
            "articleSection.name",
        }

    def test_ref_projection_never_asks_for_the_body(self):
        fields = article_ref_projection(MANIFESTO_SCHEMA).split(",")
        assert "articleBody" not in fields
        assert {"id", "slug", "status", "datePublished", "articleSection.name"} <= set(fields)

    def test_edition_projection_includes_nested_articles(self):
        fields = edition_projection(MANIFESTO_SCHEMA, with_articles=True).split(",")
        assert "editionPdf.pdf" in fields
        assert "articles.articleBody" in fields
        assert "articles.status" in fields

    def test_edition_listing_projection_omits_nested_bodies(self):
        fields = edition_projection(MANIFESTO_SCHEMA, with_articles=False).split(",")
        assert "articles.articleBody" not in fields
        assert "editionPdf.pdf" in fields

    def test_projection_follows_a_renamed_field(self):
        schema = DirectusSchema(
            article_fields={**MANIFESTO_SCHEMA.article_fields, "body": "content"}
        )
        assert "content" in article_projection(schema).split(",")


@pytest.mark.unit
class TestArticleQueryCompilation:
    def test_defaults_carry_status_projection_sort_and_limit(self):
        params = _params(ArticleQuery())
        assert params["filter[status][_eq]"] == PUBLISHED
        assert params["filter[datePublished][_nnull]"] == "true"
        assert params["sort"] == "-datePublished,-id"
        assert params["limit"] == "100"
        assert "fields" in params

    def test_ascending_order(self):
        params = _params(ArticleQuery(order=ArticleOrder.PUBLISH_DATE_ASC))
        assert params["sort"] == "datePublished,id"

    def test_status_none_drops_the_filter(self):
        assert "filter[status][_eq]" not in _params(ArticleQuery(status=None))

    def test_require_publish_date_can_be_dropped(self):
        assert "filter[datePublished][_nnull]" not in _params(
            ArticleQuery(require_publish_date=False)
        )

    def test_ids_compile_to_an_in_filter(self):
        params = _params(ArticleQuery(ids=("a", "b")))
        assert params["filter[id][_in]"] == "a,b"

    def test_slugs_compile_to_an_in_filter(self):
        assert _params(ArticleQuery(slugs=("uno",)))["filter[slug][_in]"] == "uno"

    def test_sections_compile_through_the_nested_field_path(self):
        params = _params(ArticleQuery(sections=("Politica", "Cultura")))
        assert params["filter[articleSection][name][_in]"] == "Politica,Cultura"

    def test_date_range_compiles_to_gte_lte(self):
        params = _params(
            ArticleQuery(published_from=date(2024, 1, 1), published_to=date(2024, 1, 31))
        )
        assert params["filter[datePublished][_gte]"] == "2024-01-01"
        assert params["filter[datePublished][_lte]"] == "2024-01-31T23:59:59"

    def test_page_size_becomes_limit(self):
        assert _params(ArticleQuery(page_size=25))["limit"] == "25"

    def test_edition_axis_without_a_declared_field_is_refused(self):
        schema = DirectusSchema(article_edition_field=None)
        with pytest.raises(CapabilityNotSupported):
            compile_article_query(ArticleQuery(edition_id="e1"), schema)

    def test_edition_axis_compiles_when_the_schema_declares_the_field(self):
        schema = DirectusSchema(article_edition_field="edition")
        params = compile_article_query(ArticleQuery(edition_id="e1"), schema)
        assert params["filter[edition][_eq]"] == "e1"

    def test_the_manifesto_schema_compiles_the_edition_axis(self):
        assert _params(ArticleQuery(edition_id="e1"))["filter[articleEdition][_eq]"] == "e1"


@pytest.mark.unit
class TestKeysetPagination:
    def test_descending_cursor_compiles_to_an_or_group(self):
        cursor = encode_cursor({"d": "2024-01-15", "x": ["abc", "def"]})
        params = _params(ArticleQuery(), cursor=cursor)
        assert params["filter[_or][0][datePublished][_lt]"] == "2024-01-15"
        assert params["filter[_or][1][_and][0][datePublished][_eq]"] == "2024-01-15"
        assert params["filter[_or][1][_and][1][id][_nin]"] == "abc,def"

    def test_ascending_cursor_flips_the_operators(self):
        cursor = encode_cursor({"d": "2024-01-15", "x": ["abc"]})
        params = _params(ArticleQuery(order=ArticleOrder.PUBLISH_DATE_ASC), cursor=cursor)
        assert params["filter[_or][0][datePublished][_gt]"] == "2024-01-15"
        assert params["filter[_or][1][_and][1][id][_nin]"] == "abc"

    def test_the_tiebreaker_never_orders_by_id(self):
        """Directus refuses `_lt`/`_gt` on a uuid column, so a comparison
        against `id` is a 400 in production and must never be compiled."""
        cursor = encode_cursor({"d": "2024-01-15", "x": ["abc"]})
        for order in ArticleOrder:
            params = _params(ArticleQuery(order=order), cursor=cursor)
            offending = [
                key
                for key in params
                if "[id][" in key and any(op in key for op in ("_lt", "_lte", "_gt", "_gte"))
            ]
            assert not offending, offending

    def test_an_empty_tie_group_compiles_to_a_plain_comparison(self):
        """Nothing has been served at that instant yet, so a strict comparison
        is exact and a one-branch _or would only lengthen the URL."""
        params = _params(ArticleQuery(), cursor=encode_cursor({"d": "2024-01-15"}))
        assert params["filter[datePublished][_lt]"] == "2024-01-15"
        assert not [k for k in params if "_or" in k]

    def test_no_cursor_means_no_or_group(self):
        assert not [k for k in _params(ArticleQuery()) if "_or" in k]


@pytest.mark.unit
class TestChunking:
    def test_chunks_at_the_url_length_limit(self):
        chunks = chunk_ids(tuple(str(n) for n in range(250)), 100)
        assert [len(c) for c in chunks] == [100, 100, 50]

    def test_a_short_list_is_one_chunk(self):
        assert chunk_ids(("a", "b"), 100) == (("a", "b"),)

    def test_an_empty_list_has_no_chunks(self):
        assert chunk_ids((), 100) == ()

    def test_compilation_uses_the_chunk_it_is_given(self):
        params = _params(ArticleQuery(ids=("a", "b", "c")), ids=("a",))
        assert params["filter[id][_in]"] == "a"


@pytest.mark.unit
class TestEditionQueryCompilation:
    def test_date_range_and_sort(self):
        params = compile_edition_query(
            EditionQuery(date_from=date(2024, 1, 1), date_to=date(2024, 1, 31)),
            MANIFESTO_SCHEMA,
        )
        assert params["filter[editionDate][_gte]"] == "2024-01-01"
        assert params["filter[editionDate][_lte]"] == "2024-01-31"
        assert params["sort"] == "editionDate"

    def test_exact_date(self):
        params = compile_edition_query(EditionQuery(date_exact=date(2024, 1, 15)), MANIFESTO_SCHEMA)
        assert params["filter[editionDate][_eq]"] == "2024-01-15"

    def test_require_pdf(self):
        params = compile_edition_query(EditionQuery(require_pdf=True), MANIFESTO_SCHEMA)
        assert params["filter[editionPdf][_null]"] == "false"

    def test_pdf_filter_is_absent_by_default(self):
        params = compile_edition_query(EditionQuery(), MANIFESTO_SCHEMA)
        assert "filter[editionPdf][_null]" not in params


@pytest.mark.unit
class TestCoverQueryCompilation:
    def test_narrows_to_one_published_cover_row_of_one_edition(self):
        params = compile_cover_query("ed-1", MANIFESTO_SCHEMA)
        assert params["filter[articleEdition][_eq]"] == "ed-1"
        assert params["filter[articlePositionCover][_eq]"] == "1"
        assert params["filter[status][_eq]"] == "published"
        assert params["limit"] == "1"

    def test_projects_the_file_record_behind_the_image(self):
        """Without the deep expansion the AssetRef arrives without a mime type,
        and a caller deriving a file extension has to fetch the bytes first."""
        fields = compile_cover_query("ed-1", MANIFESTO_SCHEMA)["fields"].split(",")
        assert "referenceHeadline" in fields
        assert "articleKicker" in fields
        assert "articleFeaturedImage.image.id" in fields
        assert "articleFeaturedImage.image.type" in fields
        assert "articleFeaturedImage.image.filename_download" in fields
        assert "articleFeaturedImage.image.filesize" in fields

    def test_does_not_project_the_article_headline(self):
        fields = compile_cover_query("ed-1", MANIFESTO_SCHEMA)["fields"].split(",")
        assert "headline" not in fields

    def test_is_refused_without_the_edition_axis(self):
        with pytest.raises(CapabilityNotSupported):
            compile_cover_query("ed-1", DirectusSchema(article_edition_field=None))

    def test_a_renamed_instance_compiles_its_own_vocabulary(self):
        schema = DirectusSchema(
            article_edition_field="issue",
            cover_fields={
                "article_id": "id",
                "headline": "frontPageTitle",
                "kicker": "standfirst",
                "image": "leadImage.file",
            },
            cover_filter={"isFrontPage": "true"},
        )
        params = compile_cover_query("ed-1", schema)
        assert params["filter[issue][_eq]"] == "ed-1"
        assert params["filter[isFrontPage][_eq]"] == "true"
        assert "leadImage.file.type" in params["fields"].split(",")
