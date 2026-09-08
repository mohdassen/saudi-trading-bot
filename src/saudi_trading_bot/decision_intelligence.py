from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.config import load_settings
from saudi_trading_bot.data.cache import MarketDataCache

RIYADH = ZoneInfo("Asia/Riyadh")


@dataclass(frozen=True)
class StrategyScore:
    name: str
    closed: int
    target: int
    wins: int
    win_rate_pct: float
    expectancy_r: float | None
    profit_factor: float
    max_drawdown_r: float | None
    pnl_sar: float
    open_positions: int
    pending: int
    status: str
    rank_score: float | None


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _r_values(trades: list[dict[str, Any]]) -> list[float]:
    result = []
    for trade in trades:
        risk = float(trade.get("initial_risk_sar", 0.0) or 0.0)
        if risk > 0:
            result.append(float(trade.get("pnl_sar", 0.0)) / risk)
    return result


def _max_drawdown_r(values: list[float]) -> float | None:
    if not values:
        return None
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return abs(worst)


def _score_strategy(name: str, payload: dict[str, Any], target: int) -> StrategyScore:
    trades = [x for x in payload.get("closed", []) if isinstance(x, dict)]
    pnl_values = [float(x.get("pnl_sar", 0.0)) for x in trades]
    wins = sum(x > 0 for x in pnl_values)
    gains = sum(max(0.0, x) for x in pnl_values)
    losses = abs(sum(min(0.0, x) for x in pnl_values))
    pf = gains / losses if losses else (999.0 if gains else 0.0)
    r_values = _r_values(trades)
    expectancy = sum(r_values) / len(r_values) if r_values else None
    drawdown = _max_drawdown_r(r_values)
    closed = len(trades)
    if closed < 5:
        status = f"LEARNING {closed}/{target}"
        rank_score = None
    else:
        status = f"EVALUATING {closed}/{target}" if closed < target else "MATURE"
        rank_score = (
            (expectancy or -9.0) * 100.0
            + min(pf, 3.0) * 10.0
            - (drawdown or 0.0) * 5.0
        )
    return StrategyScore(
        name=name,
        closed=closed,
        target=target,
        wins=wins,
        win_rate_pct=round(wins / closed * 100.0, 2) if closed else 0.0,
        expectancy_r=None if expectancy is None else round(expectancy, 3),
        profit_factor=round(pf, 3),
        max_drawdown_r=None if drawdown is None else round(drawdown, 3),
        pnl_sar=round(sum(pnl_values), 2),
        open_positions=len(payload.get("positions", {})),
        pending=len(payload.get("pending", {})),
        status=status,
        rank_score=None if rank_score is None else round(rank_score, 2),
    )


def _loss_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    trades = [x for x in payload.get("closed", []) if isinstance(x, dict)]
    losing = [x for x in trades if float(x.get("pnl_sar", 0.0)) < 0]
    reasons = Counter(str(x.get("reason", "unknown")) for x in losing)
    strategies = Counter(str(x.get("strategy", "unknown")) for x in losing)
    r_losses = [abs(x) for x in _r_values(losing) if x < 0]
    return {
        "closed_losses": len(losing),
        "loss_rate_pct": round(len(losing) / len(trades) * 100.0, 2) if trades else 0.0,
        "reasons": dict(reasons.most_common()),
        "strategies": dict(strategies.most_common()),
        "avg_loss_r": round(sum(r_losses) / len(r_losses), 3) if r_losses else None,
        "diagnosis": _diagnosis(reasons),
    }


def _diagnosis(reasons: Counter[str]) -> str:
    if not reasons:
        return "NO_CLOSED_LOSSES_YET"
    total = sum(reasons.values())
    if reasons.get("stop", 0) / total >= 0.70:
        return "STOP_DOMINATED: review entry quality before changing stop width"
    if reasons.get("max_hold", 0) / total >= 0.50:
        return "TIMEOUT_DOMINATED: edge may lack follow-through"
    return "MIXED: collect more forward trades before changing rules"


