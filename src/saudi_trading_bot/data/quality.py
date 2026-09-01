from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd


@dataclass(frozen=True)
class DataQualityResult:
    allowed: bool
    session_date: date | None
    expected_date: date
    eligible_symbols: int
    consensus_symbols: int
    note: str


def expected_completed_session(now: datetime) -> date:
    """Latest session that should have a completed Saudi daily candle.

    This deliberately knows only the regular Sun-Thu calendar. On an exchange
    holiday it may block entries for a day; fail-closed is the desired outcome.
    """
    candidate = now.date()
    if now.time() < time(15, 10):
        candidate -= timedelta(days=1)
    while candidate.weekday() in {4, 5}:  # Friday / Saturday
        candidate -= timedelta(days=1)
    return candidate


def validate_market_data(
    histories: dict[str, pd.DataFrame],
    now: datetime,
    min_symbols: int,
    min_consensus_pct: float,
) -> DataQualityResult:
    expected = expected_completed_session(now)
    latest_dates = [
        pd.Timestamp(history.index[-1]).date()
        for history in histories.values()
        if not history.empty
    ]
    if len(latest_dates) < min_symbols:
        return DataQualityResult(
            False, None, expected, len(latest_dates), 0,
            f"coverage {len(latest_dates)}/{min_symbols} below minimum",
        )

    counts = pd.Series(latest_dates).value_counts()
    session_date = counts.index[0]
    consensus = int(counts.iloc[0])
    consensus_pct = consensus / len(latest_dates) * 100.0
    if consensus_pct < min_consensus_pct:
        return DataQualityResult(
            False, session_date, expected, len(latest_dates), consensus,
            f"session consensus {consensus_pct:.1f}% below {min_consensus_pct:.1f}%",
        )
    if session_date != expected:
        return DataQualityResult(
            False, session_date, expected, len(latest_dates), consensus,
            f"stale session {session_date}; expected {expected}",
        )
    return DataQualityResult(
        True, session_date, expected, len(latest_dates), consensus,
        f"verified session {session_date}, consensus {consensus_pct:.1f}%",
    )
