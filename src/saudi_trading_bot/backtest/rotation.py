from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from saudi_trading_bot.signals.strategies import enrich_strategy

from .validation import (
    FoldResult,
    ValidationDecision,
    _breadth,
    _select_strategy,
)

ROTATION_STRATEGIES = (
    "monthly_momentum_6_1",
    "quarterly_momentum_9_3",
    "monthly_contrarian_3_1",
    "quarterly_contrarian_3_3",
)


@dataclass(frozen=True)
class RotationSpec:
    feature: str
    direction: str
    rebalance_months: int


@dataclass
class _Holding:
    qty: int
    entry: float
    cost_cash: float
    stop: float
    last_price: float


SPECS = {
    "monthly_momentum_6_1": RotationSpec("roc126", "high", 1),
    "quarterly_momentum_9_3": RotationSpec("roc189", "high", 3),
    "monthly_contrarian_3_1": RotationSpec("roc63", "low", 1),
    "quarterly_contrarian_3_3": RotationSpec("roc63", "low", 3),
}


def _formation_rows(
    frames: dict[str, pd.DataFrame],
    timestamp: pd.Timestamp,
) -> dict[str, pd.Series]:
    rows: dict[str, pd.Series] = {}
    for symbol, frame in frames.items():
        offset = int(frame.index.searchsorted(timestamp, side="left")) - 1
        if offset >= 0:
            rows[symbol] = frame.iloc[offset].copy()
    return rows


def _rotation_targets(
    rows: dict[str, pd.Series],
    settings: dict,
    strategy: str,
    limit: int,
) -> list[str]:
    spec = SPECS[strategy]
    candidates = []
    for symbol, row in rows.items():
        price = float(row["close"])
        factor = float(row[spec.feature])
        rsi = float(row["rsi14"])
        liquid = (
            price >= float(settings["min_price_sar"])
            and float(row["avg_value20"])
            >= float(settings["min_avg_value_sar_20d"])
        )
        long_trend = price > float(row["ema200"])
        volatility_ok = float(row["atr_pct"]) <= 7
        if not (liquid and long_trend and volatility_ok):
            continue
        if spec.direction == "high":
            eligible = factor > 0 and rsi <= 75
            score = factor
        else:
            eligible = factor < 0 and 30 <= rsi <= 55
            score = -factor
        if eligible:
            candidates.append((score, symbol))
    return [
        symbol
        for _, symbol in sorted(candidates, reverse=True)[: max(0, int(limit))]
    ]


