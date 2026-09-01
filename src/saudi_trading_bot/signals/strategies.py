from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from saudi_trading_bot.models import Signal, SignalState

from .indicators import enrich

STRATEGIES = (
    "breakout",
    "trend_pullback",
    "momentum_6m",
    "low_vol_trend",
    "contrarian_3m",
)


@dataclass(frozen=True)
class StrategyCandidate:
    score: float
    risk_distance: float


def enrich_strategy(history: pd.DataFrame) -> pd.DataFrame:
    frame = enrich(history)
    frame["roc63"] = frame["close"].pct_change(63) * 100
    frame["roc126"] = frame["close"].pct_change(126) * 100
    frame["atr_pct"] = frame["atr14"] / frame["close"] * 100
    frame["ema50_slope20"] = frame["ema50"].pct_change(20) * 100
    return frame


def latest_strategy_rows(
    histories: dict[str, pd.DataFrame],
) -> dict[str, pd.Series]:
    rows: dict[str, pd.Series] = {}
    for symbol, history in histories.items():
        frame = enrich_strategy(history).dropna()
        if not frame.empty:
            rows[symbol] = frame.iloc[-1].copy()
    if not rows:
        return rows
    roc63 = pd.Series({symbol: float(row["roc63"]) for symbol, row in rows.items()})
    percentiles = roc63.rank(method="average", pct=True)
    for symbol, row in rows.items():
        row["xs_roc63_percentile"] = float(percentiles[symbol])
    return rows


def evaluate_candidate(
    row: pd.Series,
    cfg: dict,
    strategy: str,
) -> StrategyCandidate | None:
    price = float(row["close"])
    rsi, momentum = float(row["rsi14"]), float(row["roc20"])
    volume = float(row["vol_ratio"])
    rules = cfg["entry_rules"]
    liquid = (
        price >= float(cfg["min_price_sar"])
        and float(row["avg_value20"]) >= float(cfg["min_avg_value_sar_20d"])
    )
    if not liquid:
        return None

    if strategy == "breakout":
        valid = (
            price > row["ema20"] > row["ema50"] > row["ema200"]
            and float(rules["rsi_min"]) <= rsi <= float(rules["rsi_max"])
            and float(rules["momentum_20d_min_pct"])
            <= momentum
            <= float(rules["momentum_20d_max_pct"])
            and volume >= float(rules["volume_ratio_min"])
            and price >= float(row["high20"]) * float(rules["near_high20_ratio"])
        )
        score = momentum * 2 + volume * 15 + (68 - abs(60 - rsi))
    elif strategy == "trend_pullback":
        distance20 = price / float(row["ema20"]) - 1
        valid = (
            price > row["ema50"] > row["ema200"]
            and -0.03 <= distance20 <= 0.02
            and 45 <= rsi <= 59
            and float(row["roc63"]) >= 3
            and volume >= 0.70
        )
        score = float(row["roc63"]) + (60 - rsi) * 2 - abs(distance20) * 100
    elif strategy == "momentum_6m":
        valid = (
            price > row["ema50"] > row["ema200"]
            and 8 <= float(row["roc126"]) <= 60
            and float(row["roc63"]) >= 3
            and 50 <= rsi <= 70
            and volume >= 0.80
        )
        score = (
            float(row["roc126"])
            + 0.5 * float(row["roc63"])
            - 2 * float(row["atr_pct"])
        )
    elif strategy == "low_vol_trend":
        valid = (
            price > row["ema50"] > row["ema200"]
            and float(row["ema50_slope20"]) > 0
            and 3 <= float(row["roc63"]) <= 25
            and float(row["atr_pct"]) <= 4.5
            and 48 <= rsi <= 64
        )
        score = (
            2 * float(row["roc63"])
            - 4 * float(row["atr_pct"])
            + float(row["ema50_slope20"])
        )
    elif strategy == "contrarian_3m":
        valid = (
            price > row["ema200"]
            and float(row["xs_roc63_percentile"]) <= 0.20
            and -25 <= float(row["roc63"]) <= -3
            and 30 <= rsi <= 50
            and float(row["atr_pct"]) <= 6
            and volume >= 0.80
        )
        score = (
            (0.20 - float(row["xs_roc63_percentile"])) * 100
            - float(row["roc63"])
            - 2 * float(row["atr_pct"])
        )
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
    if not valid:
        return None
    return StrategyCandidate(score, max(0.01, float(row["atr14"]) * 1.8))


def gate_signal(
    signal: Signal,
    row: pd.Series | None,
    settings: dict,
    active_strategy: str,
    reward_risk: float,
) -> Signal:
    """Make validation status the only path to a Paper-ready signal."""
    if active_strategy == "CASH":
        state = SignalState.WATCH if signal.state == SignalState.READY else signal.state
        return replace(
            signal,
            state=state,
            strategy="CASH",
            strategy_score=0.0,
            rationale=signal.rationale
            + ("Strategy validation gate: CASH — لا دخول Paper",),
        )
    if active_strategy not in STRATEGIES:
        raise ValueError(f"Unknown active strategy: {active_strategy}")

    setup = evaluate_candidate(row, settings, active_strategy) if row is not None else None
    if setup is None:
        state = SignalState.WATCH if signal.state == SignalState.READY else signal.state
        return replace(
            signal,
            state=state,
            strategy=active_strategy,
            strategy_score=0.0,
            rationale=signal.rationale
            + (f"شروط الاستراتيجية النشطة {active_strategy} غير مكتملة",),
        )

    stop = max(0.01, signal.price - setup.risk_distance)
    return replace(
        signal,
        state=SignalState.READY,
        stop=round(stop, 2),
        target=round(signal.price + reward_risk * (signal.price - stop), 2),
        strategy=active_strategy,
        strategy_score=round(setup.score, 2),
        rationale=signal.rationale + (f"استراتيجية معتمدة: {active_strategy}",),
    )
