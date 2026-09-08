from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


STATE_PATHS = (
    "artifacts/paper_portfolio.json",
    "artifacts/explorer_portfolio.json",
    "artifacts/pead_shadow_portfolio.json",
    "artifacts/momentum_shadow_portfolio.json",
    "artifacts/edge_lab_progress.json",
    "artifacts/forward_summary.json",
    "artifacts/decision_intelligence.json",
    "artifacts/alert_state.json",
)


def snapshot_state(root: Path = Path("."), paths: Iterable[str] = STATE_PATHS) -> dict:
    files: dict[str, object] = {}
    for rel in paths:
        path = root / rel
        if not path.exists():
            continue
        try:
            files[rel] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            files[rel] = {"raw": path.read_text(encoding="utf-8")}
    return {"version": 1, "files": files}


def restore_state(snapshot: dict, root: Path = Path(".")) -> int:
    restored = 0
    for rel, payload in snapshot.get("files", {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, dict) and set(payload) == {"raw"}:
            path.write_text(str(payload["raw"]), encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        restored += 1
    return restored


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("snapshot", "restore"))
    parser.add_argument("--file", default="artifacts/durable_state_snapshot.json")
    args = parser.parse_args()
    target = Path(args.file)

    if args.action == "snapshot":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(snapshot_state(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"DURABLE_STATE_SNAPSHOT file={target}")
        return

    if not target.exists():
        print(f"DURABLE_STATE_RESTORE skipped=missing file={target}")
        return
    restored = restore_state(json.loads(target.read_text(encoding="utf-8")))
    print(f"DURABLE_STATE_RESTORE restored={restored} file={target}")


if __name__ == "__main__":
    main()
