from pathlib import Path

from saudi_trading_bot.edge_lab import _performance
from saudi_trading_bot.paper.portfolio import PaperPortfolio, PaperTrade


def test_forward_progress_uses_r_and_qualification(tmp_path: Path):
    portfolio = PaperPortfolio(tmp_path / "edge.json", 100000, 0.25, 5.0)
    portfolio.closed = [
        PaperTrade(
            symbol=f"{1000 + index}",
            qty=10,
            entry=100,
            exit=102 if index % 3 else 99,
            opened_on="2026-09-01",
            closed_on="2026-09-02",
            reason="test",
            pnl_sar=200 if index % 3 else -100,
            return_pct=2.0 if index % 3 else -1.0,
            strategy="edge",
            initial_risk_sar=100,
        )
        for index in range(30)
    ]
    result = _performance(
        "edge",
        portfolio,
        30,
        {
            "min_expectancy_r": 0.10,
            "min_profit_factor": 1.30,
            "max_drawdown_r": 6.0,
        },
    )
    assert result.closed_trades == 30
    assert result.expectancy_r is not None
    assert result.expectancy_r > 0.10
    assert result.profit_factor > 1.30
    assert result.qualified is True
    assert result.status == "FORWARD_PASS"
