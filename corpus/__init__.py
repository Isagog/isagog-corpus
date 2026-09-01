"""`corpus` — an abstract newspaper-archive data access layer.

The AI/analysis layer depends on these types and nothing else: no transport
exception, no vendor filter grammar and no CMS field name ever crosses this
port. A new CMS is one new adapter package plus a passing contract suite.
"""

from corpus.base import Corpus
from corpus.capabilities import Capability, CorpusCapabilities, CorpusRequirements
from corpus.cursor import decode_cursor, encode_cursor
from corpus.errors import (
    CapabilityNotSupported,
    CorpusAuthError,
    CorpusConfigError,
    CorpusError,
    CorpusRateLimited,
    CorpusUnavailable,
    DocumentNotFound,
    InvalidDocument,
)
from corpus.http_status import http_status_for
from corpus.models import (
    PUBLISHED,
    Article,
    ArticlePage,
    ArticleRef,
    AssetRef,
    Edition,
    EditionCover,
    EditionRef,
)
from corpus.notify import NullNotifier, PublishResult, ResultNotifier
from corpus.policy import ArticlePolicy
from corpus.query import ArticleOrder, ArticleQuery, EditionQuery
from corpus.signals import ActorKind, ChangeKind, ChangeSignal

__all__ = [
    "PUBLISHED",
    "ActorKind",
    "Article",
    "ArticleOrder",
    "ArticlePage",
    "ArticlePolicy",
    "ArticleQuery",
    "ArticleRef",
    "AssetRef",
    "Capability",
    "CapabilityNotSupported",
    "ChangeKind",
    "ChangeSignal",
    "Corpus",
    "CorpusAuthError",
    "CorpusCapabilities",
    "CorpusConfigError",
    "CorpusError",
    "CorpusRateLimited",
    "CorpusRequirements",
    "CorpusUnavailable",
    "DocumentNotFound",
    "Edition",
    "EditionCover",
    "EditionQuery",
    "EditionRef",
    "InvalidDocument",
    "NullNotifier",
    "PublishResult",
    "ResultNotifier",
    "decode_cursor",
    "encode_cursor",
    "http_status_for",
]
