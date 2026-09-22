from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from saudi_trading_bot.paper.portfolio import PaperPortfolio
from saudi_trading_bot.profit_protection_shadow import evaluate_profit_protection


def _risk_bucket(mae_r: float, mfe_r: float, bars: int) -> str:
    if mfe_r >= 1.0:
        return "PROFIT_PROTECTION_CANDIDATE"
    if bars >= 5 and mfe_r < 0.25:
        return "WEAK_FOLLOW_THROUGH"
    if mae_r <= -0.65 and mfe_r < 0.50:
        return "LOSS_PATTERN_RISK"
    return "DEVELOPING"


def _portfolio_rows(name: str, path: Path) -> list[dict[str, Any]]:
    portfolio = PaperPortfolio.load(path)
    rows: list[dict[str, Any]] = []
    for p in portfolio.positions.values():
        row = {
            "edge": name,
            "symbol": p.symbol,
            "score": p.score,
            "bars_held": p.bars_held,
            "entry_regime": p.entry_regime,
            "mae_r": round(float(p.mae_r), 3),
            "mfe_r": round(float(p.mfe_r), 3),
            "diagnosis": _risk_bucket(float(p.mae_r), float(p.mfe_r), int(p.bars_held)),
        }
        row["profit_protection_shadow"] = evaluate_profit_protection(p)
        rows.append(row)
    return rows


def build_report(root: Path = Path(".")) -> dict[str, Any]:
    rows = []
    rows += _portfolio_rows("Explorer Control", root / "artifacts/explorer_portfolio.json")
    rows += _portfolio_rows("Momentum Leadership", root / "artifacts/momentum_shadow_portfolio.json")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "SHADOW_DIAGNOSTIC_ONLY",
        "production_changed": False,
        "open_positions": len(rows),
        "diagnosis_counts": {
            label: sum(r["diagnosis"] == label for r in rows)
            for label in (
                "PROFIT_PROTECTION_CANDIDATE",
                "WEAK_FOLLOW_THROUGH",
                "LOSS_PATTERN_RISK",
                "DEVELOPING",
            )
        },
        "positions": rows,
    }


def main() -> int:
    report = build_report()
    out = Path("artifacts/midtrade_forensics.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        "MIDTRADE_FORENSICS "
        f"open={report['open_positions']} "
        + " ".join(f"{k}={v}" for k, v in report["diagnosis_counts"].items())
    )
    for row in report["positions"]:
        print(
            f"MIDTRADE symbol={row['symbol']} edge={row['edge']} bars={row['bars_held']} "
            f"MAE={row['mae_r']}R MFE={row['mfe_r']}R regime={row['entry_regime']} "
            f"diagnosis={row['diagnosis']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
