from pathlib import Path


CONTRACT = Path("docs/contracts/simple-paper-brain-job-v1.md")


def test_position_updated_at_is_not_a_brain_freshness_heartbeat():
    text = CONTRACT.read_text(encoding="utf-8")

    assert "Position.updated_at is a state-change timestamp, not a liveness heartbeat." in text
    assert "must not classify a valid Position mirror as stale solely because `updated_at` is old" in text


def test_valid_flat_position_age_must_not_block_open_candidate():
    text = CONTRACT.read_text(encoding="utf-8")

    assert "a valid exact-path `FLAT` Position may be used to generate an `OPEN` candidate regardless of the age of `updated_at`" in text
    assert "Azure must re-check the current authoritative Position before executing that candidate" in text


def test_open_position_safety_uses_exact_identity_and_version_not_timestamp_age():
    text = CONTRACT.read_text(encoding="utf-8")

    assert "For `OPEN`, bind to the exact observed `position_id` and `position_version`; `updated_at` age alone is not a blocker." in text
