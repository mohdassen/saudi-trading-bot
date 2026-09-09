from __future__ import annotations

from saudi_trading_bot import pead_verified_rescue as verified
from saudi_trading_bot.pead_mubasher import parse_mubasher_financial_events


def run() -> int:
    # Keep the existing detail-page verification/fundamental parsing pipeline,
    # but replace only the brittle listing discovery stage.
    verified._parse_mubasher_financial_events = parse_mubasher_financial_events
    return verified.run()


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
