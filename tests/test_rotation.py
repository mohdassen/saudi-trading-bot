import pandas as pd

from saudi_trading_bot.backtest.rotation import (
    _formation_rows,
    _rotation_fold,
    _rotation_targets,
)


def _row(close: float, factor: float) -> dict:
    return {
        "open": close,
        "high": close * 1.02,
        "low": close * 0.99,
        "close": close,
        "ema50": close * 0.95,
        "ema200": close * 0.90,
        "roc20": 2,
        "roc63": factor,
        "roc126": factor,
        "roc189": factor,
        "rsi14": 55,
        "atr14": 2,
        "atr_pct": 2,
        "avg_value20": 5_000_000,
    }


def test_formation_rows_never_use_execution_session():
    frame = pd.DataFrame(
        [_row(100, 5), _row(999, 99)],
        index=pd.to_datetime(["2025-12-31", "2026-01-02"]),
    )

    rows = _formation_rows({"1120": frame}, pd.Timestamp("2026-01-02"))

    assert rows["1120"]["close"] == 100


def test_rotation_targets_apply_cross_sectional_direction():
    rows = {
        "A": pd.Series(_row(100, 5)),
        "B": pd.Series(_row(100, 20)),
        "C": pd.Series({**_row(100, -12), "rsi14": 45}),
    }
    settings = {"min_price_sar": 3, "min_avg_value_sar_20d": 1_000_000}

    assert _rotation_targets(rows, settings, "monthly_momentum_6_1", 1) == ["B"]
    assert _rotation_targets(rows, settings, "monthly_contrarian_3_1", 1) == ["C"]


def test_rotation_fold_executes_after_prior_formation_and_liquidates():
    frame = pd.DataFrame(
        [_row(100, 10), _row(105, 10), _row(110, 10)],
        index=pd.to_datetime(["2025-12-31", "2026-01-02", "2026-02-02"]),
    )
    settings = {
        "risk": {
            "paper_equity_sar": 100_000,
            "max_open_positions": 1,
            "max_position_pct": 100,
            "atr_stop_multiple": 1.8,
        },
        "paper": {"commission_bps": 0, "slippage_bps": 0},
        "market_regime": {
            "min_eligible_symbols": 1,
            "historical_min_eligible_symbols": 1,
            "risk_on_min_pct_above_ema50": 0,
            "risk_on_min_pct_above_ema200": 0,
            "risk_on_min_median_mom20_pct": 0,
            "recovery_min_pct_above_ema50": 0,
            "recovery_min_pct_above_ema200": 0,
            "recovery_min_median_mom20_pct": 0,
        },
        "signals": {"min_price_sar": 3, "min_avg_value_sar_20d": 1_000_000},
    }

    result = _rotation_fold(
        2026,
        {"1120": frame},
        settings,
        "monthly_momentum_6_1",
    )

    assert result.trades == 1
    assert result.win_rate == 100
    assert result.return_pct == 4.76
