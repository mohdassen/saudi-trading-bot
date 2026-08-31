from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
import json

from saudi_trading_bot.models import Signal, SignalState


@dataclass
class PaperPosition:
    symbol: str
    qty: int
    entry: float
    stop: float
    target: float
    score: float
    opened_on: str


@dataclass
class PaperTrade:
    symbol: str
    qty: int
    entry: float
    exit: float
    opened_on: str
    closed_on: str
    reason: str
    pnl_sar: float
    return_pct: float


class PaperPortfolio:
    def __init__(
        self,
        path: str | Path,
        equity_sar: float,
        risk_pct: float,
        max_position_pct: float,
        max_open_positions: int = 5,
        max_daily_new_positions: int = 2,
        commission_bps: float = 15.5,
        slippage_bps: float = 10.0,
    ):
        self.path = Path(path)
        self.equity_sar = equity_sar
        self.risk_pct = risk_pct
        self.max_position_pct = max_position_pct
        self.max_open_positions = max_open_positions
        self.max_daily_new_positions = max_daily_new_positions
        self.commission_bps = commission_bps
        self.slippage_bps = slippage_bps
        self.positions: dict[str, PaperPosition] = {}
        self.closed: list[PaperTrade] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.positions = {
                k: PaperPosition(**v) for k, v in raw.get("positions", {}).items()
            }
            self.closed = [PaperTrade(**v) for v in raw.get("closed", [])]
            self.equity_sar = float(raw.get("equity_sar", self.equity_sar))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "equity_sar": round(self.equity_sar, 2),
            "positions": {k: asdict(v) for k, v in self.positions.items()},
            "closed": [asdict(x) for x in self.closed[-500:]],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _opened_today(self, today: date) -> int:
        iso = today.isoformat()
        opened = sum(1 for p in self.positions.values() if p.opened_on == iso)
        already_closed = sum(1 for t in self.closed if t.opened_on == iso)
        return opened + already_closed

    def consider(
        self,
        signal: Signal,
        today: date | None = None,
    ) -> PaperPosition | None:
        today = today or date.today()
        if signal.state != SignalState.READY or signal.symbol in self.positions:
            return None
        if len(self.positions) >= self.max_open_positions:
            return None
        if self._opened_today(today) >= self.max_daily_new_positions:
            return None

        risk_cash = self.equity_sar * self.risk_pct / 100.0
        per_share_risk = max(0.01, signal.price - signal.stop)
        qty_by_risk = int(risk_cash / per_share_risk)
        qty_by_cap = int(
            (self.equity_sar * self.max_position_pct / 100.0) / signal.price
        )
        qty = max(0, min(qty_by_risk, qty_by_cap))
        if qty < 1:
            return None

        entry_exec = signal.price * (1 + self.slippage_bps / 10000.0)
        p = PaperPosition(
            signal.symbol,
            qty,
            round(entry_exec, 4),
            signal.stop,
            signal.target,
            signal.total_score,
            today.isoformat(),
        )
        self.positions[signal.symbol] = p
        self.save()
        return p

    def mark_daily_bar(
        self,
        symbol: str,
        low: float,
        high: float,
        close: float,
        today: date | None = None,
        max_hold_days: int = 25,
    ) -> PaperTrade | None:
        today = today or date.today()
        p = self.positions.get(symbol)
        if not p:
            return None

        opened = date.fromisoformat(p.opened_on)
        held = (today - opened).days
        raw_exit = None
        reason = ""
        if low <= p.stop:
            raw_exit, reason = p.stop, "stop"
        elif high >= p.target:
            raw_exit, reason = p.target, "target"
        elif held >= max_hold_days:
            raw_exit, reason = close, "max_hold"
        if raw_exit is None:
            return None

        exit_exec = raw_exit * (1 - self.slippage_bps / 10000.0)
        gross = (exit_exec - p.entry) * p.qty
        commission = (
            (p.entry + exit_exec) * p.qty * self.commission_bps / 10000.0
        )
        pnl = gross - commission
        ret = pnl / (p.entry * p.qty) * 100
        trade = PaperTrade(
            p.symbol,
            p.qty,
            p.entry,
            round(exit_exec, 4),
            p.opened_on,
            today.isoformat(),
            reason,
            round(pnl, 2),
            round(ret, 2),
        )
        self.closed.append(trade)
        self.equity_sar += pnl
        del self.positions[symbol]
        self.save()
        return trade
