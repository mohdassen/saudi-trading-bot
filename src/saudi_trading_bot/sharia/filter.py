from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

RIYADH = ZoneInfo("Asia/Riyadh")


@dataclass(frozen=True)
class ShariaDecision:
    symbol: str
    allowed: bool
    status: str
    source: str
    source_period: str
    source_checked_at: date | None
    stale: bool
    reason: str


class StrictShariaFilter:
    """Fail-closed screening against a dated, externally published allowlist."""

    def __init__(
        self,
        csv_path: str | Path,
        max_source_check_age_days: int = 14,
        block_unknown: bool = True,
    ):
        self.csv_path = Path(csv_path)
        self.max_source_check_age_days = max_source_check_age_days
        self.block_unknown = block_unknown
        self.df = pd.read_csv(self.csv_path, dtype={"symbol": str})
        self.df["symbol"] = self.df["symbol"].str.strip()

    def check(self, symbol: str, today: date | None = None) -> ShariaDecision:
        today = today or datetime.now(RIYADH).date()
        rows = self.df[self.df["symbol"] == str(symbol)]
        if rows.empty:
            return ShariaDecision(
                str(symbol),
                not self.block_unknown,
                "unknown",
                "",
                "",
                None,
                False,
                "not in allowlist",
            )
        row = rows.iloc[-1]
        raw_checked = row.get("source_checked_at", row.get("as_of", ""))
        checked = pd.to_datetime(raw_checked, errors="coerce")
        checked_date = None if pd.isna(checked) else checked.date()
        stale = checked_date is None or (
            today - checked_date
        ).days > self.max_source_check_age_days
        status = str(row.get("status", "unknown")).lower().strip()
        allowed = status == "allowed" and not stale
        reason = (
            "allowed"
            if allowed
            else "stale source validation"
            if stale
            else f"status={status}"
        )
        return ShariaDecision(
            symbol=str(symbol),
            allowed=allowed,
            status=status,
            source=str(row.get("source", "")),
            source_period=str(row.get("source_period", "")),
            source_checked_at=checked_date,
            stale=stale,
            reason=reason,
        )
