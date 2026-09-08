from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .base import MarketDataProvider


class ArgaamSaudiProvider(MarketDataProvider):
    """Free Saudi-specific daily OHLC rescue feed.

    This provider is deliberately secondary. It is only called when Yahoo is
    stale/empty and never overrides a fresher primary bar. No synthetic bars
    are created.
    """

    BASE = "https://www.argaam.com"
    COMPANIES_URL = f"{BASE}/en/company/companies-prices"
    SYMBOL_RE = re.compile(r"\b(\d{4})\s*-\s*")

    def __init__(self, timeout: int = 12) -> None:
        self.timeout = timeout
        self._symbol_urls: dict[str, str] | None = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    @staticmethod
    def _number(value: str) -> float:
        text = value.replace(",", "").replace("%", "").strip()
        if text in {"", "-", "--"}:
            return float("nan")
        return float(text)

    @classmethod
    def parse_symbol_map(cls, html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")
        result: dict[str, str] = {}
        for anchor in soup.find_all("a", href=True):
            text = " ".join(anchor.stripped_strings)
            match = cls.SYMBOL_RE.search(text)
            if not match:
                continue
            href = str(anchor.get("href", ""))
            if "/tadawul/" not in href:
                continue
            symbol = match.group(1)
            root = href.split("?", 1)[0].rstrip("/")
            result[symbol] = urljoin(cls.BASE, root + "/chart?isNoHeaderFooter=true")
        return result

    @classmethod
    def parse_chart(cls, html: str) -> pd.DataFrame:
        soup = BeautifulSoup(html, "html.parser")
        rows: list[dict[str, object]] = []
        for table in soup.find_all("table"):
            headers = [" ".join(x.stripped_strings).strip().lower() for x in table.find_all("th")]
            if not headers:
                continue
            required = {"date", "open", "high", "low"}
            if not required.issubset(set(headers)):
                continue
            close_key = "price" if "price" in headers else "close" if "close" in headers else None
            if close_key is None:
                continue
            for tr in table.find_all("tr"):
                cells = [" ".join(x.stripped_strings).strip() for x in tr.find_all("td")]
                if len(cells) != len(headers):
                    continue
                item = dict(zip(headers, cells, strict=True))
                try:
                    dt = pd.to_datetime(item["date"], dayfirst=True, errors="raise")
                    rows.append(
                        {
                            "date": dt,
                            "open": cls._number(item["open"]),
                            "high": cls._number(item["high"]),
                            "low": cls._number(item["low"]),
                            "close": cls._number(item[close_key]),
                            "volume": cls._number(item.get("volume", "0")),
                        }
                    )
                except (ValueError, TypeError, OverflowError):
                    continue
            if rows:
                break
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame(rows).set_index("date").sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
        return frame.dropna(subset=["open", "high", "low", "close"])

    def _symbols(self) -> dict[str, str]:
        if self._symbol_urls is not None:
            return self._symbol_urls
        response = self.session.get(self.COMPANIES_URL, timeout=self.timeout)
        response.raise_for_status()
        self._symbol_urls = self.parse_symbol_map(response.text)
        if not self._symbol_urls:
            raise RuntimeError("Argaam symbol map returned no Saudi symbols")
        return self._symbol_urls

    def history(
        self,
        symbol: str,
        start: date,
        end: date,
        interval: str = "1d",
    ) -> pd.DataFrame:
        if interval != "1d":
            return pd.DataFrame()
        clean = str(symbol).removesuffix(".SR")
        url = self._symbols().get(clean)
        if not url:
            return pd.DataFrame()
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        frame = self.parse_chart(response.text)
        if frame.empty:
            return frame
        dates = pd.DatetimeIndex(frame.index).date
        return frame.loc[(dates >= start) & (dates < end)]
