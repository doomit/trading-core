from datetime import datetime, timedelta, timezone

import pytest

from trading_core.event_orchestrator import EventIdentityConflict, start_or_resume_event


NOW = datetime(2026, 9, 3, 8, 34, tzinfo=timezone.utc)
EVENT_ID = "evt_brain_MES1_state_version_event"


class FakeRepository:
    def __init__(self):
        self.events = {}

    def get_event(self, event_id):
        return self.events.get(event_id)

    def create_event(self, record):
        if record.event_id in self.events:
            return False
        self.events[record.event_id] = record
        return True


def test_event_persists_dispatched_state_version_as_immutable_identity():
    repo = FakeRepository()
    first, created = start_or_resume_event(
        repo,
        event_id=EVENT_ID,
        symbol="MES1!",
        state_version="ctx_expected",
        created_at=NOW,
        deadline=NOW + timedelta(seconds=90),
    )

    assert created is True
    assert first.state_version == "ctx_expected"

    resumed, created_again = start_or_resume_event(
        repo,
        event_id=EVENT_ID,
        symbol="MES1!",
        state_version="ctx_expected",
        created_at=NOW + timedelta(seconds=10),
        deadline=NOW + timedelta(seconds=120),
    )
    assert created_again is False
    assert resumed == first

    with pytest.raises(EventIdentityConflict):
        start_or_resume_event(
            repo,
            event_id=EVENT_ID,
            symbol="MES1!",
            state_version="ctx_wrong",
            created_at=NOW + timedelta(seconds=20),
            deadline=NOW + timedelta(seconds=130),
        )
