from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.disclosures.financials import EarningsSnapshot
from saudi_trading_bot.pead_diagnostics import diagnose_pead

RIYADH = ZoneInfo("Asia/Riyadh")


def test_diagnostics_explain_failed_pead_gates():
    index = pd.to_datetime(["2026-09-07", "2026-09-08", "2026-09-09"])
    history = pd.DataFrame(
        {
            "close": [100.0, 100.0, 98.0],
            "high": [101.0, 101.0, 99.0],
            "low": [99.0, 99.0, 97.0],
            "open": [100.0, 100.0, 98.0],
            "volume": [1000, 1000, 1000],
        },
        index=index,
    )
    row = pd.Series({"close": 98.0, "ema50": 99.0, "avg_value20": 500000.0})
    snapshot = EarningsSnapshot(
        symbol="1234",
        url="https://example.test/event",
        published_at=datetime(2026, 9, 8, 8, 0, tzinfo=RIYADH).isoformat(),
        revenue_yoy_pct=-5.0,
        operating_yoy_pct=-10.0,
        net_yoy_pct=-20.0,
        net_qoq_pct=-25.0,
        net_current=10.0,
        net_previous_year=12.5,
        net_previous_quarter=13.3,
        earnings_score=25.0,
    )
    cfg = {
        "min_sessions_after_announcement": 1,
        "max_sessions_after_announcement": 5,
        "min_earnings_score": 70,
        "min_avg_value_sar_20d": 1000000,
        "min_technical_score": 60,
        "min_post_event_return_pct": -2.0,
        "max_post_event_return_pct": 15.0,
    }

    result = diagnose_pead(
        SimpleNamespace(total_score=55.0), row, history, snapshot, cfg
    )

    assert result["passed"] is False
    reasons = " | ".join(result["reasons"])
    assert "earnings_score_low" in reasons
    assert "below_ema50" in reasons
    assert "liquidity_low" in reasons
    assert "technical_score_low" in reasons
