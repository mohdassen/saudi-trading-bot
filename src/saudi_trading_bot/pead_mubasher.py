from __future__ import annotations

from datetime import datetime

from bs4 import BeautifulSoup

from saudi_trading_bot.disclosures.saudi_exchange import Announcement
from saudi_trading_bot.pead_rescue import (
    RIYADH,
    UTC,
    _financial_title,
    _mubasher_published_at,
    _resolve_mubasher_symbol,
)


def parse_mubasher_financial_events(
    html: str,
    now: datetime,
    companies: list[tuple[str, str]],
) -> list[Announcement]:
    """Discover Saudi financial-result links from Mubasher listing HTML.

    Discovery deliberately does not require the fragile literal text
    ``Saudi Stock Exchange`` near every anchor. The caller must still verify
    each discovered detail page before using it as a PEAD event.
    """
    soup = BeautifulSoup(html, "html.parser")
    fetched = now.astimezone(UTC).isoformat(timespec="seconds")
    found: dict[tuple[str, str], Announcement] = {}

    for anchor in soup.find_all("a", href=True):
        title = " ".join(anchor.stripped_strings).strip()
        href = str(anchor.get("href", ""))
        if "/news/" not in href or not _financial_title(title):
            continue

        symbol = _resolve_mubasher_symbol(title, companies)
        if not symbol:
            continue

        # Prefer the local card/container because modern Mubasher layouts no
        # longer guarantee a nearby publisher label. Fall back to preceding
        # text nodes to support the older layout.
        contexts: list[str] = []
        parent = anchor.parent
        for _ in range(4):
            if parent is None:
                break
            text = " ".join(parent.stripped_strings).strip()
            if text:
                contexts.append(text)
            parent = parent.parent

        previous: list[str] = []
        for node in anchor.find_all_previous(string=True, limit=16):
            cleaned = " ".join(str(node).split())
            if cleaned and cleaned != title:
                previous.append(cleaned)
        if previous:
            contexts.append(" ".join(reversed(previous)))

        published = None
        for context in contexts:
            published = _mubasher_published_at(context, now)
            if published is not None:
                break
        if published is None:
            continue

        url = (
            href
            if href.startswith("http")
            else "https://english.mubasher.info/" + href.lstrip("/")
        )
        found[(symbol, title)] = Announcement(
            symbol=symbol,
            title=title,
            url=url,
            fetched_at=fetched,
            published_at=published.astimezone(RIYADH).isoformat(),
        )

    return list(found.values())