def _market_regime(cfg) -> dict[str, Any]:
    sharia = pd.read_csv(
        cfg.path(cfg.section("sharia")["allowlist_file"]), dtype={"symbol": str}
    )
    symbols = sharia.loc[
        sharia["status"].astype(str).str.lower().eq("allowed"), "symbol"
    ].astype(str)
    cache = MarketDataCache(cfg.path(cfg.section("data")["cache_dir"]))
    above50 = []
    above200 = []
    momentum20 = []
    for symbol in symbols:
        frame = cache.load(symbol).sort_index()
        if len(frame) < 220 or "close" not in frame:
            continue
        close = frame["close"].astype(float)
        last = float(close.iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        above50.append(last > ema50)
        above200.append(last > ema200)
        momentum20.append(last / float(close.iloc[-21]) - 1.0)
    eligible = len(above50)
    if not eligible:
        return {"regime": "UNKNOWN", "eligible": 0}
    pct50 = sum(above50) / eligible * 100.0
    pct200 = sum(above200) / eligible * 100.0
    med20 = median(momentum20) * 100.0
    rules = cfg.section("market_regime")
    risk_on = (
        pct50 >= float(rules["risk_on_min_pct_above_ema50"])
        and pct200 >= float(rules["risk_on_min_pct_above_ema200"])
        and med20 >= float(rules["risk_on_min_median_mom20_pct"])
    )
    recovery = (
        pct50 >= float(rules["recovery_min_pct_above_ema50"])
        and pct200 >= float(rules["recovery_min_pct_above_ema200"])
        and med20 >= float(rules["recovery_min_median_mom20_pct"])
    )
    regime = "RISK_ON" if risk_on else ("RECOVERY" if recovery else "RISK_OFF")
    return {
        "regime": regime,
        "eligible": eligible,
        "above_ema50_pct": round(pct50, 1),
        "above_ema200_pct": round(pct200, 1),
        "median_momentum20_pct": round(med20, 1),
    }


def build_report() -> dict[str, Any]:
    cfg = load_settings()
    target = int(cfg.section("edge_lab")["target_closed_trades"])
    paths = {
        "PEAD": cfg.path(cfg.section("edge_lab")["pead"]["portfolio_file"]),
        "Momentum Leadership": cfg.path(
            cfg.section("edge_lab")["momentum"]["portfolio_file"]
        ),
        "Explorer Control": cfg.path(cfg.section("explorer")["portfolio_file"]),
    }
    raw = {name: _load_json(path) for name, path in paths.items()}
    scores = [_score_strategy(name, raw[name], target) for name in paths]
    mature = [x for x in scores if x.rank_score is not None]
    mature.sort(key=lambda x: x.rank_score or -9999.0, reverse=True)
    champion = mature[0].name if mature else "UNDECIDED"
    challenger = mature[1].name if len(mature) > 1 else "UNDECIDED"
    return {
        "updated_at": datetime.now(RIYADH).isoformat(),
        "production_strategy": "CASH",
        "market_regime": _market_regime(cfg),
        "scoreboard": [asdict(x) for x in scores],
        "loss_analyzer": {name: _loss_analysis(raw[name]) for name in paths},
        "champion_challenger": {
            "champion": champion,
            "challenger": challenger,
            "minimum_closed_before_ranking": 5,
            "promotion_requires_full_forward_gate": True,
            "production_auto_promotion": False,
        },
        "router": {
            "mode": "SHADOW_ADVISORY_ONLY",
            "rule": "Prefer the strongest forward-proven edge for the current regime; remain CASH until qualification gates pass.",
        },
    }


def run() -> int:
    cfg = load_settings()
    report = build_report()
    output = cfg.path("artifacts/decision_intelligence.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    regime = report["market_regime"]
    print(
        "DECISION_INTELLIGENCE "
        f"regime={regime.get('regime')} eligible={regime.get('eligible')} "
        f"champion={report['champion_challenger']['champion']} "
        f"production={report['production_strategy']}"
    )
    for item in report["scoreboard"]:
        print(
            "EDGE_SCORE "
            f"name={item['name']} closed={item['closed']}/{item['target']} "
            f"WR={item['win_rate_pct']:.1f}% E={item['expectancy_r']}R "
            f"PF={item['profit_factor']} DD={item['max_drawdown_r']}R "
            f"status={item['status']}"
        )
    for name, loss in report["loss_analyzer"].items():
        print(f"LOSS_ANALYZER {name}: {loss['diagnosis']}")
    print(f"DECISION_INTELLIGENCE_FILE {output}")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
