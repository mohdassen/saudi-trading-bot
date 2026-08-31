from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import pandas as pd
import requests
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class TasiRegimeResult:
    allowed: bool
    note: str
    reference_close: float | None = None
    yahoo_close: float | None = None
    ema200: float | None = None
    gap_pct: float | None = None


class TasiRegimeVerifier:
    """Verify TASI trend using two independent free public sources.

    Historical series comes from Yahoo's chart endpoint directly rather than
    yfinance. The most recent close is cross-checked against Mubasher. Any
    missing, stale, or materially inconsistent data fails closed.
    """

    def __init__(
        self,
        symbol: str = "^TASI.SR",
        yahoo_chart_url: str = "https://query1.finance.yahoo.com/v8/finance/chart",
        reference_url: str = "https://english.mubasher.info/markets/TDWL/indices/TASI/",
        timeout_seconds: int = 15,
        min_history_rows: int = 220,
        max_reference_gap_pct: float = 1.5,
        max_history_age_days: int = 7,
    ) -> None:
        self.symbol = symbol
        self.yahoo_chart_url = yahoo_chart_url.rstrip("/")
        self.reference_url = reference_url
        self.timeout_seconds = timeout_seconds
        self.min_history_rows = min_history_rows
        self.max_reference_gap_pct = max_reference_gap_pct
        self.max_history_age_days = max_history_age_days
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/124.0 Safari/537.36"
            )
        }

    @staticmethod
    def _history_from_payload(payload: dict) -> pd.DataFrame:
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            return pd.DataFrame()
        timestamps = result.get("timestamp") or []
        quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes = quote_data.get("close") or []
        rows: list[dict] = []
        for timestamp, close in zip(timestamps, closes, strict=False):
            if close is None:
                continue
            rows.append(
                {
                    "date": datetime.fromtimestamp(timestamp, tz=timezone.utc),
                    "close": float(close),
                }
            )
        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame(rows).set_index("date").sort_index()
        return frame[~frame.index.duplicated(keep="last")]

    @staticmethod
    def _reference_close_from_html(html: str) -> float | None:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        patterns = [
            r"Last update:.*?([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2})",
            r"Tadawul All Share Index \(TASI\).*?([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                return float(match.group(1).replace(",", ""))
        return None

    def fetch_history(self) -> pd.DataFrame:
        encoded_symbol = quote(self.symbol, safe="")
        response = requests.get(
            f"{self.yahoo_chart_url}/{encoded_symbol}",
            params={"range": "2y", "interval": "1d", "includePrePost": "false"},
            headers=self.headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return self._history_from_payload(response.json())

    def fetch_reference_close(self) -> float | None:
        response = requests.get(
            self.reference_url,
            headers=self.headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return self._reference_close_from_html(response.text)

    def evaluate(self) -> TasiRegimeResult:
        try:
            history = self.fetch_history()
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return TasiRegimeResult(False, f"TASI Yahoo chart unavailable: {type(exc).__name__}")

        if history.empty or len(history) < self.min_history_rows:
            return TasiRegimeResult(
                False,
                f"TASI history insufficient ({len(history)})",
            )

        newest = history.index[-1].to_pydatetime()
        age_days = (datetime.now(timezone.utc).date() - newest.date()).days
        if age_days > self.max_history_age_days:
            return TasiRegimeResult(False, f"TASI history stale ({age_days} days)")

        yahoo_close = float(history["close"].iloc[-1])
        ema200 = float(history["close"].ewm(span=200, adjust=False).mean().iloc[-1])

        try:
            reference_close = self.fetch_reference_close()
        except requests.RequestException as exc:
            return TasiRegimeResult(
                False,
                f"TASI reference unavailable: {type(exc).__name__}",
                yahoo_close=yahoo_close,
                ema200=ema200,
            )

        if reference_close is None or reference_close <= 0:
            return TasiRegimeResult(
                False,
                "TASI reference close unavailable",
                yahoo_close=yahoo_close,
                ema200=ema200,
            )

        gap_pct = abs(yahoo_close - reference_close) / reference_close * 100
        if gap_pct > self.max_reference_gap_pct:
            return TasiRegimeResult(
                False,
                (
                    f"TASI source mismatch Yahoo={yahoo_close:.2f} "
                    f"Mubasher={reference_close:.2f} gap={gap_pct:.2f}%"
                ),
                reference_close=reference_close,
                yahoo_close=yahoo_close,
                ema200=ema200,
                gap_pct=gap_pct,
            )

        allowed = reference_close >= ema200
        state = "above" if allowed else "below"
        return TasiRegimeResult(
            allowed,
            (
                f"TASI verified {reference_close:.2f} {state} EMA200 {ema200:.2f}; "
                f"Yahoo/Mubasher gap {gap_pct:.2f}%"
            ),
            reference_close=reference_close,
            yahoo_close=yahoo_close,
            ema200=ema200,
            gap_pct=gap_pct,
        )
