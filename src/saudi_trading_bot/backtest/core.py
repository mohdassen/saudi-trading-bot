from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from saudi_trading_bot.signals.indicators import enrich


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    profit_factor: float


def run_symbol_backtest(
    symbol: str,
    df: pd.DataFrame,
    commission_bps: float = 15.5,
    slippage_bps: float = 10.0,
    max_hold_days: int = 25,
) -> BacktestResult:
    x = enrich(df).dropna().copy()
    if len(x) < 80:
        return BacktestResult(symbol, 0, 0.0, 0.0, 0.0, 0.0)

    returns: list[float] = []
    i = 0
    while i < len(x) - 2:
        r = x.iloc[i]
        setup = (
            r["close"] > r["ema50"] > r["ema200"]
            and r["ema20"] > r["ema50"]
            and 52 <= r["rsi14"] <= 72
            and r["roc20"] > 0
            and r["close"] >= r["high20"] * 0.975
        )
        if not setup:
            i += 1
            continue

        entry = float(x.iloc[i + 1]["open"])
        atr = float(r["atr14"])
        stop = entry - 1.8 * atr
        target = entry + 2.2 * (entry - stop)
        exit_price = float(x.iloc[min(i + 1 + max_hold_days, len(x) - 1)]["close"])
        exit_i = min(i + 1 + max_hold_days, len(x) - 1)
        for j in range(i + 1, exit_i + 1):
            day = x.iloc[j]
            if float(day["low"]) <= stop:
                exit_price = stop
                exit_i = j
                break
            if float(day["high"]) >= target:
                exit_price = target
                exit_i = j
                break

        friction = 2 * (commission_bps + slippage_bps) / 10000.0
        ret = (exit_price / entry - 1.0) - friction
        returns.append(ret)
        i = exit_i + 1

    if not returns:
        return BacktestResult(symbol, 0, 0.0, 0.0, 0.0, 0.0)
    s = pd.Series(returns)
    equity = (1 + s).cumprod()
    dd = equity / equity.cummax() - 1
    wins = s[s > 0].sum()
    losses = -s[s < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    return BacktestResult(
        symbol=symbol,
        trades=len(s),
        win_rate=round(float((s > 0).mean() * 100), 2),
        total_return_pct=round(float((equity.iloc[-1] - 1) * 100), 2),
        max_drawdown_pct=round(float(dd.min() * 100), 2),
        profit_factor=round(pf, 2),
    )
