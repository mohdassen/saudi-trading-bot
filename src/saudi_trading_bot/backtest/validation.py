from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from saudi_trading_bot.signals.indicators import enrich


@dataclass(frozen=True)
class FoldResult:
    year: int
    trades: int
    win_rate: float
    return_pct: float
    max_drawdown_pct: float
    profit_factor: float


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


def _candidate(row: pd.Series, cfg: dict) -> tuple[float, float] | None:
    price = float(row["close"])
    rsi, momentum = float(row["rsi14"]), float(row["roc20"])
    volume = float(row["vol_ratio"])
    rules = cfg["entry_rules"]
    valid = (
        price >= float(cfg["min_price_sar"])
        and float(row["avg_value20"]) >= float(cfg["min_avg_value_sar_20d"])
        and price > row["ema20"] > row["ema50"] > row["ema200"]
        and float(rules["rsi_min"]) <= rsi <= float(rules["rsi_max"])
        and float(rules["momentum_20d_min_pct"])
        <= momentum
        <= float(rules["momentum_20d_max_pct"])
        and volume >= float(rules["volume_ratio_min"])
        and price >= float(row["high20"]) * float(rules["near_high20_ratio"])
    )
    if not valid:
        return None
    score = momentum * 2 + volume * 15 + (68 - abs(60 - rsi))
    return score, max(0.01, float(row["atr14"]) * 1.8)


def _portfolio_fold(year: int, frames: dict[str, pd.DataFrame], cfg: dict) -> FoldResult:
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
    pending: dict[str, _Pending] = {}
    positions: dict[str, _Position] = {}
    pnls: list[float] = []

    for session in dates:
        timestamp = pd.Timestamp(session)
        rows = {s: f.loc[timestamp] for s, f in frames.items() if timestamp in f.index}

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

        regime = _breadth(list(rows.values()), cfg["market_regime"])
        if regime == "RISK_OFF":
            continue
        candidates = []
        for symbol, row in rows.items():
            if symbol in positions:
                continue
            item = _candidate(row, cfg["signals"])
            if item:
                score, distance = item
                if regime == "RECOVERY" and score < 105:
                    continue
                candidates.append((score, symbol, distance))
        slots = max(0, int(risk["max_open_positions"]) - len(positions))
        for score, symbol, distance in sorted(candidates, reverse=True)[:slots]:
            pending[symbol] = _Pending(session, distance, score)

    if not pnls:
        return FoldResult(year, 0, 0.0, 0.0, 0.0, 0.0)
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
    )


def walk_forward(histories: dict[str, pd.DataFrame], settings: dict) -> list[FoldResult]:
    all_dates = [pd.Timestamp(i).date() for h in histories.values() for i in h.index]
    if not all_dates:
        return []
    frames = {symbol: enrich(history).dropna() for symbol, history in histories.items()}
    latest_year = max(all_dates).year
    return [
        _portfolio_fold(year, frames, settings)
        for year in range(latest_year - 3, latest_year + 1)
    ]


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


def write_report(output_dir: Path, folds: list[FoldResult], decision: ValidationDecision) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"decision": asdict(decision), "folds": [asdict(f) for f in folds]}
    (output_dir / "validation_report.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    lines = [
        "# Saudi Trading Bot — Portfolio Walk-Forward Validation v2",
        "",
        f"Decision: **{decision.status}**",
        "",
        "One portfolio with breadth regime, ranking, next-session fills, position limits,",
        "commission, slippage, stops, targets, and maximum holding period.",
        "Current Sharia allowlist is applied historically (survivorship-bias limitation).",
        "",
        "| OOS year | Trades | Win rate | Return | Max drawdown | Profit factor |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(
        f"| {f.year} | {f.trades} | {f.win_rate:.1f}% | {f.return_pct:.1f}% | "
        f"{f.max_drawdown_pct:.1f}% | {f.profit_factor:.2f} |" for f in folds
    )
    if decision.reasons:
        lines.extend(["", "Failed gates:", *[f"- {r}" for r in decision.reasons]])
    lines.extend(["", "A PASS still requires 3–5 clean Paper sessions before a tiny pilot."])
    (output_dir / "validation_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
