"""Test doubles and the executable adapter specification.

`FakeCorpus` is production-importable: it is what a consumer's
`backend="fake"` arm builds for demos, e2e runs and CI without secrets, so
nothing here may pull in a test framework.

`CorpusContractSuite` needs pytest and is therefore *not* re-exported —
adapter authors import it explicitly:

    from corpus.testing.contract import CorpusContractSuite
"""

from corpus.testing.fake import FULL_HOUSE, FakeCorpus
from corpus.testing.fixtures import DEFAULT_SEED, CorpusSeed, SeedArticle, SeedEdition

__all__ = [
    "DEFAULT_SEED",
    "FULL_HOUSE",
    "CorpusSeed",
    "FakeCorpus",
    "SeedArticle",
    "SeedEdition",
]
