from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    out = out.mask((loss == 0) & (gain > 0), 100.0)
    out = out.mask((loss == 0) & (gain == 0), 50.0)
    return out


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["ema20"] = x["close"].ewm(span=20, adjust=False).mean()
    x["ema50"] = x["close"].ewm(span=50, adjust=False).mean()
    x["ema200"] = x["close"].ewm(span=200, adjust=False).mean()
    x["rsi14"] = rsi(x["close"], 14)
    x["atr14"] = atr(x, 14)
    x["roc20"] = x["close"].pct_change(20) * 100
    x["high20"] = x["high"].rolling(20).max()
    x["high55"] = x["high"].rolling(55).max()
    x["avg_vol20"] = x["volume"].rolling(20).mean()
    x["vol_ratio"] = x["volume"] / x["avg_vol20"].replace(0, np.nan)
    x["avg_value20"] = (x["close"] * x["volume"]).rolling(20).mean()
    return x
