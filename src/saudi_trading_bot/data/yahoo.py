from __future__ import annotations

from datetime import date

import pandas as pd

from .base import MarketDataProvider


class YahooSaudiProvider(MarketDataProvider):
    """Research/development adapter. Not an official Saudi Exchange market-data feed."""

    def __init__(self, suffix: str = ".SR") -> None:
        self.suffix = suffix

    def ticker_for(self, symbol: str) -> str:
        """Return the Yahoo ticker without duplicating an existing market suffix."""
        symbol = str(symbol).strip()
        if symbol.startswith("^") or symbol.endswith(self.suffix):
            return symbol
        return f"{symbol}{self.suffix}"

    def history(self, symbol: str, start: date, end: date, interval: str = "1d") -> pd.DataFrame:
        import yfinance as yf

        ticker = self.ticker_for(symbol)
        df = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            interval=interval,
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=False,
            timeout=12,
        )
        if df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.rename(columns=str.lower)
        keep = [
            c
            for c in ["open", "high", "low", "close", "adj close", "volume"]
            if c in df.columns
        ]
        return df[keep].dropna(subset=["close"])
