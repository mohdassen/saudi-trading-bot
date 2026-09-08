from __future__ import annotations


def attach_entry_regime(trade: dict, regime: str | None) -> dict:
    if regime and not trade.get("entry_regime"):
        trade["entry_regime"] = str(regime)
    return trade
