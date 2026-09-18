from saudi_trading_bot.paper.portfolio import PaperPosition
from saudi_trading_bot.profit_protection_shadow import evaluate_profit_protection


def _position(mfe_r: float) -> PaperPosition:
    return PaperPosition(
        symbol="2290", qty=10, entry=100.0, stop=95.0, target=110.0,
        score=90.0, opened_on="2026-09-01", strategy="shadow",
        entry_regime="RISK_OFF", mfe_r=mfe_r, mae_r=-0.2,
    )


def test_profit_protection_is_counterfactual_and_arms_at_one_r() -> None:
    position = _position(1.31)
    result = evaluate_profit_protection(position)
    assert result["armed"] is True
    assert result["shadow_protected_stop"] == 101.25
    assert position.stop == 95.0


def test_profit_protection_does_not_arm_early() -> None:
    position = _position(0.80)
    result = evaluate_profit_protection(position)
    assert result["armed"] is False
    assert result["shadow_protected_stop"] == 95.0
