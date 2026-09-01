from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from saudi_trading_bot.signals.strategies import (
    STRATEGIES,
    enrich_strategy,
    evaluate_candidate,
)


@dataclass(frozen=True)
class FoldResult:
    year: int
    trades: int
    win_rate: float
    return_pct: float
    max_drawdown_pct: float
    profit_factor: float
    strategy: str = "unknown"


@dataclass(frozen=True)
class ValidationDecision:
    status: str
    reasons: tuple[str, ...]


@dataclass
class _Pending:
    signal_date: date
    risk_distance: float
    score: float


@dataclass
class _Position:
    qty: int
    entry: float
    stop: float
    target: float
    bars: int = 0


def _breadth(rows: list[pd.Series], cfg: dict) -> str:
    eligible = [r for r in rows if not pd.isna(r.get("ema200"))]
    if len(eligible) < int(cfg["min_eligible_symbols"]):
        return "RISK_OFF"
    above50 = sum(r["close"] > r["ema50"] for r in eligible) / len(eligible) * 100
    above200 = sum(r["close"] > r["ema200"] for r in eligible) / len(eligible) * 100
    momentum = float(pd.Series([r["roc20"] for r in eligible]).median())
    if (
        above50 >= float(cfg["risk_on_min_pct_above_ema50"])
        and above200 >= float(cfg["risk_on_min_pct_above_ema200"])
        and momentum >= float(cfg["risk_on_min_median_mom20_pct"])
    ):
        return "RISK_ON"
    if (
        above50 >= float(cfg["recovery_min_pct_above_ema50"])
        and above200 >= float(cfg["recovery_min_pct_above_ema200"])
        and momentum >= float(cfg["recovery_min_median_mom20_pct"])
    ):
        return "RECOVERY"
    return "RISK_OFF"


def _candidate(row: pd.Series, cfg: dict, strategy: str) -> tuple[float, float] | None:
    candidate = evaluate_candidate(row, cfg, strategy)
    if candidate is None:
        return None
    return candidate.score, candidate.risk_distance


