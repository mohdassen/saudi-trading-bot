from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class TasiRegimeResult:
    allowed: bool
    note: str
    primary_close: float | None = None
    reference_close: float | None = None
    market_ma: float | None = None
    gap_pct: float | None = None


class TasiRegimeVerifier:
    """Fail-closed TASI market-regime check using two free public sources.

    MarketScreener supplies the delayed TASI close and its published 100-day
    moving average. Mubasher independently supplies the delayed TASI close.
    The gate opens only when both closes agree within a configured tolerance
    and TASI is at/above its 100-day moving average.

    We intentionally do not use Yahoo for TASI because its index endpoints are
    unreliable from GitHub-hosted runners even though Saudi equity symbols work.
    """

    def __init__(
        self,
        primary_url: str = (
            "https://sa.marketscreener.com/quote/index/"
            "TADAWUL-ALL-SHARE-169754563/quotes/"
        ),
        reference_url: str = "https://english.mubasher.info/markets/TDWL/indices/TASI/",
        timeout_seconds: int = 15,
        max_reference_gap_pct: float = 1.5,
    ) -> None:
        self.primary_url = primary_url
        self.reference_url = reference_url
        self.timeout_seconds = timeout_seconds
        self.max_reference_gap_pct = max_reference_gap_pct
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

    @staticmethod
    def _number(value: str) -> float:
        return float(value.replace(",", ""))

    @classmethod
    def _marketscreener_from_html(cls, html: str) -> tuple[float | None, float | None]:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

        close_patterns = [
            r"TASI\s+Market Closed.*?([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2})\s*(?:PTS|points)",
            r"Tadawul All Share.*?Market Closed.*?([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2})",
        ]
        close = None
        for pattern in close_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                close = cls._number(match.group(1))
                break

        ma_match = re.search(
            r"Moving average 100 days\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        ma100 = cls._number(ma_match.group(1)) if ma_match else None
        return close, ma100

    @classmethod
    def _mubasher_close_from_html(cls, html: str) -> float | None:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        patterns = [
            r"Last update:.*?([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2})",
            r"Tadawul All Share Index \(TASI\).*?([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2})",
            r"TASI.*?([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                return cls._number(match.group(1))
        return None

    def fetch_primary(self) -> tuple[float | None, float | None]:
        response = requests.get(
            self.primary_url,
            headers=self.headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return self._marketscreener_from_html(response.text)

    def fetch_reference_close(self) -> float | None:
        response = requests.get(
            self.reference_url,
            headers=self.headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return self._mubasher_close_from_html(response.text)

    def evaluate(self) -> TasiRegimeResult:
        try:
            primary_close, ma100 = self.fetch_primary()
        except requests.RequestException as exc:
            return TasiRegimeResult(
                False,
                f"TASI MarketScreener unavailable: {type(exc).__name__}",
            )

        if primary_close is None or primary_close <= 0:
            return TasiRegimeResult(False, "TASI MarketScreener close unavailable")
        if ma100 is None or ma100 <= 0:
            return TasiRegimeResult(
                False,
                "TASI MarketScreener MA100 unavailable",
                primary_close=primary_close,
            )

        try:
            reference_close = self.fetch_reference_close()
        except requests.RequestException as exc:
            return TasiRegimeResult(
                False,
                f"TASI Mubasher unavailable: {type(exc).__name__}",
                primary_close=primary_close,
                market_ma=ma100,
            )

        if reference_close is None or reference_close <= 0:
            return TasiRegimeResult(
                False,
                "TASI Mubasher close unavailable",
                primary_close=primary_close,
                market_ma=ma100,
            )

        gap_pct = abs(primary_close - reference_close) / reference_close * 100
        if gap_pct > self.max_reference_gap_pct:
            return TasiRegimeResult(
                False,
                (
                    f"TASI source mismatch MarketScreener={primary_close:.2f} "
                    f"Mubasher={reference_close:.2f} gap={gap_pct:.2f}%"
                ),
                primary_close=primary_close,
                reference_close=reference_close,
                market_ma=ma100,
                gap_pct=gap_pct,
            )

        allowed = reference_close >= ma100
        state = "above" if allowed else "below"
        return TasiRegimeResult(
            allowed,
            (
                f"TASI verified {reference_close:.2f} {state} MA100 {ma100:.2f}; "
                f"MarketScreener/Mubasher gap {gap_pct:.2f}%"
            ),
            primary_close=primary_close,
            reference_close=reference_close,
            market_ma=ma100,
            gap_pct=gap_pct,
        )
