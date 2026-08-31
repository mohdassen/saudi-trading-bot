from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd


class MarketDataCache:
    """Simple CSV cache so a temporary free-provider outage does not stop the bot."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, symbol: str) -> Path:
        safe = symbol.replace("^", "INDEX_").replace("/", "_")
        return self.root / f"{safe}.csv"

    def load(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> pd.DataFrame:
        path = self.path_for(symbol)
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if start is not None:
            df = df[df.index.date >= start]
        if end is not None:
            df = df[df.index.date < end]
        return df

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        path = self.path_for(symbol)
        merged = df.copy()
        if path.exists():
            old = pd.read_csv(path, index_col=0, parse_dates=True)
            merged = pd.concat([old, merged]).sort_index()
            merged = merged[~merged.index.duplicated(keep="last")]
        merged.to_csv(path)

    def age_days(self, symbol: str, today: date | None = None) -> int | None:
        path = self.path_for(symbol)
        if not path.exists():
            return None
        today = today or datetime.now(UTC).date()
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date()
        return (today - modified).days
