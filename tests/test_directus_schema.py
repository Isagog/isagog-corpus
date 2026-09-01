"""Schema as data: the vendor vocabulary lives in one frozen object."""

import pytest
from corpus_directus.schema import MANIFESTO_SCHEMA, MANIFESTO_WP_SCHEMA, DirectusSchema
from pydantic import ValidationError


@pytest.mark.unit
class TestDirectusSchema:
    def test_manifesto_field_names_match_the_production_instance(self):
        assert MANIFESTO_SCHEMA.articles_collection == "articles"
        assert MANIFESTO_SCHEMA.editions_collection == "editions"
        assert MANIFESTO_SCHEMA.article_fields["publish_date"] == "datePublished"
        assert MANIFESTO_SCHEMA.article_fields["kicker"] == "articleKicker"
        assert MANIFESTO_SCHEMA.article_fields["body"] == "articleBody"
        assert MANIFESTO_SCHEMA.article_fields["section"] == "articleSection.name"
        assert MANIFESTO_SCHEMA.edition_fields["date"] == "editionDate"
        assert MANIFESTO_SCHEMA.edition_fields["pdf"] == "editionPdf.pdf"
        assert MANIFESTO_SCHEMA.published_status == "published"

    def test_is_frozen(self):
        with pytest.raises(ValidationError):
            MANIFESTO_SCHEMA.published_status = "live"

    def test_a_second_instance_is_a_constant_not_a_codebase(self):
        """Retargeting a differently-named Directus is a schema object."""
        other = DirectusSchema(
            article_fields={**MANIFESTO_SCHEMA.article_fields, "body": "content"},
            published_status="live",
        )
        assert other.article_fields["body"] == "content"
        assert other.article_fields["headline"] == "headline"
        assert MANIFESTO_SCHEMA.article_fields["body"] == "articleBody"

    def test_article_edition_field_is_declared(self):
        """Verified against pulse.ilmanifesto.it on 2026-09-01:
        `filter[articleEdition][_eq]=<uuid>` returns that edition's articles.
        Both `ArticleQuery(edition_id=...)` and covers depend on it."""
        assert MANIFESTO_SCHEMA.article_edition_field == "articleEdition"

    def test_an_instance_without_the_edition_axis_declares_no_covers(self):
        """A cover is one edition's articles filtered down to one row, so an
        instance that cannot express that axis has no covers to offer."""
        assert MANIFESTO_SCHEMA.supports_covers is True
        assert DirectusSchema(article_edition_field=None).supports_covers is False

    def test_cover_vocabulary_is_schema_data(self):
        assert MANIFESTO_SCHEMA.cover_field("headline") == "referenceHeadline"
        assert MANIFESTO_SCHEMA.cover_field("image") == "articleFeaturedImage.image"
        assert MANIFESTO_SCHEMA.cover_filter == {"articlePositionCover": "1"}
        assert MANIFESTO_SCHEMA.file_field("mime") == "type"
        assert MANIFESTO_SCHEMA.file_field("size") == "filesize"

    def test_the_display_headline_is_not_the_article_headline(self):
        """The two are different Directus fields, and conflating them is the
        one mistake a cover adapter is most likely to make."""
        assert MANIFESTO_SCHEMA.cover_field("headline") != MANIFESTO_SCHEMA.article_field(
            "headline"
        )

    def test_a_renamed_instance_is_a_constant_not_a_subclass(self):
        other = DirectusSchema(
            cover_fields={
                "article_id": "id",
                "headline": "frontPageTitle",
                "kicker": "standfirst",
                "image": "leadImage.file",
            },
            cover_filter={"isFrontPage": "true"},
        )
        assert other.cover_field("headline") == "frontPageTitle"
        assert MANIFESTO_SCHEMA.cover_field("headline") == "referenceHeadline"


@pytest.mark.unit
class TestEditionSeries:
    def test_the_default_schema_spans_every_series(self):
        assert MANIFESTO_SCHEMA.edition_filter == {}

    def test_the_wp_schema_is_the_only_one_that_narrows(self):
        """pulse.ilmanifesto.it holds four overlapping imported series, so a
        date can resolve to more than one edition. `wp` is the live one and is
        unambiguous across its whole range."""
        assert MANIFESTO_WP_SCHEMA.edition_filter == {"syncSource": "wp"}

    def test_the_two_schemas_differ_only_in_that(self):
        assert MANIFESTO_WP_SCHEMA.model_copy(update={"edition_filter": {}}) == MANIFESTO_SCHEMA
