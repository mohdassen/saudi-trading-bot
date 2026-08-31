import numpy as np
import pandas as pd

from saudi_trading_bot.signals.indicators import enrich


def test_enrich_adds_core_indicators():
    n = 260
    close = pd.Series(np.linspace(10, 20, n))
    df = pd.DataFrame({
        "open": close * 0.995,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.full(n, 1_000_000),
    })
    x = enrich(df)
    for c in ["ema20", "ema50", "ema200", "rsi14", "atr14", "roc20", "high20", "vol_ratio"]:
        assert c in x.columns
    assert x.iloc[-1]["ema20"] > x.iloc[-1]["ema50"] > x.iloc[-1]["ema200"]
