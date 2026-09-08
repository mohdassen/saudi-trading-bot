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

    @staticmethod
    def _newest(df: pd.DataFrame) -> date | None:
        if df.empty:
            return None
        return pd.Timestamp(df.index[-1]).date()

    def history(
        self,
        symbol: str,
        start: date,
        end: date,
        interval: str = "1d",
    ) -> pd.DataFrame:
        primary_df = pd.DataFrame()
        recent_df = pd.DataFrame()
        errors: list[str] = []

        try:
            primary_df = self.primary.history(symbol, start, end, interval)
        except Exception as exc:  # noqa: BLE001 - external provider isolation boundary
            errors.append(f"primary {type(exc).__name__}: {exc}")

        # A short-window Ticker.history request often recovers the newest Saudi
        # session when Yahoo's batch download endpoint is lagging. Use it only
        # when the provider explicitly offers this free fallback.
        recent_method = getattr(self.primary, "history_recent", None)
        if callable(recent_method):
            try:
                recent_df = recent_method(symbol, interval)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"recent {type(exc).__name__}: {exc}")

        candidates = [df for df in (primary_df, recent_df) if not df.empty]
        if candidates:
            best = max(candidates, key=lambda df: self._newest(df) or date.min)
            # Merge long history with the recent recovery window so indicators
            # retain sufficient lookback while the newest bars win on overlap.
            if not primary_df.empty and not recent_df.empty:
                best = pd.concat([primary_df, recent_df]).sort_index()
                best = best[~best.index.duplicated(keep="last")]
                best = best.loc[
                    (pd.DatetimeIndex(best.index).date >= start)
                    & (pd.DatetimeIndex(best.index).date < end)
                ]
            self.cache.save(symbol, best)
            self.last_source = (
                "free_primary_recent"
                if self._newest(recent_df) and self._newest(recent_df) >= (self._newest(primary_df) or date.min)
                else "free_primary"
            )
            self.last_error = "; ".join(errors)
            return best

        if not errors:
            errors.append("primary returned empty data")
        self.last_error = "; ".join(errors)

        cached = self.cache.load(symbol, start, end)
        if not cached.empty:
            self.last_source = "local_cache"
            return cached
        self.last_source = "none"
        return pd.DataFrame()
