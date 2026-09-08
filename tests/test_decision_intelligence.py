from collections import Counter

from saudi_trading_bot.decision_intelligence import (
    _diagnosis,
    _loss_analysis,
    _max_drawdown_r,
    _score_strategy,
)


def test_max_drawdown_r_uses_cumulative_r_curve():
    assert _max_drawdown_r([1.0, -0.5, -1.0, 2.0]) == 1.5


def test_strategy_is_not_ranked_before_five_closed_trades():
    payload = {
        "positions": {},
        "pending": {},
        "closed": [
            {"pnl_sar": 100.0, "initial_risk_sar": 100.0},
            {"pnl_sar": -50.0, "initial_risk_sar": 100.0},
        ],
    }
    score = _score_strategy("test", payload, 30)
    assert score.closed == 2
    assert score.rank_score is None
    assert score.status == "LEARNING 2/30"


def test_loss_analyzer_detects_stop_dominated_failures():
    payload = {
        "closed": [
            {
                "pnl_sar": -100.0,
                "initial_risk_sar": 100.0,
                "reason": "stop",
                "strategy": "x",
            },
            {
                "pnl_sar": -100.0,
                "initial_risk_sar": 100.0,
                "reason": "stop",
                "strategy": "x",
            },
            {
                "pnl_sar": -100.0,
                "initial_risk_sar": 100.0,
                "reason": "stop",
                "strategy": "x",
            },
            {
                "pnl_sar": 200.0,
                "initial_risk_sar": 100.0,
                "reason": "target",
                "strategy": "x",
            },
        ]
    }
    result = _loss_analysis(payload)
    assert result["closed_losses"] == 3
    assert result["diagnosis"].startswith("STOP_DOMINATED")


def test_diagnosis_handles_no_losses():
    assert _diagnosis(Counter()) == "NO_CLOSED_LOSSES_YET"
