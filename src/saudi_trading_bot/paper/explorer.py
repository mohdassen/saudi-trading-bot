from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from saudi_trading_bot.models import Signal, SignalState
from saudi_trading_bot.paper.portfolio import PaperPortfolio


@dataclass(frozen=True)
class ExplorerHealth:
    allowed: bool
    trades: int
    profit_factor: float
    pnl_sar: float
    note: str


def health(portfolio: PaperPortfolio, cfg: dict, initial_equity: float) -> ExplorerHealth:
    window = int(cfg["performance_window"])
    trades = portfolio.closed[-window:]
    pnl = sum(item.pnl_sar for item in trades)
    gains = sum(max(0.0, item.pnl_sar) for item in trades)
    losses = abs(sum(min(0.0, item.pnl_sar) for item in trades))
    profit_factor = gains / losses if losses else (999.0 if gains else 0.0)
    enough = len(trades) >= int(cfg["min_trades_before_brake"])
    loss_limit = initial_equity * float(cfg["max_loss_pct"]) / 100.0
    allowed = not enough or (
        profit_factor >= float(cfg["min_profit_factor"]) and pnl > -loss_limit
    )
    note = (
        f"trades={len(trades)} pf={profit_factor:.2f} pnl={pnl:.2f}SAR "
        f"status={'RUN' if allowed else 'BRAKE'}"
    )
    return ExplorerHealth(allowed, len(trades), profit_factor, pnl, note)


def candidate(signal: Signal, row: pd.Series | None, cfg: dict) -> Signal | None:
    """Select a liquid, long-trend daily setup for isolated Paper exploration."""
    if row is None or signal.total_score < float(cfg["min_signal_score"]):
        return None
    price = float(row["close"])
    valid = (
        signal.state in {SignalState.READY, SignalState.WATCH}
        and price > float(row["ema200"])
        and float(row["roc20"]) > 0
        and 48 <= float(row["rsi14"]) <= 70
    )
    if not valid:
        return None
    return replace(
        signal,
        state=SignalState.READY,
        strategy="explorer_swing",
        strategy_score=round(signal.total_score, 2),
        rationale=signal.rationale + ("Paper Explorer isolated learning trade",),
    )
