from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.disclosures.saudi_exchange import Announcement
from saudi_trading_bot.models import Signal, SignalState
from saudi_trading_bot.pead_rescue import _merge_events, _price_confirmed_candidate

RIYADH = ZoneInfo("Asia/Riyadh")


def test_merge_events_preserves_real_published_time():
    now = datetime(2026, 9, 8, 16, 0, tzinfo=RIYADH)
    old = Announcement(
        "3008",
        "Al Kathiri announces Interim Financial results",
        "https://www.saudiexchange.sa/x",
        (now - timedelta(days=1)).isoformat(),
        (now - timedelta(days=1)).replace(hour=8).isoformat(),
    )
    refreshed_without_time = Announcement(
        old.symbol,
        old.title,
        old.url,
        now.isoformat(),
        "",
    )
    merged = _merge_events([old], [refreshed_without_time], now, 12)
    assert len(merged) == 1
    assert merged[0].published_at == old.published_at


def test_price_confirmed_pead_requires_positive_post_event_confirmation():
    dates = pd.date_range("2026-09-01", periods=6, freq="D")
    history = pd.DataFrame(
        {
            "close": [100, 100, 101, 103, 104, 105],
            "open": [100, 100, 101, 102, 103, 104],
            "high": [101, 101, 102, 104, 105, 106],
            "low": [99, 99, 100, 101, 102, 103],
            "volume": [1000] * 6,
        },
        index=dates,
    )
    base = Signal(
        symbol="3008",
        state=SignalState.WATCH,
        total_score=80,
        trend_score=80,
        momentum_score=80,
        swing_score=80,
        disclosure_score=50,
        price=105,
        stop=100,
        target=115,
        atr=2,
        rationale=(),
        generated_at=datetime(2026, 9, 6, 16, tzinfo=RIYADH),
    )
    row = pd.Series(
        {
            "close": 105,
            "ema50": 100,
            "roc20": 5,
            "vol_ratio": 1.2,
            "avg_value20": 5_000_000,
        }
    )
    event = Announcement(
        "3008",
        "Al Kathiri announces Interim Financial results",
        "https://www.saudiexchange.sa/x",
        datetime(2026, 9, 2, 8, tzinfo=RIYADH).isoformat(),
        datetime(2026, 9, 2, 8, tzinfo=RIYADH).isoformat(),
    )
    cfg = {
        "min_sessions_after_announcement": 1,
        "max_sessions_after_announcement": 5,
        "fallback_min_post_event_return_pct": 1.0,
        "fallback_max_post_event_return_pct": 12.0,
        "fallback_min_technical_score": 68,
        "fallback_min_volume_ratio": 1.0,
        "min_avg_value_sar_20d": 1_000_000,
        "atr_stop_multiple": 2.0,
        "reward_risk": 2.0,
    }
    candidate = _price_confirmed_candidate(base, row, history, event, cfg)
    assert candidate is not None
    assert candidate.strategy == "pead_price_confirmed_shadow"
    assert candidate.state == SignalState.READY
