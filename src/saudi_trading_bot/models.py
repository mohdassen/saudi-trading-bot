from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SignalState(str, Enum):
    BLOCKED = "BLOCKED"
    IGNORE = "IGNORE"
    WATCH = "WATCH"
    READY = "READY"


@dataclass(frozen=True)
class DisclosureImpact:
    score: float
    label: str
    title: str = ""
    url: str = ""


@dataclass(frozen=True)
class Signal:
    symbol: str
    state: SignalState
    total_score: float
    trend_score: float
    momentum_score: float
    swing_score: float
    disclosure_score: float
    price: float
    stop: float
    target: float
    atr: float
    rationale: tuple[str, ...]
    generated_at: datetime
    strategy: str = "CASH"
    strategy_score: float = 0.0
