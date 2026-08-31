from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from saudi_trading_bot.models import DisclosureImpact


POSITIVE = (
    "ارتفاع صافي الربح",
    "نمو الأرباح",
    "توزيع أرباح",
    "توصية مجلس الإدارة بتوزيع",
    "ترسية",
    "عقد",
    "استحواذ",
    "increase in net profit",
    "dividend",
    "awarded",
    "contract",
    "acquisition",
    "profit rises",
    "profit jumps",
)
NEGATIVE = (
    "انخفاض صافي الربح",
    "صافي خسارة",
    "تراجع الأرباح",
    "تعليق التداول",
    "إلغاء عقد",
    "انخفاض الإيرادات",
    "الخسائر المتراكمة",
    "net loss",
    "decrease in net profit",
    "suspension",
    "contract termination",
    "accumulated losses",
)


@dataclass(frozen=True)
class Announcement:
    symbol: str
    title: str
    url: str
    fetched_at: str


def classify_disclosure(title: str, url: str = "") -> DisclosureImpact:
    t = title.lower()
    pos = sum(1 for k in POSITIVE if k.lower() in t)
    neg = sum(1 for k in NEGATIVE if k.lower() in t)
    if pos > neg:
        return DisclosureImpact(75.0, "positive", title, url)
    if neg > pos:
        return DisclosureImpact(20.0, "negative", title, url)
    return DisclosureImpact(50.0, "neutral", title, url)


class SaudiExchangeDisclosures:
    """Best-effort public announcement reader with last-known-good local cache."""

    def __init__(
        self,
        url: str,
        cache_file: str | Path,
        timeout: int = 20,
        lookback_days: int = 7,
        fallback_url: str = "",
    ):
        self.urls = [u for u in [url, fallback_url] if u]
        self.cache_file = Path(cache_file)
        self.timeout = timeout
        self.lookback_days = lookback_days
        self.last_error = ""
        self.last_url = ""

    @staticmethod
    def parse(
        html: str,
        base_url: str = "https://www.saudiexchange.sa",
    ) -> list[Announcement]:
        soup = BeautifulSoup(html, "html.parser")
        now = datetime.now(UTC).isoformat(timespec="seconds")
        found: dict[tuple[str, str], Announcement] = {}

        for a in soup.find_all("a", href=True):
            title = " ".join(a.stripped_strings).strip()
            if not _looks_like_announcement(title):
                continue
            nearby = title
            parent = a.parent
            if parent is not None:
                nearby += " " + " ".join(parent.stripped_strings)
                if parent.parent is not None:
                    nearby += " " + " ".join(parent.parent.stripped_strings)
            symbols = _symbols(nearby)
            if not symbols:
                continue
            href = a.get("href", "")
            url = (
                href
                if href.startswith("http")
                else base_url.rstrip("/") + "/" + href.lstrip("/")
            )
            for symbol in symbols:
                found[(symbol, title)] = Announcement(symbol, title, url, now)

        tokens = [" ".join(x.split()) for x in soup.stripped_strings if x.strip()]
        for i, title in enumerate(tokens):
            if not _looks_like_announcement(title):
                continue
            window = " ".join(tokens[i + 1 : i + 6])
            for symbol in _symbols(window):
                found.setdefault((symbol, title), Announcement(symbol, title, "", now))

        return list(found.values())

    def _load_cache(self) -> list[Announcement]:
        if not self.cache_file.exists():
            return []
        try:
            raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
            return [Announcement(**x) for x in raw.get("announcements", [])]
        except (OSError, TypeError, ValueError):
            return []

    def refresh(self) -> list[Announcement]:
        errors: list[str] = []
        for url in self.urls:
            try:
                r = requests.get(
                    url,
                    timeout=self.timeout,
                    headers={"User-Agent": "SaudiTradingBot/0.2 (+paper-research)"},
                )
                r.raise_for_status()
                items = self.parse(r.text)
                if not items:
                    raise ValueError("announcement page parsed zero items")
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "updated_at": datetime.now(UTC).isoformat(),
                    "source_url": url,
                    "announcements": [asdict(x) for x in items],
                }
                self.cache_file.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.last_error = ""
                self.last_url = url
                return items
            except (OSError, ValueError, requests.RequestException) as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
        self.last_error = " | ".join(errors)
        self.last_url = "cache"
        return self._load_cache()

    def impact_for(
        self,
        symbol: str,
        announcements: list[Announcement],
    ) -> DisclosureImpact | None:
        cutoff = datetime.now(UTC) - timedelta(days=self.lookback_days)
        matches = []
        for announcement in announcements:
            if announcement.symbol != str(symbol):
                continue
            try:
                fetched = datetime.fromisoformat(announcement.fetched_at)
            except ValueError:
                continue
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=UTC)
            if fetched >= cutoff:
                matches.append(announcement)
        if not matches:
            return None
        scored = [classify_disclosure(a.title, a.url) for a in matches]
        scored.sort(key=lambda x: abs(x.score - 50), reverse=True)
        return scored[0]


def _looks_like_announcement(text: str) -> bool:
    if len(text) < 18:
        return False
    t = text.lower()
    return any(
        k in t
        for k in (
            "announce",
            "announcement",
            "تعلن",
            "إعلان",
            "results",
            "contract",
            "dividend",
        )
    )


def _symbols(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"(?<!\d)(\d{4})(?!\d)", text)))