def _rotation_fold(
    year: int,
    frames: dict[str, pd.DataFrame],
    settings: dict,
    strategy: str,
) -> FoldResult:
    risk, paper = settings["risk"], settings["paper"]
    spec = SPECS[strategy]
    dates = sorted(
        {
            pd.Timestamp(index)
            for frame in frames.values()
            for index in frame.index
            if pd.Timestamp(index).year == year
        }
    )
    starting_cash = float(risk["paper_equity_sar"])
    cash, peak, max_dd = starting_cash, starting_cash, 0.0
    commission = float(paper["commission_bps"]) / 10000
    slippage = float(paper["slippage_bps"]) / 10000
    max_positions = int(risk["max_open_positions"])
    max_position_pct = float(risk["max_position_pct"]) / 100
    atr_multiple = float(risk["atr_stop_multiple"])
    regime_settings = dict(settings["market_regime"])
    regime_settings["min_eligible_symbols"] = regime_settings.get(
        "historical_min_eligible_symbols",
        regime_settings["min_eligible_symbols"],
    )
    positions: dict[str, _Holding] = {}
    pnls: list[float] = []
    last_month: tuple[int, int] | None = None

    def close(symbol: str, raw_price: float) -> None:
        nonlocal cash
        holding = positions.pop(symbol)
        exit_price = raw_price * (1 - slippage)
        proceeds = holding.qty * exit_price * (1 - commission)
        cash += proceeds
        pnls.append(proceeds - holding.cost_cash)

    for timestamp in dates:
        rows = {
            symbol: frame.loc[timestamp]
            for symbol, frame in frames.items()
            if timestamp in frame.index
        }
        month = (timestamp.year, timestamp.month)
        first_session = month != last_month
        last_month = month
        scheduled = first_session and (timestamp.month - 1) % spec.rebalance_months == 0

        if scheduled:
            formation = _formation_rows(frames, timestamp)
            regime = _breadth(list(formation.values()), regime_settings)
            targets = (
                _rotation_targets(
                    formation,
                    settings["signals"],
                    strategy,
                    max_positions,
                )
                if regime != "RISK_OFF"
                else []
            )
            for symbol in list(positions):
                if symbol not in targets and symbol in rows:
                    close(symbol, float(rows[symbol]["open"]))

            open_nav = cash + sum(
                holding.qty
                * float(rows[symbol]["open"] if symbol in rows else holding.last_price)
                for symbol, holding in positions.items()
            )
            allocation = open_nav * max_position_pct
            for symbol in targets:
                if symbol in positions or symbol not in rows:
                    continue
                row, prior = rows[symbol], formation[symbol]
                entry = float(row["open"]) * (1 + slippage)
                per_share_cash = entry * (1 + commission)
                qty = min(int(allocation / per_share_cash), int(cash / per_share_cash))
                if qty < 1:
                    continue
                cost = qty * per_share_cash
                cash -= cost
                positions[symbol] = _Holding(
                    qty=qty,
                    entry=entry,
                    cost_cash=cost,
                    stop=max(0.01, entry - atr_multiple * float(prior["atr14"])),
                    last_price=float(row["open"]),
                )

        for symbol in list(positions):
            row = rows.get(symbol)
            if row is None:
                continue
            holding = positions[symbol]
            if float(row["low"]) <= holding.stop:
                close(symbol, holding.stop)
            else:
                holding.last_price = float(row["close"])

        nav = cash + sum(
            holding.qty * holding.last_price for holding in positions.values()
        )
        peak = max(peak, nav)
        max_dd = min(max_dd, nav / peak - 1)

    for symbol in list(positions):
        close(symbol, positions[symbol].last_price)
    peak = max(peak, cash)
    max_dd = min(max_dd, cash / peak - 1)

    if not pnls:
        return FoldResult(year, 0, 0, 0, 0, 0, strategy)
    series = pd.Series(pnls)
    wins = float(series[series > 0].sum())
    losses = float(-series[series < 0].sum())
    factor = wins / losses if losses else float("inf")
    return FoldResult(
        year=year,
        trades=len(series),
        win_rate=round(float((series > 0).mean() * 100), 2),
        return_pct=round((cash / starting_cash - 1) * 100, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        profit_factor=round(factor, 2),
        strategy=strategy,
    )


def evaluate_rotation_lab(
    histories: dict[str, pd.DataFrame],
    settings: dict,
) -> tuple[list[FoldResult], dict[tuple[str, int], FoldResult]]:
    all_dates = [pd.Timestamp(i) for history in histories.values() for i in history.index]
    if not all_dates:
        return [], {}
    frames = {}
    for symbol, history in histories.items():
        frame = enrich_strategy(history)
        frame["roc189"] = frame["close"].pct_change(189) * 100
        frames[symbol] = frame.dropna().sort_index()
    latest_year = max(all_dates).year
    lab = settings["validation"].get("strategy_lab", {})
    training_years = int(lab.get("training_years", 2))
    oos_years = int(lab.get("oos_years", 8))
    first_training_year = latest_year - (training_years + oos_years - 1)
    matrix = {
        (strategy, year): _rotation_fold(year, frames, settings, strategy)
        for strategy in ROTATION_STRATEGIES
        for year in range(first_training_year, latest_year + 1)
    }
    folds = []
    for target_year in range(latest_year - oos_years + 1, latest_year + 1):
        strategy = _select_strategy(
            matrix,
            target_year,
            lab,
            ROTATION_STRATEGIES,
        )
        folds.append(
            matrix[(strategy, target_year)]
            if strategy
            else FoldResult(target_year, 0, 0, 0, 0, 0, "CASH")
        )
    return folds, matrix


def write_rotation_report(
    output_dir: Path,
    folds: list[FoldResult],
    matrix: dict[tuple[str, int], FoldResult],
    decision: ValidationDecision,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [asdict(matrix[key]) for key in sorted(matrix, key=lambda x: (x[1], x[0]))]
    payload = {
        "decision": asdict(decision),
        "folds": [asdict(fold) for fold in folds],
        "strategy_matrix": rows,
        "method": "long-only calendar rotation with next-session opens",
    }
    (output_dir / "rotation_validation_report.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Saudi Trading Bot — Monthly Rotation Lab V4",
        "",
        f"Decision: **{decision.status}**",
        "",
        "Formation uses only the prior completed session. Rebalances execute at the",
        "next calendar month/quarter open with commission, slippage, and stop-first",
        "daily risk handling. The model is long-only and may remain in cash.",
        "",
        "| OOS year | Selected | Trades | Win rate | Return | Max drawdown | PF |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        f"| {f.year} | {f.strategy} | {f.trades} | {f.win_rate:.1f}% | "
        f"{f.return_pct:.1f}% | {f.max_drawdown_pct:.1f}% | {f.profit_factor:.2f} |"
        for f in folds
    )
    if decision.reasons:
        lines.extend(["", "Failed gates:", *[f"- {x}" for x in decision.reasons]])
    (output_dir / "rotation_validation_report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
