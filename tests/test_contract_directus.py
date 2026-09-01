"""The contract suite, run against DirectusCorpus over a stubbed instance.

Same suite, same seed, same assertions as the fake — which is what makes
"write a new adapter" a checklist.
"""

import pytest
import respx
from corpus.testing.contract import CorpusContractSuite
from corpus.testing.fixtures import DEFAULT_SEED, CorpusSeed
from corpus_directus.client import DirectusCorpus

from tests.directus_stub import DirectusStub

BASE_URL = "http://directus.contract"


@pytest.mark.integration
class TestDirectusCorpusContract(CorpusContractSuite):
    @pytest.fixture
    def seed(self) -> CorpusSeed:
        return DEFAULT_SEED

    @pytest.fixture
    async def corpus(self, seed):
        instance = DirectusCorpus(base_url=BASE_URL, api_key="contract-key")
        # Some contract tests are pure port logic and issue no request at all.
        with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
            mock.route().mock(side_effect=DirectusStub(seed))
            yield instance
        await instance.aclose()
