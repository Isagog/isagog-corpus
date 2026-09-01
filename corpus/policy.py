"""Consumer-owned data constraints.

The corpus never rejects an article a different pipeline could use: memaflow2's
300–30 000 character window is memaflow2's rule, not the archive's.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from corpus.errors import InvalidDocument
from corpus.models import Article


class ArticlePolicy(BaseModel):
    """Pipeline-specific processability constraints. NOT enforced by the
    corpus — each consumer applies its own at its boundary."""

    model_config = ConfigDict(frozen=True)

    required_fields: frozenset[str] = frozenset({"headline", "body", "publish_date"})
    min_body_chars: int | None = None
    max_body_chars: int | None = None

    def check(self, article: Article) -> None:
        for name in sorted(self.required_fields):
            if not getattr(article, name):
                raise InvalidDocument(f"required field {name!r} is empty", kind="missing_field")
        n = len(article.body)
        if self.min_body_chars is not None and n < self.min_body_chars:
            raise InvalidDocument(f"body too short ({n})", kind="bad_value")
        if self.max_body_chars is not None and n > self.max_body_chars:
            raise InvalidDocument(f"body too long ({n})", kind="bad_value")
