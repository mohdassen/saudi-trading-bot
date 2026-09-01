from saudi_trading_bot.backtest.validation import (
    STRATEGIES,
    FoldResult,
    _select_strategy,
    decide,
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
