import numpy as np
import pandas as pd

from saudi_trading_bot.backtest.core import run_symbol_backtest


def test_backtest_smoke_on_synthetic_daily_series():
    n = 420
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    base = 50 + np.linspace(0, 35, n) + 2.2 * np.sin(np.arange(n) / 5.0)
    df = pd.DataFrame(
        {
            "open": base * 0.998,
            "high": base * 1.015,
            "low": base * 0.985,
            "close": base,
            "volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )

    result = run_symbol_backtest("1120", df)

    assert result.symbol == "1120"
    assert result.trades >= 0
    assert 0 <= result.win_rate <= 100
    assert result.max_drawdown_pct <= 0
