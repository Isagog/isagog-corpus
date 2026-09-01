"""Inbound save notifications → ChangeSignal. Tolerant by construction."""

from datetime import UTC, date, datetime

import pytest
from corpus.signals import ActorKind, ChangeKind, ChangeSignal
from corpus_directus.inbound import parse_change

RECEIVED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

BODIES = [
    None,
    {},
    {"event": "items.create"},
    {"event": "items.update", "source": "editor"},
    {"event": "publish", "status": "published"},
    {"event": "items.unheard-of", "source": "robot"},
    {"publish_date": "not-a-date"},
    {"publish_date": None, "status": None},
    {"unexpected": {"nested": ["shapes"]}},
    {"event": 17, "source": ["editor"], "status": {}},
    {"content_fingerprint": "sha256:abc"},
]


@pytest.mark.unit
class TestTolerance:
    @pytest.mark.parametrize("body", BODIES)
    def test_never_raises_and_always_yields_a_signal(self, body):
        signal = parse_change("a1", body, received_at=RECEIVED)
        assert isinstance(signal, ChangeSignal)
        assert signal.article_id == "a1"
        assert signal.received_at == RECEIVED

    def test_a_body_less_request_parses_to_the_conservative_signal(self):
        signal = parse_change("a1", None, received_at=RECEIVED)
        assert signal.change is ChangeKind.UNKNOWN
        assert signal.actor is ActorKind.UNKNOWN
        assert signal.publish_date is None
        assert signal.status is None
        assert signal.fingerprint is None
        assert signal.raw == {}

    def test_received_at_defaults_to_now(self):
        assert parse_change("a1", {}).received_at.tzinfo is not None


@pytest.mark.unit
class TestVocabulary:
    @pytest.mark.parametrize(
        ("event", "expected"),
        [
            ("items.create", ChangeKind.CREATE),
            ("items.update", ChangeKind.UPDATE),
            ("create", ChangeKind.CREATE),
            ("update", ChangeKind.UPDATE),
            ("publish", ChangeKind.PUBLISH),
            ("items.delete", ChangeKind.UNKNOWN),
            ("", ChangeKind.UNKNOWN),
        ],
    )
    def test_directus_event_names(self, event, expected):
        assert parse_change("a1", {"event": event}).change is expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("editor", ActorKind.EDITOR),
            ("bulk", ActorKind.BULK),
            ("import", ActorKind.IMPORT),
            ("migration", ActorKind.MIGRATION),
            ("api", ActorKind.API),
            ("something-new", ActorKind.UNKNOWN),
        ],
    )
    def test_actor_vocabulary(self, source, expected):
        assert parse_change("a1", {"source": source}).actor is expected

    def test_publish_date_and_status_and_fingerprint(self):
        signal = parse_change(
            "a1",
            {
                "publish_date": "2024-01-15T10:00:00Z",
                "status": "published",
                "content_fingerprint": "sha256:abc",
            },
        )
        assert signal.publish_date == date(2024, 1, 15)
        assert signal.status == "published"
        assert signal.fingerprint == "sha256:abc"

    def test_raw_body_is_kept_verbatim_for_audit(self):
        body = {"event": "items.create", "vendor_only": {"flow_id": 7}}
        assert parse_change("a1", body).raw == body

    def test_status_alone_never_implies_a_publish_verdict(self):
        """Evidence, never verdict: a published status on an update stays an update."""
        signal = parse_change("a1", {"event": "items.update", "status": "published"})
        assert signal.change is ChangeKind.UPDATE