def _portfolio_fold(
    year: int,
    frames: dict[str, pd.DataFrame],
    cfg: dict,
    strategy: str,
) -> FoldResult:
    risk, paper = cfg["risk"], cfg["paper"]
    dates = sorted(
        {
            pd.Timestamp(index).date()
            for frame in frames.values()
            for index in frame.index
            if pd.Timestamp(index).year == year
        }
    )
    starting_equity = float(risk["paper_equity_sar"])
    equity, peak, max_dd = starting_equity, starting_equity, 0.0
    commission = float(paper["commission_bps"]) / 10000
    slippage = float(paper["slippage_bps"]) / 10000
    historical_regime = dict(cfg["market_regime"])
    historical_regime["min_eligible_symbols"] = historical_regime.get(
        "historical_min_eligible_symbols",
        historical_regime["min_eligible_symbols"],
    )
    pending: dict[str, _Pending] = {}
    positions: dict[str, _Position] = {}
    pnls: list[float] = []

    for session in dates:
        timestamp = pd.Timestamp(session)
        rows = {
            s: f.loc[timestamp].copy()
            for s, f in frames.items()
            if timestamp in f.index
        }
        roc63 = pd.Series(
            {symbol: float(row["roc63"]) for symbol, row in rows.items()}
        )
        percentiles = roc63.rank(method="average", pct=True)
        for symbol, row in rows.items():
            row["xs_roc63_percentile"] = float(percentiles[symbol])

        opened = 0
        for symbol, order in sorted(pending.items(), key=lambda x: x[1].score, reverse=True):
            row = rows.get(symbol)
            if row is None or order.signal_date >= session:
                continue
            if len(positions) >= int(risk["max_open_positions"]):
                break
            if opened >= int(risk["max_daily_new_positions"]):
                break
            entry = float(row["open"]) * (1 + slippage)
            qty = min(
                int(equity * float(risk["risk_per_trade_pct"]) / 100 / order.risk_distance),
                int(equity * float(risk["max_position_pct"]) / 100 / entry),
            )
            if qty > 0:
                positions[symbol] = _Position(
                    qty,
                    entry,
                    entry - order.risk_distance,
                    entry + float(risk["reward_risk"]) * order.risk_distance,
                )
                equity -= qty * entry * commission
                opened += 1
        pending.clear()

        for symbol in list(positions):
            row = rows.get(symbol)
            if row is None:
                continue
            position = positions[symbol]
            position.bars += 1
            exit_price = None
            if float(row["low"]) <= position.stop:
                exit_price = position.stop * (1 - slippage)
            elif float(row["high"]) >= position.target:
                exit_price = position.target * (1 - slippage)
            elif position.bars >= int(paper["max_hold_days"]):
                exit_price = float(row["close"]) * (1 - slippage)
            if exit_price is not None:
                pnl = (
                    position.qty * (exit_price - position.entry)
                    - position.qty * exit_price * commission
                )
                equity += pnl
                pnls.append(pnl)
                del positions[symbol]

        marked = equity + sum(
            p.qty * (float(rows[s]["close"]) - p.entry)
            for s, p in positions.items()
            if s in rows
        )
        peak = max(peak, marked)
        max_dd = min(max_dd, marked / peak - 1)

        regime = _breadth(list(rows.values()), historical_regime)
        if regime == "RISK_OFF":
            continue
        candidates = []
        for symbol, row in rows.items():
            if symbol in positions:
                continue
            item = _candidate(row, cfg["signals"], strategy)
            if item:
                score, distance = item
                if (
                    regime == "RECOVERY"
                    and strategy == "breakout"
                    and score
                    < float(
                        historical_regime.get(
                            "recovery_breakout_min_strategy_score",
                            105,
                        )
                    )
                ):
                    continue
                candidates.append((score, symbol, distance))
        slots = max(0, int(risk["max_open_positions"]) - len(positions))
        for score, symbol, distance in sorted(candidates, reverse=True)[:slots]:
            pending[symbol] = _Pending(session, distance, score)

    # Folds are deliberately independent calendar-year tests. Realise every
    # remaining position at that symbol's final available close in the year so
    # return, trade count, and profit factor never omit an open loss or profit.
    for symbol, position in list(positions.items()):
        frame = frames[symbol]
        year_rows = frame[pd.DatetimeIndex(frame.index).year == year]
        if year_rows.empty:
            continue
        exit_price = float(year_rows.iloc[-1]["close"]) * (1 - slippage)
        pnl = (
            position.qty * (exit_price - position.entry)
            - position.qty * exit_price * commission
        )
        equity += pnl
        pnls.append(pnl)
        del positions[symbol]
    peak = max(peak, equity)
    max_dd = min(max_dd, equity / peak - 1)

    if not pnls:
        return FoldResult(year, 0, 0.0, 0.0, 0.0, 0.0, strategy)
    series = pd.Series(pnls)
    wins, losses = float(series[series > 0].sum()), float(-series[series < 0].sum())
    factor = wins / losses if losses else float("inf")
    return FoldResult(
        year,
        len(series),
        round(float((series > 0).mean() * 100), 2),
        round((equity / starting_equity - 1) * 100, 2),
        round(max_dd * 100, 2),
        round(factor, 2),
        strategy,
    )


def evaluate_strategy_lab(
    histories: dict[str, pd.DataFrame],
    settings: dict,
) -> tuple[list[FoldResult], dict[tuple[str, int], FoldResult]]:
    all_dates = [pd.Timestamp(i).date() for h in histories.values() for i in h.index]
    if not all_dates:
        return [], {}
    frames = {}
    for symbol, history in histories.items():
        frame = enrich_strategy(history)
        frames[symbol] = frame.dropna()
    latest_year = max(all_dates).year
    lab = settings["validation"].get("strategy_lab", {})
    training_years = int(lab.get("training_years", 2))
    oos_years = int(lab.get("oos_years", 4))
    first_training_year = latest_year - (training_years + oos_years - 1)
    matrix = {
        (strategy, year): _portfolio_fold(year, frames, settings, strategy)
        for strategy in STRATEGIES
        for year in range(first_training_year, latest_year + 1)
    }
    selected_folds = []
    for target_year in range(latest_year - oos_years + 1, latest_year + 1):
        strategy = _select_strategy(matrix, target_year, lab)
        if strategy is None:
            selected_folds.append(FoldResult(target_year, 0, 0, 0, 0, 0, "CASH"))
            continue
        selected_folds.append(matrix[(strategy, target_year)])
    return selected_folds, matrix


def walk_forward(histories: dict[str, pd.DataFrame], settings: dict) -> list[FoldResult]:
    folds, _ = evaluate_strategy_lab(histories, settings)
    return folds


