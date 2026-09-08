from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.freshness import expected_completed_session

from .base import MarketDataProvider
from .cache import MarketDataCache

RIYADH = ZoneInfo("Asia/Riyadh")


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

        expected = expected_completed_session(datetime.now(RIYADH))
        primary_is_stale = self._newest(primary_df) is None or self._newest(primary_df) < expected

        # Only stale/empty symbols use the independent short-window Yahoo path.
        # Normal scans therefore keep roughly the same request volume as before.
        recent_method = getattr(self.primary, "history_recent", None)
        if primary_is_stale and callable(recent_method):
            try:
                recent_df = recent_method(symbol, interval)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"recent {type(exc).__name__}: {exc}")

        candidates = [df for df in (primary_df, recent_df) if not df.empty]
        if candidates:
            best = max(candidates, key=lambda df: self._newest(df) or date.min)
            if not primary_df.empty and not recent_df.empty:
                best = pd.concat([primary_df, recent_df]).sort_index()
                best = best[~best.index.duplicated(keep="last")]
                best = best.loc[
                    (pd.DatetimeIndex(best.index).date >= start)
                    & (pd.DatetimeIndex(best.index).date < end)
                ]
            self.cache.save(symbol, best)
            recent_won = (
                not recent_df.empty
                and (self._newest(recent_df) or date.min) >= (self._newest(primary_df) or date.min)
            )
            self.last_source = "free_primary_recent" if recent_won else "free_primary"
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
