from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.models import Signal, SignalState

RIYADH = ZoneInfo("Asia/Riyadh")


@dataclass
class PendingEntry:
    symbol: str
    score: float
    risk_distance: float
    reward_risk: float
    signal_bar_date: str
    queued_on: str
    strategy: str = "unknown"


@dataclass
class PaperPosition:
    symbol: str
    qty: int
    entry: float
    stop: float
    target: float
    score: float
    opened_on: str
    last_marked_on: str = ""
    bars_held: int = 0
    strategy: str = "unknown"
    entry_regime: str = "UNKNOWN"
    mae_r: float = 0.0
    mfe_r: float = 0.0


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
    strategy: str = "unknown"
    initial_risk_sar: float = 0.0
    entry_regime: str = "UNKNOWN"
    mae_r: float = 0.0
    mfe_r: float = 0.0


class PaperPortfolio:
    """Persistent daily-bar paper portfolio with next-session execution."""

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
        self.pending: dict[str, PendingEntry] = {}
        self.closed: list[PaperTrade] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        positions: dict[str, PaperPosition] = {}
        for symbol, value in raw.get("positions", {}).items():
            item = dict(value)
            item.setdefault("last_marked_on", "")
            item.setdefault("bars_held", 0)
            item.setdefault("entry_regime", "UNKNOWN")
            item.setdefault("mae_r", 0.0)
            item.setdefault("mfe_r", 0.0)
            positions[symbol] = PaperPosition(**item)
        self.positions = positions
        self.pending = {k: PendingEntry(**v) for k, v in raw.get("pending", {}).items()}
        trades = []
        for value in raw.get("closed", []):
            item = dict(value)
            item.setdefault("initial_risk_sar", 0.0)
            item.setdefault("entry_regime", "UNKNOWN")
            item.setdefault("mae_r", 0.0)
            item.setdefault("mfe_r", 0.0)
            trades.append(PaperTrade(**item))
        self.closed = trades
        self.equity_sar = float(raw.get("equity_sar", self.equity_sar))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "equity_sar": round(self.equity_sar, 2),
            "positions": {k: asdict(v) for k, v in self.positions.items()},
            "pending": {k: asdict(v) for k, v in self.pending.items()},
            "closed": [asdict(x) for x in self.closed[-500:]],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def discard_unapproved_pending(self, active_strategy: str) -> list[str]:
        removed = [
            symbol
            for symbol, pending in self.pending.items()
            if active_strategy == "CASH" or pending.strategy != active_strategy
        ]
        for symbol in removed:
            self.pending.pop(symbol, None)
        if removed:
            self.save()
        return removed

    def _opened_on(self, session_date: date) -> int:
        iso = session_date.isoformat()
        opened = sum(1 for p in self.positions.values() if p.opened_on == iso)
        already_closed = sum(1 for trade in self.closed if trade.opened_on == iso)
        return opened + already_closed

    def queue(self, signal: Signal, signal_bar_date: date, reward_risk: float = 2.2) -> PendingEntry | None:
        if signal.state != SignalState.READY:
            return None
        if signal.symbol in self.positions or signal.symbol in self.pending:
            return None
        if len(self.positions) + len(self.pending) >= self.max_open_positions:
            return None
        risk_distance = max(0.01, signal.price - signal.stop)
        pending = PendingEntry(
            symbol=signal.symbol,
            score=signal.strategy_score if signal.strategy != "CASH" else signal.total_score,
            risk_distance=round(risk_distance, 4),
            reward_risk=float(reward_risk),
            signal_bar_date=signal_bar_date.isoformat(),
            queued_on=datetime.now(RIYADH).date().isoformat(),
            strategy=signal.strategy,
        )
        self.pending[signal.symbol] = pending
        self.save()
        return pending

    @staticmethod
    def _first_bar_after(history: pd.DataFrame, after_date: date) -> tuple[date, pd.Series] | None:
        if history.empty:
            return None
        for index, row in history.sort_index().iterrows():
            bar_date = pd.Timestamp(index).date()
            if bar_date > after_date:
                return bar_date, row
        return None

    def execute_pending(self, histories: dict[str, pd.DataFrame], entry_regime: str = "UNKNOWN") -> list[PaperPosition]:
        opened: list[PaperPosition] = []
        remove: set[str] = set()
        for pending in sorted(self.pending.values(), key=lambda item: item.score, reverse=True):
            history = histories.get(pending.symbol)
            if history is None or history.empty:
                continue
            next_bar = self._first_bar_after(history, date.fromisoformat(pending.signal_bar_date))
            if next_bar is None:
                continue
            session_date, row = next_bar
            if len(self.positions) >= self.max_open_positions:
                remove.add(pending.symbol)
                continue
            if self._opened_on(session_date) >= self.max_daily_new_positions:
                remove.add(pending.symbol)
                continue
            raw_open = float(row["open"])
            if raw_open <= 0:
                remove.add(pending.symbol)
                continue
            entry_exec = raw_open * (1 + self.slippage_bps / 10000.0)
            risk_cash = self.equity_sar * self.risk_pct / 100.0
            qty_by_risk = int(risk_cash / pending.risk_distance)
            qty_by_cap = int((self.equity_sar * self.max_position_pct / 100.0) / entry_exec)
            qty = max(0, min(qty_by_risk, qty_by_cap))
            if qty < 1:
                remove.add(pending.symbol)
                continue
            stop = max(0.01, entry_exec - pending.risk_distance)
            target = entry_exec + pending.reward_risk * pending.risk_distance
            position = PaperPosition(
                symbol=pending.symbol,
                qty=qty,
                entry=round(entry_exec, 4),
                stop=round(stop, 4),
                target=round(target, 4),
                score=pending.score,
                opened_on=session_date.isoformat(),
                strategy=pending.strategy,
                entry_regime=entry_regime,
            )
            self.positions[pending.symbol] = position
            opened.append(position)
            remove.add(pending.symbol)
        for symbol in remove:
            self.pending.pop(symbol, None)
        if opened or remove:
            self.save()
        return opened

    def mark_histories(self, histories: dict[str, pd.DataFrame], max_hold_days: int = 25) -> list[PaperTrade]:
        trades: list[PaperTrade] = []
        for symbol in list(self.positions):
            position = self.positions.get(symbol)
            history = histories.get(symbol)
            if position is None or history is None or history.empty:
                continue
            opened_on = date.fromisoformat(position.opened_on)
            last_marked = date.fromisoformat(position.last_marked_on) if position.last_marked_on else None
            risk_per_share = max(0.01, position.entry - position.stop)
            for index, row in history.sort_index().iterrows():
                bar_date = pd.Timestamp(index).date()
                if bar_date < opened_on:
                    continue
                if last_marked is not None and bar_date <= last_marked:
                    continue
                position.bars_held += 1
                low = float(row["low"])
                high = float(row["high"])
                close = float(row["close"])
                position.mfe_r = max(position.mfe_r, (high - position.entry) / risk_per_share)
                position.mae_r = min(position.mae_r, (low - position.entry) / risk_per_share)
                raw_exit: float | None = None
                reason = ""
                if low <= position.stop:
                    raw_exit, reason = position.stop, "stop"
                elif high >= position.target:
                    raw_exit, reason = position.target, "target"
                elif position.bars_held >= max_hold_days:
                    raw_exit, reason = close, "max_hold"
                if raw_exit is None:
                    position.last_marked_on = bar_date.isoformat()
                    last_marked = bar_date
                    continue
                exit_exec = raw_exit * (1 - self.slippage_bps / 10000.0)
                gross = (exit_exec - position.entry) * position.qty
                commission = (position.entry + exit_exec) * position.qty * self.commission_bps / 10000.0
                pnl = gross - commission
                ret = pnl / (position.entry * position.qty) * 100
                initial_risk_sar = risk_per_share * position.qty
                trade = PaperTrade(
                    symbol=position.symbol,
                    qty=position.qty,
                    entry=position.entry,
                    exit=round(exit_exec, 4),
                    opened_on=position.opened_on,
                    closed_on=bar_date.isoformat(),
                    reason=reason,
                    pnl_sar=round(pnl, 2),
                    return_pct=round(ret, 2),
                    strategy=position.strategy,
                    initial_risk_sar=round(initial_risk_sar, 2),
                    entry_regime=position.entry_regime,
                    mae_r=round(position.mae_r, 3),
                    mfe_r=round(position.mfe_r, 3),
                )
                self.closed.append(trade)
                self.equity_sar += pnl
                del self.positions[symbol]
                trades.append(trade)
                break
        self.save()
        return trades
