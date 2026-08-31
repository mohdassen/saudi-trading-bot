from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MarketBreadthResult:
    allowed: bool
    state: str
    note: str
    eligible_symbols: int
    pct_above_ema50: float | None = None
    pct_above_ema200: float | None = None
    median_mom20_pct: float | None = None


class SaudiMarketBreadth:
    """Classify the Saudi Sharia universe using robust market breadth.

    RISK_ON requires broad confirmation across short/long trend and momentum.
    RECOVERY allows selective Paper entries when short-term breadth is strong,
    momentum is positive, and long-term breadth is recovering from a lower base.
    RISK_OFF blocks new entries. Too little data always fails closed.
    """

    def __init__(
        self,
        min_eligible_symbols: int = 150,
        min_history_rows: int = 220,
        risk_on_min_pct_above_ema50: float = 50.0,
        risk_on_min_pct_above_ema200: float = 45.0,
        risk_on_min_median_mom20_pct: float = 0.0,
        recovery_min_pct_above_ema50: float = 55.0,
        recovery_min_pct_above_ema200: float = 30.0,
        recovery_min_median_mom20_pct: float = 1.0,
    ) -> None:
        self.min_eligible_symbols = min_eligible_symbols
        self.min_history_rows = min_history_rows
        self.risk_on_min_pct_above_ema50 = risk_on_min_pct_above_ema50
        self.risk_on_min_pct_above_ema200 = risk_on_min_pct_above_ema200
        self.risk_on_min_median_mom20_pct = risk_on_min_median_mom20_pct
        self.recovery_min_pct_above_ema50 = recovery_min_pct_above_ema50
        self.recovery_min_pct_above_ema200 = recovery_min_pct_above_ema200
        self.recovery_min_median_mom20_pct = recovery_min_median_mom20_pct

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
                "RISK_OFF",
                (
                    "Saudi breadth insufficient: "
                    f"eligible={eligible} required={self.min_eligible_symbols}"
                ),
                eligible,
            )

        pct50 = sum(above_50) / eligible * 100.0
        pct200 = sum(above_200) / eligible * 100.0
        median20 = float(pd.Series(momentum_20).median())

        risk_on = (
            pct50 >= self.risk_on_min_pct_above_ema50
            and pct200 >= self.risk_on_min_pct_above_ema200
            and median20 >= self.risk_on_min_median_mom20_pct
        )
        recovery = (
            pct50 >= self.recovery_min_pct_above_ema50
            and pct200 >= self.recovery_min_pct_above_ema200
            and median20 >= self.recovery_min_median_mom20_pct
        )

        if risk_on:
            state = "RISK_ON"
            allowed = True
        elif recovery:
            state = "RECOVERY"
            allowed = True
        else:
            state = "RISK_OFF"
            allowed = False

        note = (
            f"Saudi breadth {state}: eligible={eligible} "
            f"aboveEMA50={pct50:.1f}% aboveEMA200={pct200:.1f}% "
            f"median20d={median20:+.1f}%"
        )
        return MarketBreadthResult(
            allowed,
            state,
            note,
            eligible,
            round(pct50, 2),
            round(pct200, 2),
            round(median20, 2),
        )
