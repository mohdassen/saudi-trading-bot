import json

import pandas as pd

from saudi_trading_bot.backtest.validation import (
    STRATEGIES,
    FoldResult,
    ValidationDecision,
    _candidate,
    _select_strategy,
    decide,
    write_report,
)

SETTINGS = {
    "min_oos_trades": 80,
    "min_win_rate_pct": 38,
    "min_profit_factor": 1.2,
    "max_drawdown_pct": 15,
    "min_profitable_folds": 3,
}


def test_validation_pass_requires_all_gates():
    folds = [
        FoldResult(year, 25, 45, 8, -6, 1.4)
        for year in range(2023, 2027)
    ]
    assert decide(folds, SETTINGS).status == "BACKTEST_PASS"


def test_validation_blocks_weak_or_sparse_result():
    folds = [
        FoldResult(year, 10, 30, -4, -20, 0.8)
        for year in range(2023, 2027)
    ]
    result = decide(folds, SETTINGS)
    assert result.status == "BLOCK"
    assert "minimum out-of-sample trades" in result.reasons
    assert "maximum drawdown" in result.reasons


def test_nested_selector_uses_training_years_only():
    matrix = {}
    for strategy in STRATEGIES:
        matrix[(strategy, 2023)] = FoldResult(2023, 10, 40, 2, -3, 1.1, strategy)
        matrix[(strategy, 2024)] = FoldResult(2024, 10, 40, 2, -3, 1.1, strategy)
    matrix[("trend_pullback", 2023)] = FoldResult(
        2023, 10, 45, 8, -3, 1.4, "trend_pullback"
    )
    matrix[("trend_pullback", 2024)] = FoldResult(
        2024, 10, 45, 8, -3, 1.4, "trend_pullback"
    )
    selected = _select_strategy(
        matrix,
        2025,
        {"min_train_trades": 12, "min_train_profit_factor": 1.05},
    )
    assert selected == "trend_pullback"


def test_validation_report_preserves_full_strategy_matrix(tmp_path):
    selected = [FoldResult(2026, 8, 50, 2, -3, 1.3, "momentum_6m")]
    matrix = {
        ("momentum_6m", 2026): selected[0],
        ("breakout", 2026): FoldResult(2026, 4, 25, -1, -4, 0.8, "breakout"),
    }

    write_report(
        tmp_path,
        selected,
        ValidationDecision("BLOCK", ("minimum out-of-sample trades",)),
        matrix,
    )

    payload = json.loads((tmp_path / "validation_report.json").read_text())
    assert len(payload["strategy_matrix"]) == 2
    assert {row["strategy"] for row in payload["strategy_matrix"]} == {
        "breakout",
        "momentum_6m",
    }


def test_three_month_contrarian_requires_bottom_cross_sectional_quintile():
    row = pd.Series(
        {
            "close": 100,
            "ema200": 95,
            "roc20": -4,
            "roc63": -10,
            "rsi14": 40,
            "atr14": 2,
            "atr_pct": 2,
            "vol_ratio": 1,
            "avg_value20": 5_000_000,
            "xs_roc63_percentile": 0.10,
        }
    )
    settings = {
        "min_price_sar": 3,
        "min_avg_value_sar_20d": 1_000_000,
        "entry_rules": {},
    }

    assert _candidate(row, settings, "contrarian_3m") is not None
    row["xs_roc63_percentile"] = 0.50
    assert _candidate(row, settings, "contrarian_3m") is None
