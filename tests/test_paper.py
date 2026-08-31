from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from saudi_trading_bot.models import Signal, SignalState
from saudi_trading_bot.paper.portfolio import PaperPortfolio


def signal(symbol="1120"):
    return Signal(
        symbol,
        SignalState.READY,
        80,
        80,
        80,
        80,
        50,
        100,
        95,
        111,
        2.0,
        ("test",),
        datetime.now(UTC),
    )


def history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [98.0, 101.0],
            "high": [101.0, 113.0],
            "low": [96.0, 100.0],
            "close": [100.0, 112.0],
        },
        index=pd.to_datetime(["2026-09-01", "2026-09-02"]),
    )


def test_pending_signal_does_not_fill_on_signal_bar(tmp_path: Path):
    portfolio = PaperPortfolio(tmp_path / "paper.json", 100000, 0.75, 12.5, 5, 2)
    queued = portfolio.queue(signal(), signal_bar_date=date(2026, 9, 1))
    assert queued is not None

    same_bar = history().iloc[:1]
    opened = portfolio.execute_pending({"1120": same_bar})
    assert opened == []
    assert "1120" in portfolio.pending
    assert "1120" not in portfolio.positions


def test_next_session_open_then_target_exit(tmp_path: Path):
    portfolio = PaperPortfolio(tmp_path / "paper.json", 100000, 0.75, 12.5, 5, 2)
    portfolio.queue(signal(), signal_bar_date=date(2026, 9, 1))

    opened = portfolio.execute_pending({"1120": history()})
    assert len(opened) == 1
    position = opened[0]
    assert position.opened_on == "2026-09-02"
    assert position.entry > 101.0  # next-open buy-side slippage is modeled
    assert position.stop > 95.0  # stop is rebased to the actual next open

    trades = portfolio.mark_histories({"1120": history()})
    assert len(trades) == 1
    assert trades[0].reason == "target"
    assert trades[0].pnl_sar > 0
    assert "1120" not in portfolio.positions
