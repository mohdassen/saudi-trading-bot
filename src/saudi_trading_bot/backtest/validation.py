from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .core import simulate_trade_returns, summarize_returns


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


def walk_forward(
    histories: dict[str, pd.DataFrame],
    commission_bps: float,
    slippage_bps: float,
    max_hold_days: int,
) -> list[FoldResult]:
    all_dates = [pd.Timestamp(i).date() for h in histories.values() for i in h.index]
    if not all_dates:
        return []
    latest_year = max(all_dates).year
    folds = []
    for year in range(latest_year - 3, latest_year + 1):
        returns = []
        for history in histories.values():
            returns.extend(
                simulate_trade_returns(
                    history,
                    commission_bps,
                    slippage_bps,
                    max_hold_days,
                    signal_start=date(year, 1, 1),
                    signal_end=date(year, 12, 31),
                )
            )
        summary = summarize_returns(f"OOS-{year}", returns)
        folds.append(
            FoldResult(
                year,
                summary.trades,
                summary.win_rate,
                summary.total_return_pct,
                summary.max_drawdown_pct,
                summary.profit_factor,
            )
        )
    return folds


def decide(folds: list[FoldResult], settings: dict) -> ValidationDecision:
    trades = sum(f.trades for f in folds)
    profitable = sum(f.return_pct > 0 for f in folds)
    weighted_wins = sum(f.win_rate * f.trades for f in folds)
    win_rate = weighted_wins / trades if trades else 0.0
    gross_return = sum(f.return_pct for f in folds)
    worst_dd = min((f.max_drawdown_pct for f in folds), default=-100.0)
    finite_pf = [f.profit_factor for f in folds if f.trades and f.profit_factor != float("inf")]
    median_pf = float(pd.Series(finite_pf).median()) if finite_pf else 0.0
    checks = {
        "minimum out-of-sample trades": trades >= int(settings["min_oos_trades"]),
        "minimum weighted win rate": win_rate >= float(settings["min_win_rate_pct"]),
        "minimum median profit factor": median_pf >= float(settings["min_profit_factor"]),
        "maximum drawdown": abs(worst_dd) <= float(settings["max_drawdown_pct"]),
        "profitable folds": profitable >= int(settings["min_profitable_folds"]),
        "positive combined fold return": gross_return > 0,
    }
    failed = tuple(name for name, passed in checks.items() if not passed)
    return ValidationDecision("BACKTEST_PASS" if not failed else "BLOCK", failed)


def write_report(
    output_dir: Path,
    folds: list[FoldResult],
    decision: ValidationDecision,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"decision": asdict(decision), "folds": [asdict(f) for f in folds]}
    (output_dir / "validation_report.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    lines = [
        "# Saudi Trading Bot — Accelerated Validation",
        "",
        f"Decision: **{decision.status}**",
        "",
        "This is research validation, not a promise of profit or permission for live trading.",
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
    lines.extend([
        "",
        "A BACKTEST_PASS still requires 3–5 clean Paper sessions before a tiny pilot.",
    ])
    (output_dir / "validation_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
