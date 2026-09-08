from __future__ import annotations

from datetime import date

import pandas as pd

from .base import MarketDataProvider


class YahooSaudiProvider(MarketDataProvider):
    """Research/development adapter. Not an official Saudi Exchange market-data feed.

    OHLC is adjusted for splits/dividends so momentum and backtests are not
    corrupted by historical corporate-action jumps. The latest bar remains on
    today's price scale, so Paper entry/stop calculations stay interpretable.
    """

    def __init__(self, suffix: str = ".SR") -> None:
        self.suffix = suffix

    def ticker_for(self, symbol: str) -> str:
        """Return the Yahoo ticker without duplicating an existing market suffix."""
        symbol = str(symbol).strip()
        if symbol.startswith("^") or symbol.endswith(self.suffix):
            return symbol
        return f"{symbol}{self.suffix}"

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.rename(columns=str.lower)
        keep = [
            c
            for c in ["open", "high", "low", "close", "volume"]
            if c in df.columns
        ]
        result = df[keep].dropna(subset=["close"]).copy()
        # yfinance may return timezone-aware indexes through Ticker.history.
        try:
            result.index = pd.DatetimeIndex(result.index).tz_localize(None)
        except TypeError:
            result.index = pd.DatetimeIndex(result.index).tz_convert(None)
        return result

    def history(
        self,
        symbol: str,
        start: date,
        end: date,
        interval: str = "1d",
    ) -> pd.DataFrame:
        import yfinance as yf

        ticker = self.ticker_for(symbol)
        df = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            interval=interval,
            auto_adjust=True,
            actions=False,
            progress=False,
            threads=False,
            timeout=12,
        )
        return self._normalize(df)

    def history_recent(self, symbol: str, interval: str = "1d") -> pd.DataFrame:
        """Independent free freshness path used when batch download lags.

        Ticker.history follows a different yfinance request path and uses a
        short period window. It remains the same free Yahoo source; no paid
        provider is introduced.
        """
        import yfinance as yf

        ticker = yf.Ticker(self.ticker_for(symbol))
        df = ticker.history(
            period="1mo",
            interval=interval,
            auto_adjust=True,
            actions=False,
            repair=True,
            timeout=12,
        )
        return self._normalize(df)
