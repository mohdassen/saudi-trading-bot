from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.disclosures.saudi_exchange import Announcement
from saudi_trading_bot.models import Signal, SignalState
from saudi_trading_bot.pead_rescue import (
    _merge_events,
    _parse_mubasher_financial_events,
    _price_confirmed_candidate,
    _resolve_mubasher_symbol,
)

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


def test_mubasher_financial_event_resolves_symbol_and_relative_time():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=RIYADH)
    html = """
    <div class="announcement">
      <span>2 hours ago</span>
      <span>Saudi Stock Exchange</span>
      <a href="/news/4652442/example/news">
        Flynas Co. announces its Interim Financial results for the Period
        Ending on 2026-06-30 ( Six Months )
      </a>
    </div>
    """
    events = _parse_mubasher_financial_events(
        html,
        now,
        [("4264", "FLYNAS")],
    )
    assert len(events) == 1
    assert events[0].symbol == "4264"
    assert events[0].url.startswith("https://english.mubasher.info/news/")
    assert datetime.fromisoformat(events[0].published_at) == now - timedelta(hours=2)


def test_mubasher_parser_rejects_non_exchange_news():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=RIYADH)
    html = """
    <div>
      <span>2 hours ago</span>
      <span>Mubasher Exclusive</span>
      <a href="/news/1/example/news">
        Flynas Co. announces its Interim Financial results for Six Months
      </a>
    </div>
    """
    assert not _parse_mubasher_financial_events(
        html,
        now,
        [("4264", "FLYNAS")],
    )


def test_mubasher_symbol_resolution_refuses_ambiguous_names():
    title = "Alpha Beta Co. announces its Interim Financial Results"
    companies = [("1001", "Alpha Beta"), ("1002", "Alpha Beta Holdings")]
    assert _resolve_mubasher_symbol(title, companies) == ""


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
