from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.models import DisclosureImpact, Signal, SignalState

from .indicators import enrich


def _clip(v: float) -> float:
    return float(max(0.0, min(100.0, v)))


class SignalEngine:
    def __init__(self, settings: dict):
        self.s = settings

    def score(self, symbol: str, history: pd.DataFrame, disclosure: DisclosureImpact | None = None) -> Signal:
        if len(history) < int(self.s["min_history_rows"]):
            raise ValueError(f"{symbol}: insufficient history ({len(history)})")
        df = enrich(history)
        row = df.iloc[-1]
        price = float(row["close"])
        atr = float(row["atr14"])

        trend = 0.0
        trend += 40 if price > row["ema50"] else 0
        trend += 35 if row["ema50"] > row["ema200"] else 0
        trend += 25 if row["ema20"] > row["ema50"] else 0

        momentum = 0.0
        rsi = float(row["rsi14"])
        roc20 = float(row["roc20"])
        vol_ratio = float(row["vol_ratio"]) if not pd.isna(row["vol_ratio"]) else 0.0
        momentum += _clip((roc20 + 5) * 4.0) * 0.45
        momentum += (100.0 if 52 <= rsi <= 72 else 55.0 if 45 <= rsi < 52 or 72 < rsi <= 78 else 20.0) * 0.35
        momentum += _clip(vol_ratio * 50.0) * 0.20

        high20 = float(row["high20"])
        high55 = float(row["high55"])
        dist20 = (high20 - price) / price if price else 1.0
        dist55 = (high55 - price) / price if price else 1.0
        swing = 0.0
        swing += 55 if 0 <= dist20 <= 0.025 else 25 if dist20 <= 0.06 else 5
        swing += 30 if 0 <= dist55 <= 0.04 else 10
        swing += 15 if price > row["ema20"] else 0

        disclosure_score = disclosure.score if disclosure else 50.0
        w = self.s["weights"]
        total = (
            trend * float(w["trend"])
            + momentum * float(w["momentum"])
            + swing * float(w["swing"])
            + disclosure_score * float(w["disclosure"])
        )

        liquid = float(row["avg_value20"]) >= float(self.s["min_avg_value_sar_20d"])
        above_min_price = price >= float(self.s["min_price_sar"])
        rules = self.s.get("entry_rules", {})
        entry_quality = (
            (not rules.get("require_ema_stack", True)
             or price > row["ema20"] > row["ema50"] > row["ema200"])
            and float(rules.get("rsi_min", 52))
            <= rsi
            <= float(rules.get("rsi_max", 68))
            and float(rules.get("momentum_20d_min_pct", 2))
            <= roc20
            <= float(rules.get("momentum_20d_max_pct", 22))
            and vol_ratio >= float(rules.get("volume_ratio_min", 1.05))
            and price >= high20 * float(rules.get("near_high20_ratio", 0.98))
        )
        if not liquid or not above_min_price:
            state = SignalState.IGNORE
        elif total >= float(self.s["score_threshold_entry"]) and entry_quality:
            state = SignalState.READY
        elif total >= float(self.s["score_threshold_watch"]):
            state = SignalState.WATCH
        else:
            state = SignalState.IGNORE

        stop = max(0.01, price - 1.8 * atr)
        target = price + (price - stop) * 2.2
        rationale = []
        if price > row["ema50"] > row["ema200"]:
            rationale.append("اتجاه متوسط/طويل إيجابي")
        if roc20 > 0:
            rationale.append(f"زخم 20 يوم +{roc20:.1f}%")
        if 52 <= rsi <= 72:
            rationale.append(f"RSI صحي {rsi:.0f}")
        if vol_ratio >= 1.2:
            rationale.append(f"حجم تداول {vol_ratio:.1f}× المتوسط")
        if disclosure and disclosure.label != "neutral":
            rationale.append(f"إفصاح {disclosure.label}: {disclosure.title[:70]}")
        if total >= float(self.s["score_threshold_entry"]) and not entry_quality:
            rationale.append("شروط جودة الدخول V2 غير مكتملة")

        return Signal(
            symbol=symbol,
            state=state,
            total_score=round(float(total), 2),
            trend_score=round(float(trend), 2),
            momentum_score=round(float(momentum), 2),
            swing_score=round(float(swing), 2),
            disclosure_score=round(float(disclosure_score), 2),
            price=round(price, 2),
            stop=round(stop, 2),
            target=round(target, 2),
            atr=round(atr, 3),
            rationale=tuple(rationale),
            generated_at=datetime.now(ZoneInfo("Asia/Riyadh")),
        )
