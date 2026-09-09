from __future__ import annotations

import re
from datetime import datetime

from saudi_trading_bot import pead_rescue, pead_verified_rescue

_BARE_MARKET_TIME = re.compile(
    r"(?i)(\d{1,2}:\d{2}\s*[AP]M)(?=\s+Saudi\s+Stock\s+Exchange\b)"
)


def mubasher_published_at(text: str, now: datetime) -> datetime | None:
    """Parse Mubasher listing timestamps including same-day bare clock values.

    Mubasher renders today's market announcements as e.g.
    ``08:01 AM Saudi Stock Exchange``. The legacy parser handles relative,
    Today/Yesterday and dated values; this wrapper adds only that missing,
    Saudi-market-specific representation and otherwise preserves old behavior.
    """
    parsed = pead_rescue._mubasher_published_at(text, now)
    if parsed is not None:
        return parsed

    matches = list(_BARE_MARKET_TIME.finditer(text))
    if not matches:
        return None

    # The listing parser builds context from preceding DOM text. The nearest
    # announcement timestamp is therefore the last matching market clock.
    parsed_clock = datetime.strptime(
        matches[-1].group(1).upper(), "%I:%M %p"
    ).replace(tzinfo=pead_rescue.RIYADH)
    local_now = now.astimezone(pead_rescue.RIYADH)
    return datetime.combine(
        local_now.date(),
        parsed_clock.timetz(),
        tzinfo=pead_rescue.RIYADH,
    )


def run() -> int:
    # _parse_mubasher_financial_events resolves this global at call time, so
    # patching it here keeps the verified PEAD implementation unchanged.
    pead_rescue._mubasher_published_at = mubasher_published_at
    return pead_verified_rescue.run()


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
