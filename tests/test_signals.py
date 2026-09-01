"""Inbound change signals — evidence, never verdict (proposal §4.7)."""

from datetime import date, datetime

import pytest
from corpus.signals import (
    ActorKind,
    ChangeKind,
    ChangeSignal,
    coerce_actor_kind,
    coerce_change_kind,
    coerce_signal_date,
)
from pydantic import ValidationError


@pytest.mark.unit
class TestChangeSignal:
    def test_defaults_are_the_most_conservative_values(self):
        signal = ChangeSignal(article_id="a1", received_at=datetime(2026, 9, 1, 12, 0))
        assert signal.change is ChangeKind.UNKNOWN
        assert signal.actor is ActorKind.UNKNOWN
        assert signal.publish_date is None
        assert signal.status is None
        assert signal.fingerprint is None
        assert signal.raw == {}

    def test_carries_no_priority_or_lane_field(self):
        """Port invariant 1: the signal is evidence; routing is consumer policy."""
        fields = set(ChangeSignal.model_fields)
        assert "priority" not in fields
        assert "lane" not in fields

    def test_is_frozen(self):
        signal = ChangeSignal(article_id="a1", received_at=datetime(2026, 9, 1))
        with pytest.raises(ValidationError):
            signal.article_id = "a2"


@pytest.mark.unit
class TestCoercion:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("create", ChangeKind.CREATE),
            ("CREATE", ChangeKind.CREATE),
            ("update", ChangeKind.UPDATE),
            ("publish", ChangeKind.PUBLISH),
            ("items.create", ChangeKind.UNKNOWN),
            (None, ChangeKind.UNKNOWN),
            ("", ChangeKind.UNKNOWN),
            (17, ChangeKind.UNKNOWN),
            ({"nested": "thing"}, ChangeKind.UNKNOWN),
        ],
    )
    def test_change_kind_degrades_to_unknown(self, raw, expected):
        assert coerce_change_kind(raw) is expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("editor", ActorKind.EDITOR),
            ("BULK", ActorKind.BULK),
            ("import", ActorKind.IMPORT),
            ("migration", ActorKind.MIGRATION),
            ("api", ActorKind.API),
            ("robot", ActorKind.UNKNOWN),
            (None, ActorKind.UNKNOWN),
        ],
    )
    def test_actor_kind_degrades_to_unknown(self, raw, expected):
        assert coerce_actor_kind(raw) is expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2024-01-15", date(2024, 1, 15)),
            ("2024-01-15T10:00:00Z", date(2024, 1, 15)),
            (date(2024, 1, 15), date(2024, 1, 15)),
            ("not-a-date", None),
            (None, None),
            ("", None),
            (42, None),
        ],
    )
    def test_dates_never_raise(self, raw, expected):
        assert coerce_signal_date(raw) == expected
