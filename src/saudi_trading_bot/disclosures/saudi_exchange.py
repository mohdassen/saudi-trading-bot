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

_COMPANY_STOPWORDS = {
    "and",
    "co",
    "company",
    "for",
    "group",
    "holding",
    "holdings",
    "limited",
    "ltd",
    "of",
    "the",
}


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
    """Official-first announcements with a zero-cost public-page fallback."""

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

        for anchor in soup.find_all("a", href=True):
            title = " ".join(anchor.stripped_strings).strip()
            if not _looks_like_announcement(title):
                continue
            nearby = title
            parent = anchor.parent
            if parent is not None:
                nearby += " " + " ".join(parent.stripped_strings)
                if parent.parent is not None:
                    nearby += " " + " ".join(parent.parent.stripped_strings)
            symbols = _symbols(nearby)
            if not symbols:
                continue
            href = anchor.get("href", "")
            url = (
                href
                if href.startswith("http")
                else base_url.rstrip("/") + "/" + href.lstrip("/")
            )
            for symbol in symbols:
                found[(symbol, title)] = Announcement(symbol, title, url, now)

        tokens = [" ".join(x.split()) for x in soup.stripped_strings if x.strip()]
        for index, title in enumerate(tokens):
            if not _looks_like_announcement(title):
                continue
            window = " ".join(tokens[index + 1 : index + 6])
            for symbol in _symbols(window):
                found.setdefault((symbol, title), Announcement(symbol, title, "", now))

        return list(found.values())

    @staticmethod
    def parse_mubasher(
        html: str,
        base_url: str = "https://english.mubasher.info",
    ) -> list[Announcement]:
        """Parse only public market-announcement headlines; no login/API is used."""
        soup = BeautifulSoup(html, "html.parser")
        now = datetime.now(UTC).isoformat(timespec="seconds")
        found: dict[str, Announcement] = {}
        for anchor in soup.find_all("a", href=True):
            title = " ".join(anchor.stripped_strings).strip()
            href = str(anchor.get("href", ""))
            if "/news/" not in href or not _looks_like_announcement(title):
                continue
            url = (
                href
                if href.startswith("http")
                else base_url.rstrip("/") + "/" + href.lstrip("/")
            )
            found.setdefault(title, Announcement("", title, url, now))
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
                response = requests.get(
                    url,
                    timeout=self.timeout,
                    headers={"User-Agent": "SaudiTradingBot/0.2 (+paper-research)"},
                )
                response.raise_for_status()
                items = (
                    self.parse_mubasher(response.text)
                    if "mubasher.info" in url
                    else self.parse(response.text)
                )
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
                self.last_error = "" if url == self.urls[0] else "official source unavailable"
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
        company_name: str = "",
    ) -> DisclosureImpact | None:
        cutoff = datetime.now(UTC) - timedelta(days=self.lookback_days)
        matches = []
        for announcement in announcements:
            symbol_match = announcement.symbol == str(symbol)
            name_match = (
                not announcement.symbol
                and bool(company_name)
                and _company_matches(company_name, announcement.title)
            )
            if not symbol_match and not name_match:
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


def _company_matches(company_name: str, title: str) -> bool:
    company_tokens = _meaningful_tokens(company_name)
    if not company_tokens:
        return False
    title_tokens = set(_meaningful_tokens(title))
    required = min(2, len(set(company_tokens)))
    return len(set(company_tokens) & title_tokens) >= required


def _meaningful_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [
        word
        for word in words
        if len(word) >= 3 and word not in _COMPANY_STOPWORDS
    ]


def _looks_like_announcement(text: str) -> bool:
    if len(text) < 18:
        return False
    lowered = text.lower()
    return any(
        keyword in lowered
        for keyword in (
            "announce",
            "announcement",
            "award",
            "contract",
            "dividend",
            "financial result",
            "loss",
            "profit",
            "resign",
            "تعلن",
            "إعلان",
            "نتائج",
            "أرباح",
            "خسائر",
        )
    )


def _symbols(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"(?<!\d)(\d{4})(?!\d)", text)))
