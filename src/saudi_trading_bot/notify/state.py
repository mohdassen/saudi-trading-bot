from __future__ import annotations

import json
from pathlib import Path

from saudi_trading_bot.models import Signal


class AlertState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.state: dict[str, str] = {}
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                self.state = {}

    def changed(self, signal: Signal) -> bool:
        new = f"{signal.strategy}:{signal.state.value}"
        old = self.state.get(signal.symbol)
        self.state[signal.symbol] = new
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return old != new
