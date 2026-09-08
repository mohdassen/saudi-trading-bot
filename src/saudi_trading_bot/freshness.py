from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

RIYADH = ZoneInfo("Asia/Riyadh")


def previous_trading_day(day: date) -> date:
    d = day
    while d.weekday() in (4, 5):  # Friday, Saturday
        d -= timedelta(days=1)
    return d


def expected_completed_session(now: datetime | None = None) -> date:
    now = now.astimezone(RIYADH) if now else datetime.now(RIYADH)
    local_day = now.date()
    # Before continuous market close, the latest fully completed daily candle
    # should be the prior Saudi trading session, not today's still-forming bar.
    if now.time() < time(15, 20):
        local_day -= timedelta(days=1)
    return previous_trading_day(local_day)


def is_fresh_session(latest: date, now: datetime | None = None, tolerance_sessions: int = 1) -> bool:
    expected = expected_completed_session(now)
    if latest >= expected:
        return True
    probe = expected
    for _ in range(tolerance_sessions):
        probe = previous_trading_day(probe - timedelta(days=1))
        if latest >= probe:
            return True
    return False
