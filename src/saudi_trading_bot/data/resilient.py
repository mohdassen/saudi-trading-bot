from __future__ import annotations

from datetime import date

import pandas as pd

from .base import MarketDataProvider
from .cache import MarketDataCache


class ResilientFreeProvider(MarketDataProvider):
    """Free primary feed + local cache fallback. Never upgrades to a paid provider."""

    def __init__(self, primary: MarketDataProvider, cache: MarketDataCache):
        self.primary = primary
        self.cache = cache
        self.last_source = "none"
        self.last_error = ""

    def history(self, symbol: str, start: date, end: date, interval: str = "1d") -> pd.DataFrame:
        try:
            df = self.primary.history(symbol, start, end, interval)
            if not df.empty:
                self.cache.save(symbol, df)
                self.last_source = "free_primary"
                self.last_error = ""
                return df
            self.last_error = "primary returned empty data"
        except Exception as exc:  # provider/network failures must not crash the whole scan
            self.last_error = f"{type(exc).__name__}: {exc}"

        cached = self.cache.load(symbol, start, end)
        if not cached.empty:
            self.last_source = "local_cache"
            return cached
        self.last_source = "none"
        return pd.DataFrame()
