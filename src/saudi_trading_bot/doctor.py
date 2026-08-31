from __future__ import annotations

from datetime import date
import os

from saudi_trading_bot.config import Settings
from saudi_trading_bot.sharia.filter import StrictShariaFilter


def run_doctor(cfg: Settings) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    data = cfg.section("data")
    checks.append(
        (
            "zero_paid_services",
            bool(data.get("free_only", False)),
            "free_only=true required",
        )
    )
    checks.append(
        (
            "market_provider",
            data.get("provider") == "yfinance",
            f"provider={data.get('provider')}",
        )
    )
    sh = cfg.section("sharia")
    flt = StrictShariaFilter(
        cfg.path(sh["allowlist_file"]),
        sh["max_source_check_age_days"],
        sh["block_unknown"],
    )
    allowed = 0
    stale = 0
    for symbol in flt.df["symbol"].astype(str):
        d = flt.check(symbol, today=date.today())
        allowed += int(d.allowed)
        stale += int(d.stale)
    checks.append(
        (
            "sharia_allowlist",
            allowed > 0 and stale == 0,
            f"allowed={allowed} stale={stale}",
        )
    )
    telegram_set = bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(
        os.getenv("TELEGRAM_CHAT_ID")
    )
    checks.append(
        (
            "telegram",
            telegram_set,
            "configured" if telegram_set else "not configured (scan still works without --send)",
        )
    )
    return checks
