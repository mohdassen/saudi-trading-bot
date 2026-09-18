from __future__ import annotations

from dataclasses import asdict
from typing import Any

from saudi_trading_bot.paper.portfolio import PaperPosition


def evaluate_profit_protection(
    position: PaperPosition,
    *,
    arm_at_r: float = 1.0,
    protect_r: float = 0.25,
) -> dict[str, Any]:
    """Shadow-only counterfactual; never mutates the live PaperPosition."""
    armed = float(position.mfe_r) >= float(arm_at_r)
    protected_stop = (
        position.entry + float(protect_r) * max(0.01, position.entry - position.stop)
        if armed
        else position.stop
    )
    return {
        "symbol": position.symbol,
        "strategy": position.strategy,
        "entry_regime": position.entry_regime,
        "mfe_r": round(float(position.mfe_r), 3),
        "mae_r": round(float(position.mae_r), 3),
        "armed": armed,
        "arm_at_r": float(arm_at_r),
        "protect_r": float(protect_r),
        "baseline_stop": float(position.stop),
        "shadow_protected_stop": round(float(protected_stop), 4),
        "position_unchanged": asdict(position),
    }
