from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from saudi_trading_bot.disclosures.financials import EarningsSnapshot
from saudi_trading_bot.edge_lab import _pre_event_close, _published, _sessions_since


def diagnose_pead(
    base,
    row: pd.Series,
    history: pd.DataFrame,
    snapshot: EarningsSnapshot,
    cfg: dict,
) -> dict[str, object]:
    """Return auditable PEAD gate metrics without changing trading decisions."""
    reasons: list[str] = []
    published = _published(snapshot)
    sessions: int | None = None
    reaction_pct: float | None = None
    price = float(row["close"])
    ema50 = float(row["ema50"])
    avg_value20 = float(row["avg_value20"])
    technical_score = float(base.total_score)

    if published is None:
        reasons.append("published_at_missing")
    else:
        sessions = _sessions_since(history, published)
        min_sessions = int(cfg["min_sessions_after_announcement"])
        max_sessions = int(cfg["max_sessions_after_announcement"])
        if not min_sessions <= sessions <= max_sessions:
            reasons.append(
                f"sessions_outside_window:{sessions} not {min_sessions}-{max_sessions}"
            )

    min_earnings = float(cfg["min_earnings_score"])
    if snapshot.earnings_score < min_earnings:
        reasons.append(
            f"earnings_score_low:{snapshot.earnings_score:.1f}<{min_earnings:.1f}"
        )
    if snapshot.net_current is None:
        reasons.append("net_current_missing")
    elif snapshot.net_current <= 0:
        reasons.append(f"net_current_not_positive:{snapshot.net_current:.2f}")

    if price <= ema50:
        reasons.append(f"below_ema50:{price:.2f}<={ema50:.2f}")

    min_liquidity = float(cfg["min_avg_value_sar_20d"])
    if avg_value20 < min_liquidity:
        reasons.append(f"liquidity_low:{avg_value20:.0f}<{min_liquidity:.0f}")

    min_technical = float(cfg["min_technical_score"])
    if technical_score < min_technical:
        reasons.append(
            f"technical_score_low:{technical_score:.1f}<{min_technical:.1f}"
        )

    if published is not None:
        before = _pre_event_close(history, published)
        if before is None or before <= 0:
            reasons.append("pre_event_close_missing")
        else:
            reaction_pct = (price / before - 1.0) * 100.0
            min_reaction = float(cfg["min_post_event_return_pct"])
            max_reaction = float(cfg["max_post_event_return_pct"])
            if not min_reaction <= reaction_pct <= max_reaction:
                reasons.append(
                    f"post_event_return_outside:{reaction_pct:.2f}% not "
                    f"{min_reaction:.2f}%..{max_reaction:.2f}%"
                )

    snapshot_data = asdict(snapshot)
    return {
        "symbol": snapshot.symbol,
        "published_at": snapshot.published_at,
        "earnings_score": snapshot.earnings_score,
        "revenue_yoy_pct": snapshot.revenue_yoy_pct,
        "operating_yoy_pct": snapshot.operating_yoy_pct,
        "net_yoy_pct": snapshot.net_yoy_pct,
        "net_qoq_pct": snapshot.net_qoq_pct,
        "net_current": snapshot.net_current,
        "net_previous_year": snapshot.net_previous_year,
        "net_previous_quarter": snapshot.net_previous_quarter,
        "sessions_after_announcement": sessions,
        "technical_score": round(technical_score, 2),
        "price": round(price, 4),
        "ema50": round(ema50, 4),
        "avg_value20": round(avg_value20, 2),
        "post_event_return_pct": None if reaction_pct is None else round(reaction_pct, 3),
        "passed": not reasons,
        "reasons": reasons,
        "source_url": snapshot_data["url"],
    }


def format_diagnostic(payload: dict[str, object]) -> str:
    reasons = payload.get("reasons") or []
    reason_text = ";".join(str(item) for item in reasons) if reasons else "PASS"
    return (
        "PEAD_EVENT_DIAGNOSTIC "
        f"symbol={payload['symbol']} earnings={payload['earnings_score']} "
        f"rev_yoy={payload['revenue_yoy_pct']} op_yoy={payload['operating_yoy_pct']} "
        f"net_yoy={payload['net_yoy_pct']} net_qoq={payload['net_qoq_pct']} "
        f"net_current={payload['net_current']} sessions={payload['sessions_after_announcement']} "
        f"technical={payload['technical_score']} price={payload['price']} "
        f"ema50={payload['ema50']} avg_value20={payload['avg_value20']} "
        f"reaction={payload['post_event_return_pct']} result={reason_text}"
    )
