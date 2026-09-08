from datetime import datetime
from zoneinfo import ZoneInfo

from saudi_trading_bot.freshness import expected_completed_session, is_fresh_session

RIYADH = ZoneInfo("Asia/Riyadh")


def test_before_close_expects_previous_session() -> None:
    now = datetime(2026, 9, 9, 10, 0, tzinfo=RIYADH)
    assert expected_completed_session(now).isoformat() == "2026-09-08"


def test_weekend_rolls_back() -> None:
    now = datetime(2026, 9, 12, 18, 0, tzinfo=RIYADH)
    assert expected_completed_session(now).isoformat() == "2026-09-10"


def test_one_session_tolerance() -> None:
    now = datetime(2026, 9, 9, 18, 0, tzinfo=RIYADH)
    assert is_fresh_session(datetime(2026, 9, 8).date(), now, tolerance_sessions=1)
