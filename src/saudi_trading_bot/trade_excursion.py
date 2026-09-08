from __future__ import annotations


def update_excursions(trade: dict, high: float, low: float) -> dict:
    entry = float(trade.get("entry_price") or trade.get("entry") or 0.0)
    risk = abs(entry - float(trade.get("stop_price") or trade.get("stop") or entry))
    if entry <= 0 or risk <= 0:
        return trade

    mfe_r = (float(high) - entry) / risk
    mae_r = (float(low) - entry) / risk
    trade["mfe_r"] = max(float(trade.get("mfe_r", 0.0)), mfe_r)
    trade["mae_r"] = min(float(trade.get("mae_r", 0.0)), mae_r)
    return trade
