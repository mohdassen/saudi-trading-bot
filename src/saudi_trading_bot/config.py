from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    root: Path

    def section(self, name: str) -> dict[str, Any]:
        return self.raw[name]

    def path(self, value: str) -> Path:
        return (self.root / value).resolve()


def load_settings(path: str | Path = "config/settings.yaml") -> Settings:
    p = Path(path).resolve()
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Settings(raw=raw, root=p.parent.parent)
