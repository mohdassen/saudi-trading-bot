from __future__ import annotations

from saudi_trading_bot import pead_mubasher
from saudi_trading_bot import pead_verified_rescue as verified
from saudi_trading_bot.pead_verified_runner import mubasher_published_at


def run() -> int:
    # Keep the existing detail-page verification/fundamental parsing pipeline,
    # but replace only the brittle listing discovery stage. Mubasher renders
    # same-day announcements with a bare clock (e.g. 08:01 AM), so patch the
    # listing parser's timestamp resolver before discovery.
    pead_mubasher._mubasher_published_at = mubasher_published_at
    verified._parse_mubasher_financial_events = (
        pead_mubasher.parse_mubasher_financial_events
    )
    return verified.run()


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
