"""`corpus_directus` — the Directus specialization of the `corpus` port.

Everything that knows a Directus field name, endpoint or filter operator lives
in this package: `schema.py` (vocabulary), `rows.py` (parsing), `compile.py`
(query grammar), `inbound.py` (save notifications) and `notifier.py` (the
Flow webhook). Retargeting a differently-named instance is a `DirectusSchema`
constant; a different CMS is a new adapter passing the same contract suite.
"""

from corpus_directus.client import DirectusCorpus
from corpus_directus.inbound import parse_change
from corpus_directus.notifier import DirectusFlowNotifier
from corpus_directus.schema import MANIFESTO_SCHEMA, MANIFESTO_WP_SCHEMA, DirectusSchema
from corpus_directus.settings import (
    DirectusCorpusSettings,
    DirectusNotifierSettings,
    Timeouts,
)

__all__ = [
    "MANIFESTO_SCHEMA",
    "MANIFESTO_WP_SCHEMA",
    "DirectusCorpus",
    "DirectusCorpusSettings",
    "DirectusFlowNotifier",
    "DirectusNotifierSettings",
    "DirectusSchema",
    "Timeouts",
    "parse_change",
]
