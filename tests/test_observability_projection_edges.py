from trading_core.observability import make_activity, project_current_status


NOW_MS = 1788148200000
NOW_ISO = "2026-08-31T03:50:00Z"


def market_activity(stage="MARKET_INGEST"):
    return make_activity(
        activity_id=f"market-{stage}",
        occurred_at=NOW_ISO,
        actor_type="AZURE",
        actor_name="market-runtime",
        stage=stage,
        event_type="MARKET_UPDATED",
        status="SUCCESS",
        event_id="market-MES-1788148140000",
        correlation_id="market-MES-1788148140000",
        symbol="MES",
        producer_version="v1",
    )


def test_market_activity_never_becomes_current_trading_event():
    doc = project_current_status(
        activities=[market_activity()],
        now_ms=NOW_MS,
        market={"ingest_healthy": True, "build_healthy": True, "symbols": {}},
        paper={},
        position={"side": "FLAT", "quantity": 0},
        projection={"watermark": NOW_MS},
    )
    assert doc["current_event"] is None
    assert all(item["status"] == "IDLE" for item in doc["pipeline"][2:])


def test_unknown_market_build_health_makes_overall_degraded_not_healthy():
    doc = project_current_status(
        activities=[market_activity()],
        now_ms=NOW_MS,
        market={"ingest_healthy": True, "build_healthy": None, "symbols": {}},
        paper={},
        position={"side": "FLAT", "quantity": 0},
        projection={"watermark": NOW_MS},
    )
    assert doc["overall"] == "DEGRADED"
    assert doc["pipeline"][0]["status"] == "HEALTHY"
    assert doc["pipeline"][1]["status"] == "WAITING"
