from __future__ import annotations

import os

import requests

from saudi_trading_bot.models import Signal


def format_signal(
    signal: Signal,
    sharia_source: str = "",
    sharia_period: str = "",
) -> str:
    icon = (
        "🟢"
        if signal.state.value == "READY"
        else "🟡"
        if signal.state.value == "WATCH"
        else "⚪️"
    )
    reasons = "\n".join(f"• {r}" for r in signal.rationale) or "• لا يوجد سبب قوي كافٍ"
    sharia = sharia_source + (f" ({sharia_period})" if sharia_period else "")
    strategy = f"الاستراتيجية: {signal.strategy}"
    if signal.strategy != "CASH":
        strategy += f" | تقييمها: {signal.strategy_score:.1f}"
    return (
        f"{icon} Saudi Trading Bot — {signal.state.value}\n"
        f"السهم: {signal.symbol}\n"
        f"{strategy}\n"
        f"السعر: {signal.price:.2f} ر.س\n"
        f"التقييم: {signal.total_score:.1f}/100\n"
        f"Trend {signal.trend_score:.0f} | Momentum {signal.momentum_score:.0f} | "
        f"Swing {signal.swing_score:.0f}\n"
        f"وقف Paper: {signal.stop:.2f} | هدف Paper: {signal.target:.2f}\n"
        f"الأسباب:\n{reasons}\n"
        f"الفلتر الشرعي: PASS — {sharia}\n\n"
        "Paper/Shadow فقط — لا توجد أوامر تداول حقيقية."
    )


def send_telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not configured")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()
