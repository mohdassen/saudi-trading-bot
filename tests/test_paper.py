from datetime import date, datetime
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
        datetime.now(),
    )


def test_paper_entry_and_target_exit(tmp_path: Path):
    p = PaperPortfolio(tmp_path / "paper.json", 100000, 0.75, 12.5, 5, 2)
    pos = p.consider(signal(), today=date(2026, 9, 1))
    assert pos is not None and pos.qty > 0
    assert pos.entry > 100  # buy-side slippage is modeled
    trade = p.mark_daily_bar(
        "1120",
        low=99,
        high=112,
        close=110,
        today=date(2026, 9, 2),
    )
    assert trade is not None
    assert trade.reason == "target"
    assert trade.pnl_sar > 0
