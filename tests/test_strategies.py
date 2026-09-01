from datetime import UTC, datetime

from saudi_trading_bot.models import Signal, SignalState
from saudi_trading_bot.signals.strategies import gate_signal


def _ready_signal() -> Signal:
    return Signal(
        symbol="1120",
        state=SignalState.READY,
        total_score=80,
        trend_score=90,
        momentum_score=80,
        swing_score=70,
        disclosure_score=50,
        price=100,
        stop=95,
        target=111,
        atr=2,
        rationale=(),
        generated_at=datetime.now(UTC),
    )


def test_cash_validation_gate_never_returns_ready():
    gated = gate_signal(_ready_signal(), None, {}, "CASH", 2.2)

    assert gated.state == SignalState.WATCH
    assert gated.strategy == "CASH"
    assert gated.strategy_score == 0
    assert "لا دخول Paper" in gated.rationale[-1]
