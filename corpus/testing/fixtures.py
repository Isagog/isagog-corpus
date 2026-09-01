"""Canonical seed data.

One dataset drives every run of the contract suite, so "the fake and the
adapter behave the same" is a claim about the same articles, the same edition
boundaries and the same draft that must stay out of a published listing.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from corpus.models import PUBLISHED, Article, AssetRef, EditionCover

ARTICLE_1_ID = "550e8400-e29b-41d4-a716-446655440001"
ARTICLE_2_ID = "550e8400-e29b-41d4-a716-446655440002"
ARTICLE_3_ID = "550e8400-e29b-41d4-a716-446655440003"
ARTICLE_4_ID = "550e8400-e29b-41d4-a716-446655440004"
ARTICLE_5_ID = "550e8400-e29b-41d4-a716-446655440005"

EDITION_1_ID = "660e8400-e29b-41d4-a716-4466554400a1"
EDITION_2_ID = "660e8400-e29b-41d4-a716-4466554400a2"
EDITION_3_ID = "660e8400-e29b-41d4-a716-4466554400a3"

PDF_ASSET_ID = "770e8400-e29b-41d4-a716-4466554400f1"
PDF_ASSET_ID_3 = "770e8400-e29b-41d4-a716-4466554400f3"
COVER_ASSET_ID = "770e8400-e29b-41d4-a716-4466554400c1"

_COVER_JPEG = b"\xff\xd8\xff\xe0 prima pagina 2024-01-15 " + b"y" * 256

_BODY = (
    "Il corpo dell'articolo, abbastanza lungo da superare le soglie di "
    "processabilita dichiarate dalle pipeline che leggono questo archivio. "
) * 4


class SeedArticle(BaseModel):
    """An article plus the two facts that live *around* it in a CMS: its
    editorial status and the edition it belongs to."""

    model_config = ConfigDict(frozen=True)

    article: Article
    status: str = PUBLISHED
    edition_id: str | None = None


class SeedEdition(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    date: str
    slug: str | None = None
    title: str | None = None
    status: str = PUBLISHED
    pdf: AssetRef | None = None
    #: None means this edition has no front page — `get_edition_cover` must
    #: raise DocumentNotFound for it, not return a blank cover.
    cover: EditionCover | None = None


class CorpusSeed(BaseModel):
    model_config = ConfigDict(frozen=True)

    articles: tuple[SeedArticle, ...]
    editions: tuple[SeedEdition, ...]
    assets: Mapping[str, bytes]


def _article(
    article_id: str,
    slug: str,
    publish_date: str,
    headline: str,
    section: str,
    *,
    author: str = "Mario Rossi",
    kicker: str = "Cronaca",
) -> Article:
    return Article(
        id=article_id,
        slug=slug,
        publish_date=publish_date,
        author=author,
        headline=headline,
        kicker=kicker,
        body=_BODY,
        section=section,
    )


DEFAULT_SEED = CorpusSeed(
    articles=(
        SeedArticle(
            article=_article(
                ARTICLE_1_ID, "primo-articolo", "2024-01-15", "Primo articolo", "Politica"
            ),
            edition_id=EDITION_1_ID,
        ),
        SeedArticle(
            article=_article(
                ARTICLE_2_ID,
                "secondo-articolo",
                "2024-01-15",
                "Secondo articolo",
                "Cultura",
                author="",
                kicker="",
            ),
            edition_id=EDITION_1_ID,
        ),
        SeedArticle(
            article=_article(
                ARTICLE_3_ID, "terzo-articolo", "2024-01-16", "Terzo articolo", "Politica"
            ),
            edition_id=EDITION_2_ID,
        ),
        SeedArticle(
            article=_article(
                ARTICLE_4_ID, "quarto-articolo", "2024-01-17", "Quarto articolo", "Esteri"
            ),
            edition_id=EDITION_3_ID,
        ),
        # The draft: it must never appear in a default listing, nor inside a
        # hydrated edition.
        SeedArticle(
            article=_article(
                ARTICLE_5_ID, "quinto-articolo", "2024-01-17", "Quinto articolo", "Politica"
            ),
            status="draft",
            edition_id=EDITION_3_ID,
        ),
    ),
    editions=(
        SeedEdition(
            id=EDITION_1_ID,
            date="2024-01-15",
            slug="edizione-2024-01-15",
            title="Edizione del 15 gennaio",
            pdf=AssetRef(id=PDF_ASSET_ID, filename="2024-01-15.pdf", mime="application/pdf"),
            # The display headline is deliberately NOT the cover story's own
            # headline ("Primo articolo"). An adapter that lazily maps the
            # article headline onto the cover fails the contract suite here.
            cover=EditionCover(
                article_id=ARTICLE_1_ID,
                headline="Prima pagina del 15 gennaio",
                kicker="Cronaca",
                image=AssetRef(
                    id=COVER_ASSET_ID,
                    filename="2024-01-15-cover.jpg",
                    mime="image/jpeg",
                    size=len(_COVER_JPEG),
                ),
            ),
        ),
        # No PDF and no cover: `EditionQuery(require_pdf=True)` must exclude
        # it, and `get_edition_cover` must report it as not found.
        SeedEdition(id=EDITION_2_ID, date="2024-01-16", slug="edizione-2024-01-16"),
        # A cover whose image the CMS never got: the front page still exists,
        # so `image is None` must be reachable without an error.
        SeedEdition(
            id=EDITION_3_ID,
            date="2024-01-17",
            slug="edizione-2024-01-17",
            pdf=AssetRef(id=PDF_ASSET_ID_3, filename="2024-01-17.pdf", mime="application/pdf"),
            cover=EditionCover(
                article_id=ARTICLE_4_ID,
                headline="Prima pagina del 17 gennaio",
                kicker="Cronaca",
            ),
        ),
    ),
    assets={
        PDF_ASSET_ID: b"%PDF-1.4 edizione 2024-01-15 " + b"x" * 512,
        PDF_ASSET_ID_3: b"%PDF-1.4 edizione 2024-01-17 " + b"x" * 512,
        COVER_ASSET_ID: _COVER_JPEG,
    },
)
