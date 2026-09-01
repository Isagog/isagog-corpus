"""The contract suite, run against the reference implementation.

If this goes red the fake has drifted from the specification — which is the
whole point of keeping the fake honest.
"""

import pytest
from corpus.testing.contract import CorpusContractSuite
from corpus.testing.fake import FakeCorpus
from corpus.testing.fixtures import DEFAULT_SEED, CorpusSeed


@pytest.mark.unit
class TestFakeCorpusContract(CorpusContractSuite):
    @pytest.fixture
    def seed(self) -> CorpusSeed:
        return DEFAULT_SEED

    @pytest.fixture
    def corpus(self, seed) -> FakeCorpus:
        return FakeCorpus.from_seed(seed)
