from datetime import datetime
from zoneinfo import ZoneInfo

from saudi_trading_bot.pead_verified_runner import mubasher_published_at

RIYADH = ZoneInfo("Asia/Riyadh")


def test_bare_market_time_is_today_in_riyadh():
    now = datetime(2026, 9, 9, 20, 0, tzinfo=RIYADH)
    context = "08:00 AM Saudi Stock Exchange 08:01 AM Saudi Stock Exchange"

    parsed = mubasher_published_at(context, now)

    assert parsed == datetime(2026, 9, 9, 8, 1, tzinfo=RIYADH)


def test_existing_dated_parser_still_wins():
    now = datetime(2026, 9, 9, 20, 0, tzinfo=RIYADH)
    context = "8 September 08:01 AM Saudi Stock Exchange"

    parsed = mubasher_published_at(context, now)

    assert parsed == datetime(2026, 9, 8, 8, 1, tzinfo=RIYADH)
