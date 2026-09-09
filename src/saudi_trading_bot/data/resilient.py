from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.freshness import expected_completed_session

from .base import MarketDataProvider
from .cache import MarketDataCache

RIYADH = ZoneInfo("Asia/Riyadh")


class ResilientFreeProvider(MarketDataProvider):
    """Free primary feed + optional free rescue feed + local cache fallback."""

    def __init__(
        self,
        primary: MarketDataProvider,
        cache: MarketDataCache,
        rescue: MarketDataProvider | None = None,
    ):
        self.primary = primary
        self.cache = cache
        if rescue is None and primary.__class__.__name__ == "YahooSaudiProvider":
            # Only the approved Saudi Yahoo adapter receives the Saudi-specific
            # live rescue. Generic/test providers remain completely isolated.
            from .argaam import ArgaamSaudiProvider

            rescue = ArgaamSaudiProvider()
        self.rescue = rescue
        self.last_source = "none"
        self.last_error = ""

    @staticmethod
    def _newest(df: pd.DataFrame) -> date | None:
        if df.empty:
            return None
        return pd.Timestamp(df.index[-1]).date()

    @staticmethod
    def _merge(*frames: pd.DataFrame) -> pd.DataFrame:
        usable = [frame for frame in frames if not frame.empty]
        if not usable:
            return pd.DataFrame()
        result = pd.concat(usable).sort_index()
        result = result[~result.index.duplicated(keep="last")]
        return result

    def history(
        self,
        symbol: str,
        start: date,
        end: date,
        interval: str = "1d",
    ) -> pd.DataFrame:
        expected = expected_completed_session(datetime.now(RIYADH))

        # A cache row from the expected completed Saudi session is already
        # trustworthy enough for repeated consumers inside the same workflow
        # (Production scan -> Edge Lab). Reusing it avoids hundreds of duplicate
        # Yahoo/Argaam requests while never accepting a stale cache as fresh.
        cached = self.cache.load(symbol, start, end)
        if not cached.empty and (self._newest(cached) or date.min) >= expected:
            self.last_source = "local_cache_fresh"
            self.last_error = ""
            return cached

        primary_df = pd.DataFrame()
        recent_df = pd.DataFrame()
        rescue_df = pd.DataFrame()
        errors: list[str] = []

        try:
            primary_df = self.primary.history(symbol, start, end, interval)
        except Exception as exc:  # noqa: BLE001 - external provider isolation boundary
            errors.append(f"primary {type(exc).__name__}: {exc}")

        newest_primary = self._newest(primary_df)
        primary_is_stale = newest_primary is None or newest_primary < expected

        recent_method = getattr(self.primary, "history_recent", None)
        if primary_is_stale and callable(recent_method):
            try:
                recent_df = recent_method(symbol, interval)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"recent {type(exc).__name__}: {exc}")

        newest_after_recent = max(
            self._newest(primary_df) or date.min,
            self._newest(recent_df) or date.min,
        )
        if newest_after_recent < expected and self.rescue is not None:
            try:
                rescue_df = self.rescue.history(symbol, start, end, interval)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"rescue {type(exc).__name__}: {exc}")

        best = self._merge(primary_df, recent_df, rescue_df)
        if not best.empty:
            dates = pd.DatetimeIndex(best.index).date
            best = best.loc[(dates >= start) & (dates < end)]
            if not best.empty:
                self.cache.save(symbol, best)
                newest_rescue = self._newest(rescue_df) or date.min
                newest_recent = self._newest(recent_df) or date.min
                newest_primary = self._newest(primary_df) or date.min
                if newest_rescue >= max(newest_recent, newest_primary) and not rescue_df.empty:
                    self.last_source = "free_argaam_rescue"
                elif newest_recent >= newest_primary and not recent_df.empty:
                    self.last_source = "free_primary_recent"
                else:
                    self.last_source = "free_primary"
                self.last_error = "; ".join(errors)
                return best

        if not errors:
            errors.append("all free providers returned empty data")
        self.last_error = "; ".join(errors)
        if not cached.empty:
            self.last_source = "local_cache"
            return cached
        self.last_source = "none"
        return pd.DataFrame()
