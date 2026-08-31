from datetime import UTC, date, datetime
from pathlib import Path

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


def test_paper_entry_and_target_exit(tmp_path: Path):
    portfolio = PaperPortfolio(tmp_path / "paper.json", 100000, 0.75, 12.5, 5, 2)
    position = portfolio.consider(signal(), today=date(2026, 9, 1))
    assert position is not None and position.qty > 0
    assert position.entry > 100  # buy-side slippage is modeled
    trade = portfolio.mark_daily_bar(
        "1120",
        low=99,
        high=112,
        close=110,
        today=date(2026, 9, 2),
    )
    assert trade is not None
    assert trade.reason == "target"
    assert trade.pnl_sar > 0
