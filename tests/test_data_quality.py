from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.data.quality import (
    expected_completed_session,
    validate_market_data,
)

RIYADH = ZoneInfo("Asia/Riyadh")


def _history(latest: str) -> pd.DataFrame:
    return pd.DataFrame({"close": [10.0]}, index=pd.to_datetime([latest]))


def test_expected_session_after_close_and_weekend():
    assert expected_completed_session(
        datetime(2026, 9, 1, 16, 0, tzinfo=RIYADH)
    ).isoformat() == "2026-09-01"
    assert expected_completed_session(
        datetime(2026, 9, 4, 16, 0, tzinfo=RIYADH)
    ).isoformat() == "2026-09-03"


def test_stale_market_wide_data_blocks():
    histories = {str(i): _history("2026-08-27") for i in range(200)}
    result = validate_market_data(
        histories,
        datetime(2026, 9, 1, 16, 0, tzinfo=RIYADH),
        min_symbols=150,
        min_consensus_pct=80,
    )
    assert not result.allowed
    assert "stale session" in result.note


def test_fresh_consensus_passes():
    histories = {str(i): _history("2026-09-01") for i in range(180)}
    histories.update({f"old-{i}": _history("2026-08-31") for i in range(20)})
    result = validate_market_data(
        histories,
        datetime(2026, 9, 1, 16, 0, tzinfo=RIYADH),
        min_symbols=150,
        min_consensus_pct=80,
    )
    assert result.allowed
    assert result.consensus_symbols == 180


def test_low_coverage_blocks():
    result = validate_market_data(
        {str(i): _history("2026-09-01") for i in range(149)},
        datetime(2026, 9, 1, 16, 0, tzinfo=RIYADH),
        min_symbols=150,
        min_consensus_pct=80,
    )
    assert not result.allowed
    assert "coverage" in result.note
