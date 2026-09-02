from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from saudi_trading_bot.models import Signal, SignalState
from saudi_trading_bot.paper.explorer import candidate, health
from saudi_trading_bot.paper.portfolio import PaperPortfolio, PaperTrade

CFG = {
    "min_signal_score": 78,
    "performance_window": 10,
    "min_trades_before_brake": 10,
    "min_profit_factor": 0.8,
    "max_loss_pct": 2.0,
}


def signal() -> Signal:
    return Signal(
        "1120", SignalState.READY, 80, 80, 80, 80, 50, 100, 95, 111,
        2.0, ("test",), datetime.now(UTC)
    )


def test_explorer_candidate_is_isolated_strategy():
    row = pd.Series({"close": 105, "ema200": 100, "roc20": 4, "rsi14": 58})
    picked = candidate(signal(), row, CFG)
    assert picked is not None
    assert picked.strategy == "explorer_swing"
    assert picked.strategy_score == 80


def test_explorer_rejects_weak_or_below_long_trend():
    row = pd.Series({"close": 95, "ema200": 100, "roc20": 4, "rsi14": 58})
    assert candidate(signal(), row, CFG) is None
    assert candidate(replace(signal(), total_score=70), row, CFG) is None


def test_explorer_performance_brake(tmp_path: Path):
    portfolio = PaperPortfolio(tmp_path / "explorer.json", 100000, 0.25, 5, 3, 1)
    portfolio.closed = [
        PaperTrade("1120", 1, 100, 90, "2026-01-01", "2026-01-02", "stop", -250, -2.5, "explorer_swing")
        for _ in range(10)
    ]
    status = health(portfolio, CFG, 100000)
    assert not status.allowed
    assert status.pnl_sar == -2500
