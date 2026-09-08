import json
from pathlib import Path

from saudi_trading_bot.state_persistence import restore_state, snapshot_state


def test_snapshot_and_restore_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "artifacts" / "pead_shadow_portfolio.json"
    src.parent.mkdir(parents=True)
    src.write_text(json.dumps({"closed": [{"symbol": "2380.SR", "r": 1.2}]}), encoding="utf-8")

    snap = snapshot_state(tmp_path, ["artifacts/pead_shadow_portfolio.json"])
    src.unlink()
    restored = restore_state(snap, tmp_path)

    assert restored == 1
    assert json.loads(src.read_text(encoding="utf-8"))["closed"][0]["r"] == 1.2
