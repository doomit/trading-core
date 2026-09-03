from __future__ import annotations

import itertools
from collections.abc import Iterable, Iterator, Mapping
from typing import Any


def fresh_first_bounded_candidates(
    *,
    fresh_entities: Iterable[Mapping[str, Any]],
    fallback_entities: Iterable[Mapping[str, Any]],
    batch_size: int,
) -> Iterator[Mapping[str, Any]]:
    """Yield fresh PLAN_READY candidates before fallback rows, without duplicates."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    seen_event_ids: set[str] = set()
    emitted = 0
    for entity in itertools.chain(fresh_entities, fallback_entities):
        event_id = entity.get("event_id")
        if isinstance(event_id, str) and event_id:
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)

        yield entity
        emitted += 1
        if emitted >= batch_size:
            return
