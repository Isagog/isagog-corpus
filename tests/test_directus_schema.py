"""Schema as data: the vendor vocabulary lives in one frozen object."""

import pytest
from corpus_directus.schema import MANIFESTO_SCHEMA, DirectusSchema
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

    def test_article_edition_field_is_unset_by_default(self):
        """No production call site filters articles by edition; the reverse
        field name must be declared before that axis can compile."""
        assert MANIFESTO_SCHEMA.article_edition_field is None
