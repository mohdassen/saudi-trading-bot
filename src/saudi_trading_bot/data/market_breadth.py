from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MarketBreadthResult:
    allowed: bool
    note: str
    eligible_symbols: int
    pct_above_ema50: float | None = None
    pct_above_ema200: float | None = None
    median_mom20_pct: float | None = None


class SaudiMarketBreadth:
    """Market regime derived from the same free Saudi stock data used by the bot.

    This deliberately avoids a hard dependency on a TASI website/API because
    public index pages commonly block cloud/datacenter IPs. The gate fails
    closed when too few stocks have enough history.
    """

    def __init__(
        self,
        min_eligible_symbols: int = 150,
        min_history_rows: int = 220,
        min_pct_above_ema50: float = 45.0,
        min_pct_above_ema200: float = 40.0,
        min_median_mom20_pct: float = -1.5,
    ) -> None:
        self.min_eligible_symbols = min_eligible_symbols
        self.min_history_rows = min_history_rows
        self.min_pct_above_ema50 = min_pct_above_ema50
        self.min_pct_above_ema200 = min_pct_above_ema200
        self.min_median_mom20_pct = min_median_mom20_pct

    def evaluate(self, histories: dict[str, pd.DataFrame]) -> MarketBreadthResult:
        above_50: list[bool] = []
        above_200: list[bool] = []
        momentum_20: list[float] = []

        for history in histories.values():
            if history.empty or len(history) < self.min_history_rows:
                continue
            if "close" not in history.columns:
                continue

            close = pd.to_numeric(history["close"], errors="coerce").dropna()
            if len(close) < self.min_history_rows or len(close) < 21:
                continue

            last = float(close.iloc[-1])
            if last <= 0:
                continue

            ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
            ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
            mom20_pct = (last / float(close.iloc[-21]) - 1.0) * 100.0

            above_50.append(last >= ema50)
            above_200.append(last >= ema200)
            momentum_20.append(mom20_pct)

        eligible = len(momentum_20)
        if eligible < self.min_eligible_symbols:
            return MarketBreadthResult(
                False,
                (
                    "Saudi breadth insufficient: "
                    f"eligible={eligible} required={self.min_eligible_symbols}"
                ),
                eligible,
            )

        pct50 = sum(above_50) / eligible * 100.0
        pct200 = sum(above_200) / eligible * 100.0
        median20 = float(pd.Series(momentum_20).median())

        checks = {
            "aboveEMA50": pct50 >= self.min_pct_above_ema50,
            "aboveEMA200": pct200 >= self.min_pct_above_ema200,
            "median20d": median20 >= self.min_median_mom20_pct,
        }
        allowed = all(checks.values())
        failed = ",".join(name for name, passed in checks.items() if not passed)
        state = "PASS" if allowed else f"BLOCK({failed})"
        note = (
            f"Saudi breadth {state}: eligible={eligible} "
            f"aboveEMA50={pct50:.1f}% aboveEMA200={pct200:.1f}% "
            f"median20d={median20:+.1f}%"
        )
        return MarketBreadthResult(
            allowed,
            note,
            eligible,
            round(pct50, 2),
            round(pct200, 2),
            round(median20, 2),
        )
