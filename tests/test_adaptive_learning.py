from pathlib import Path

import pandas as pd

from saudi_trading_bot.adaptive_learning import run


def _scan(path: Path, session: str, price: float, high: float, low: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {
            "symbol": "2380",
            "session": session,
            "state": "WATCH",
            "score": 85.0,
            "strategy": "CASH",
            "strategy_score": 0.0,
            "price": price,
            "high": high,
            "low": low,
            "atr": 1.0,
            "stop": price - 1.5,
            "target": price + 3.0,
            "market_regime": "RISK_OFF",
        }
    ]).to_csv(path, index=False)


def test_learning_ledger_accumulates_future_observations(tmp_path: Path) -> None:
    scan = tmp_path / "artifacts/latest_scan.csv"
    _scan(scan, "2026-09-14", 20.0, 20.2, 19.8)
    assert run(tmp_path) == 0
    _scan(scan, "2026-09-15", 21.0, 21.2, 20.5)
    assert run(tmp_path) == 0
    import json

    ledger = json.loads((tmp_path / "artifacts/adaptive_learning_ledger.json").read_text())
    first = ledger["candidates"][0]
    assert len(first["observations"]) == 1
    assert first["labels"]["return_1d_pct"] == 5.0
    assert first["features"]["market_regime"] == "RISK_OFF"


def test_low_score_is_not_added(tmp_path: Path) -> None:
    path = tmp_path / "artifacts/latest_scan.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"symbol": "1", "session": "2026-09-15", "score": 59, "price": 10}]).to_csv(path, index=False)
    assert run(tmp_path) == 0
    import json

    ledger = json.loads((tmp_path / "artifacts/adaptive_learning_ledger.json").read_text())
    assert ledger["candidates"] == []