def _select_strategy(
    matrix: dict[tuple[str, int], FoldResult],
    target_year: int,
    lab: dict,
    strategies: tuple[str, ...] = STRATEGIES,
) -> str | None:
    choices = []
    for strategy in strategies:
        training_years = int(lab.get("training_years", 2))
        training = [
            matrix[(strategy, target_year - offset)]
            for offset in range(training_years, 0, -1)
        ]
        trades = sum(f.trades for f in training)
        combined_return = sum(f.return_pct for f in training)
        factors = [f.profit_factor for f in training if f.trades]
        median_factor = float(pd.Series(factors).median()) if factors else 0
        worst_dd = min((f.max_drawdown_pct for f in training), default=-100)
        if (
            trades < int(lab.get("min_train_trades", 12))
            or combined_return <= 0
            or median_factor < float(lab.get("min_train_profit_factor", 1.05))
        ):
            continue
        score = combined_return + 10 * (median_factor - 1) + worst_dd * 0.25
        choices.append((score, strategy))
    return max(choices)[1] if choices else None


def decide(folds: list[FoldResult], settings: dict) -> ValidationDecision:
    trades = sum(f.trades for f in folds)
    profitable = sum(f.return_pct > 0 for f in folds)
    win_rate = sum(f.win_rate * f.trades for f in folds) / trades if trades else 0
    worst_dd = min((f.max_drawdown_pct for f in folds), default=-100)
    factors = [f.profit_factor for f in folds if f.trades and f.profit_factor != float("inf")]
    median_factor = float(pd.Series(factors).median()) if factors else 0
    checks = {
        "minimum out-of-sample trades": trades >= int(settings["min_oos_trades"]),
        "minimum weighted win rate": win_rate >= float(settings["min_win_rate_pct"]),
        "minimum median profit factor": median_factor >= float(settings["min_profit_factor"]),
        "maximum drawdown": abs(worst_dd) <= float(settings["max_drawdown_pct"]),
        "profitable folds": profitable >= int(settings["min_profitable_folds"]),
        "positive combined fold return": sum(f.return_pct for f in folds) > 0,
    }
    failed = tuple(name for name, passed in checks.items() if not passed)
    return ValidationDecision("BACKTEST_PASS" if not failed else "BLOCK", failed)


def write_report(
    output_dir: Path,
    folds: list[FoldResult],
    decision: ValidationDecision,
    matrix: dict[tuple[str, int], FoldResult] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_rows = [
        asdict(matrix[key])
        for key in sorted(matrix or {}, key=lambda item: (item[1], item[0]))
    ]
    payload = {
        "decision": asdict(decision),
        "folds": [asdict(f) for f in folds],
        "strategy_matrix": matrix_rows,
    }
    (output_dir / "validation_report.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    lines = [
        "# Saudi Trading Bot — Nested Strategy Lab V3.1",
        "",
        f"Decision: **{decision.status}**",
        "",
        "One portfolio with breadth regime, ranking, next-session fills, position limits,",
        "commission, slippage, stops, targets, and maximum holding period.",
        "Current Sharia allowlist is applied historically (survivorship-bias limitation).",
        "",
        "Each OOS year uses only prior years to select among five predefined",
        "strategy families. The target year remains unseen during selection.",
        "",
        "| OOS year | Selected | Trades | Win rate | Return | Max drawdown | Profit factor |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        f"| {f.year} | {f.strategy} | {f.trades} | {f.win_rate:.1f}% | {f.return_pct:.1f}% | "
        f"{f.max_drawdown_pct:.1f}% | {f.profit_factor:.2f} |" for f in folds
    )
    if decision.reasons:
        lines.extend(["", "Failed gates:", *[f"- {r}" for r in decision.reasons]])
    if matrix_rows:
        lines.extend(
            [
                "",
                "## Full strategy matrix",
                "",
                "This diagnostic matrix is not used to select the same year's strategy.",
                "",
                "| Year | Strategy | Trades | Return | Max drawdown | Profit factor |",
                "|---:|---|---:|---:|---:|---:|",
            ]
        )
        lines.extend(
            f"| {row['year']} | {row['strategy']} | {row['trades']} | "
            f"{row['return_pct']:.1f}% | {row['max_drawdown_pct']:.1f}% | "
            f"{row['profit_factor']:.2f} |"
            for row in matrix_rows
        )
    lines.extend(["", "A PASS still requires 3–5 clean Paper sessions before a tiny pilot."])
    (output_dir / "validation_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
