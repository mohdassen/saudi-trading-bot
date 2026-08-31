from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class MarketDataProvider(ABC):
    @abstractmethod
    def history(self, symbol: str, start: date, end: date, interval: str = "1d") -> pd.DataFrame:
        raise NotImplementedError
