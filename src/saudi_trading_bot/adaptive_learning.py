from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

RIYADH = ZoneInfo("Asia/Riyadh")
HORIZONS = (1, 3, 5, 10, 20)
MIN_SCORE = 60.0


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _f(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(result) else result


def _candidate(row: pd.Series, session: str) -> dict[str, Any]:
    price = _f(row.get("price")) or 0.0
    atr = _f(row.get("atr")) or 0.0
    stop = _f(row.get("stop"))
    target = _f(row.get("target"))
    if not stop or stop >= price:
        stop = price - max(0.01, atr * 1.5)
    if not target or target <= price:
        target = price + 2.0 * max(0.01, price - stop)
    risk = max(0.01, price - stop)
    return {
        "id": f"{session}:{row['symbol']}:{row.get('strategy', 'CASH')}",
        "session": session,
        "symbol": str(row["symbol"]),
        "state": str(row.get("state", "")),
        "strategy": str(row.get("strategy", "CASH")),
        "score": _f(row.get("score")),
        "strategy_score": _f(row.get("strategy_score")),
        "entry": price,
        "stop": round(stop, 4),
        "target": round(target, 4),
        "risk": round(risk, 4),
        "features": {
            "trend_score": _f(row.get("trend_score")),
            "momentum_score": _f(row.get("momentum_score")),
            "swing_score": _f(row.get("swing_score")),
            "disclosure_score": _f(row.get("disclosure_score")),
            "atr_pct": round(atr / price * 100.0, 4) if price else None,
            "ema50_distance_pct": _f(row.get("ema50_distance_pct")),
            "ema200_distance_pct": _f(row.get("ema200_distance_pct")),
            "avg_value20": _f(row.get("avg_value20")),
            "xs_roc63_percentile": _f(row.get("xs_roc63_percentile")),
            "market_regime": str(row.get("market_regime", "UNKNOWN")),
        },
        "observations": [],
        "labels": {},
        "barrier": None,
    }


def _update_observation(item: dict[str, Any], row: pd.Series, session: str) -> None:
    if session == item["session"]:
        return
    observations = item.setdefault("observations", [])
    if any(obs.get("session") == session for obs in observations):
        return
    close = _f(row.get("price"))
    high = _f(row.get("high")) or close
    low = _f(row.get("low")) or close
    if close is None:
        return
    observations.append({"session": session, "close": close, "high": high, "low": low})
    n = len(observations)
    entry = float(item["entry"])
    risk = float(item["risk"])
    for horizon in HORIZONS:
        if n == horizon:
            item["labels"][f"return_{horizon}d_pct"] = round((close / entry - 1.0) * 100.0, 4)
            item["labels"][f"return_{horizon}d_r"] = round((close - entry) / risk, 4)
    highs = [float(obs["high"]) for obs in observations]
    lows = [float(obs["low"]) for obs in observations]
    item["labels"]["mfe_r"] = round((max(highs) - entry) / risk, 4)
    item["labels"]["mae_r"] = round((min(lows) - entry) / risk, 4)
    if item.get("barrier") is None:
        target_hit = high >= float(item["target"])
        stop_hit = low <= float(item["stop"])
        if target_hit and stop_hit:
            item["barrier"] = {"result": "AMBIGUOUS_SAME_BAR", "session": session}
        elif target_hit:
            item["barrier"] = {"result": "TARGET_FIRST", "session": session}
        elif stop_hit:
            item["barrier"] = {"result": "STOP_FIRST", "session": session}


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    mature = [item for item in items if len(item.get("observations", [])) >= 20]
    barrier = [item for item in items if item.get("barrier")]
    target = sum(item["barrier"]["result"] == "TARGET_FIRST" for item in barrier)
    stop = sum(item["barrier"]["result"] == "STOP_FIRST" for item in barrier)
    usable = target + stop
    r20 = [item.get("labels", {}).get("return_20d_r") for item in mature]
    r20 = [float(value) for value in r20 if value is not None]
    return {
        "candidates": len(items),
        "mature_20d": len(mature),
        "barrier_labeled": usable,
        "target_first": target,
        "stop_first": stop,
        "target_first_pct": round(target / usable * 100.0, 2) if usable else None,
        "mean_20d_r": round(sum(r20) / len(r20), 4) if r20 else None,
        "ready_for_meta_model": len(mature) >= 100,
        "meta_model_mode": "SHADOW_ONLY",
    }


def run(root: Path = Path(".")) -> int:
    scan_path = root / "artifacts/latest_scan.csv"
    ledger_path = root / "artifacts/adaptive_learning_ledger.json"
    report_path = root / "artifacts/adaptive_learning_report.json"
    if not scan_path.exists():
        print("ADAPTIVE_LEARNING skipped=no_latest_scan")
        return 0
    frame = pd.read_csv(scan_path, dtype={"symbol": str})
    if frame.empty:
        print("ADAPTIVE_LEARNING skipped=empty_scan")
        return 0
    session = str(frame["session"].dropna().iloc[0]) if "session" in frame and not frame["session"].dropna().empty else datetime.now(RIYADH).date().isoformat()
    payload = _load(ledger_path, {"version": 1, "candidates": []})
    items = [item for item in payload.get("candidates", []) if isinstance(item, dict)]
    by_symbol = {str(row["symbol"]): row for _, row in frame.iterrows()}
    for item in items:
        row = by_symbol.get(str(item.get("symbol")))
        if row is not None:
            _update_observation(item, row, session)
    existing = {str(item.get("id")) for item in items}
    for _, row in frame.iterrows():
        score = max(_f(row.get("score")) or 0.0, _f(row.get("strategy_score")) or 0.0)
        if score < MIN_SCORE:
            continue
        candidate = _candidate(row, session)
        if candidate["id"] not in existing:
            items.append(candidate)
            existing.add(candidate["id"])
    payload = {"version": 1, "updated_at": datetime.now(RIYADH).isoformat(), "candidates": items}
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = _summary(items)
    report["updated_at"] = datetime.now(RIYADH).isoformat()
    report["session"] = session
    report["rule"] = "Observe every score>=60 candidate; never alter production thresholds."
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "ADAPTIVE_LEARNING "
        f"session={session} candidates={report['candidates']} mature20={report['mature_20d']} "
        f"labeled={report['barrier_labeled']} target_first={report['target_first']} "
        f"stop_first={report['stop_first']} meta_ready={report['ready_for_meta_model']} "
        "mode=SHADOW_ONLY"
    )
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
