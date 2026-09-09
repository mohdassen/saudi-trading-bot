from datetime import date
from pathlib import Path

import pandas as pd

from saudi_trading_bot.data.base import MarketDataProvider
from saudi_trading_bot.data.cache import MarketDataCache
from saudi_trading_bot.data.resilient import ResilientFreeProvider


class Broken(MarketDataProvider):
    def history(self, symbol, start, end, interval="1d"):
        raise RuntimeError("offline")


def test_cache_fallback(tmp_path: Path):
    cache = MarketDataCache(tmp_path)
    idx = pd.to_datetime(["2026-08-30", "2026-08-31"])
    df = pd.DataFrame({"open": [1, 1], "high": [2, 2], "low": [1, 1], "close": [2, 2], "volume": [10, 10]}, index=idx)
    cache.save("1120", df)
    p = ResilientFreeProvider(Broken(), cache)
    got = p.history("1120", date(2026, 8, 1), date(2026, 9, 2))
    assert len(got) == 2
    assert p.last_source == "local_cache"


def test_fresh_cache_short_circuits_external_provider(tmp_path: Path, monkeypatch):
    cache = MarketDataCache(tmp_path)
    idx = pd.to_datetime(["2026-09-08", "2026-09-09"])
    df = pd.DataFrame(
        {
            "open": [10, 11],
            "high": [12, 13],
            "low": [9, 10],
            "close": [11, 12],
            "volume": [100, 120],
        },
        index=idx,
    )
    cache.save("1120", df)
    monkeypatch.setattr(
        "saudi_trading_bot.data.resilient.expected_completed_session",
        lambda now: date(2026, 9, 9),
    )

    p = ResilientFreeProvider(Broken(), cache)
    got = p.history("1120", date(2026, 9, 1), date(2026, 9, 10))

    assert len(got) == 2
    assert p.last_source == "local_cache_fresh"
    assert p.last_error == ""
