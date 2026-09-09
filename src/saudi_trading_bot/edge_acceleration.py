from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from saudi_trading_bot.config import load_settings
from saudi_trading_bot.decision_intelligence import _load_json, _score_strategy

RIYADH = ZoneInfo("Asia/Riyadh")


def _clean_trade(trade: dict[str, Any]) -> bool:
    try:
        risk = float(trade.get("initial_risk_sar", 0.0) or 0.0)
        float(trade.get("pnl_sar", 0.0))
    except (TypeError, ValueError):
        return False
    return risk > 0 and bool(str(trade.get("strategy", "")).strip())


def _coverage(trades: list[dict[str, Any]], field: str) -> float:
    if not trades:
        return 0.0
    present = sum(trade.get(field) not in (None, "", "UNKNOWN") for trade in trades)
    return round(present / len(trades) * 100.0, 1)


def _evidence(name: str, payload: dict[str, Any], target: int) -> dict[str, Any]:
    closed = [item for item in payload.get("closed", []) if isinstance(item, dict)]
    clean = [item for item in closed if _clean_trade(item)]
    score = _score_strategy(name, {**payload, "closed": clean}, target)
    return {
        "name": name,
        "clean_closed": len(clean),
        "raw_closed": len(closed),
        "target": target,
        "progress_pct": round(min(100.0, len(clean) / target * 100.0), 1),
        "open_positions": len(payload.get("positions", {})),
        "pending": len(payload.get("pending", {})),
        "win_rate_pct": score.win_rate_pct,
        "expectancy_r": score.expectancy_r,
        "profit_factor": score.profit_factor,
        "max_drawdown_r": score.max_drawdown_r,
        "regime_coverage_pct": _coverage(clean, "entry_regime"),
        "mae_coverage_pct": _coverage(clean, "mae_r"),
        "mfe_coverage_pct": _coverage(clean, "mfe_r"),
        "status": score.status,
    }


def build_report() -> dict[str, Any]:
    cfg = load_settings()
    edge = cfg.section("edge_lab")
    target = int(edge["target_closed_trades"])
    paths = {
        "PEAD": cfg.path(edge["pead"]["portfolio_file"]),
        "Momentum Leadership": cfg.path(edge["momentum"]["portfolio_file"]),
        "Explorer Control": cfg.path(cfg.section("explorer")["portfolio_file"]),
    }
    evidence = [
        _evidence(name, _load_json(path), target)
        for name, path in paths.items()
    ]
    evidence.sort(
        key=lambda item: (
            item["clean_closed"],
            item["open_positions"] + item["pending"],
        ),
        reverse=True,
    )
    total_clean = sum(item["clean_closed"] for item in evidence)
    total_pipeline = sum(item["open_positions"] + item["pending"] for item in evidence)
    focus = evidence[0]["name"] if evidence else "UNDECIDED"
    return {
        "updated_at": datetime.now(RIYADH).isoformat(),
        "mode": "EDGE_ACCELERATION",
        "production_strategy": "CASH",
        "objective": "Maximize clean forward evidence without relaxing entry thresholds.",
        "rules": {
            "no_threshold_relaxation": True,
            "no_paid_data": True,
            "no_auto_production_promotion": True,
            "primary_metric": "clean_closed_trades",
            "qualification_target_per_edge": target,
        },
        "portfolio": evidence,
        "total_clean_closed": total_clean,
        "active_evidence_pipeline": total_pipeline,
        "current_focus": focus,
    }


def run() -> int:
    cfg = load_settings()
    report = build_report()
    output = cfg.path("artifacts/edge_acceleration.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "EDGE_ACCELERATION "
        f"clean_closed={report['total_clean_closed']} "
        f"pipeline={report['active_evidence_pipeline']} "
        f"focus={report['current_focus']} production=CASH"
    )
    for item in report["portfolio"]:
        print(
            "EDGE_EVIDENCE "
            f"name={item['name']} clean={item['clean_closed']}/{item['target']} "
            f"raw={item['raw_closed']} open={item['open_positions']} "
            f"pending={item['pending']} E={item['expectancy_r']}R "
            f"PF={item['profit_factor']} DD={item['max_drawdown_r']}R "
            f"regime={item['regime_coverage_pct']}% MAE={item['mae_coverage_pct']}% "
            f"MFE={item['mfe_coverage_pct']}%"
        )
    print(f"EDGE_ACCELERATION_FILE {output}")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
