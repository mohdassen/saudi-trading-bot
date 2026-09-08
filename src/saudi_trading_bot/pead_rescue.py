from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from saudi_trading_bot.config import load_settings
from saudi_trading_bot.data.cache import MarketDataCache
from saudi_trading_bot.disclosures.financials import SaudiFinancialResultReader
from saudi_trading_bot.disclosures.saudi_exchange import Announcement, SaudiExchangeDisclosures
from saudi_trading_bot.edge_lab import (
    _financial_title,
    _pead_candidate,
    _portfolio,
    _pre_event_close,
    _sessions_since,
)
from saudi_trading_bot.models import SignalState
from saudi_trading_bot.signals.engine import SignalEngine
from saudi_trading_bot.signals.strategies import latest_strategy_rows

RIYADH = ZoneInfo("Asia/Riyadh")
UTC = ZoneInfo("UTC")


def _event_time(item: Announcement) -> datetime | None:
    raw = item.published_at
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=RIYADH)


def _load_saved(path: Path) -> list[Announcement]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [Announcement(**item) for item in payload.get("events", [])]
    except (OSError, TypeError, ValueError):
        return []


def _save_events(path: Path, events: list[Announcement], now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "updated_at": now.isoformat(),
                "events": [asdict(item) for item in events],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _merge_events(
    previous: list[Announcement],
    fresh: list[Announcement],
    now: datetime,
    keep_days: int,
) -> list[Announcement]:
    merged: dict[tuple[str, str, str], Announcement] = {}
    for item in [*previous, *fresh]:
        key = (item.symbol, item.title, item.url)
        old = merged.get(key)
        if old is None:
            merged[key] = item
            continue
        # Prefer the copy that carries an actual exchange publication time.
        if item.published_at and not old.published_at:
            merged[key] = item

    cutoff = now - timedelta(days=keep_days)
    kept: list[Announcement] = []
    for item in merged.values():
        stamp = _event_time(item)
        if stamp is not None:
            if stamp.astimezone(RIYADH) >= cutoff:
                kept.append(item)
            continue
        # Never use an unknown-time event for a trade, but retain it briefly for diagnostics.
        try:
            fetched = datetime.fromisoformat(item.fetched_at)
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=UTC)
            if fetched.astimezone(RIYADH) >= cutoff:
                kept.append(item)
        except ValueError:
            pass
    return sorted(
        kept,
        key=lambda item: _event_time(item)
        or datetime.min.replace(tzinfo=RIYADH),
        reverse=True,
    )


def _refresh_official_events(
    urls: list[str],
    cache_file: Path,
    now: datetime,
    keep_days: int,
    timeout: int,
) -> tuple[list[Announcement], str, list[str]]:
    previous = _load_saved(cache_file)
    errors: list[str] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    fresh: list[Announcement] = []
    source = "cache"
    session = requests.Session()
    for url in urls:
        if not url:
            continue
        try:
            response = session.get(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            parsed = SaudiExchangeDisclosures.parse(
                response.text,
                base_url="https://www.saudiexchange.sa",
            )
            parsed = [
                item
                for item in parsed
                if item.symbol and _financial_title(item.title)
            ]
            if not parsed:
                raise ValueError("official page parsed zero financial-result events")
            fresh.extend(parsed)
            source = url
            break
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    events = _merge_events(previous, fresh, now, keep_days)
    _save_events(cache_file, events, now)
    return events, source, errors


def _histories_from_cache(cache: MarketDataCache, symbols: list[str]) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = cache.load(symbol)
        if len(frame) >= 220:
            result[symbol] = frame.sort_index()
    return result


def _price_confirmed_candidate(base, row, history, event: Announcement, cfg: dict):
    published = _event_time(event)
    if published is None:
        return None
    sessions = _sessions_since(history, published)
    if not int(cfg["min_sessions_after_announcement"]) <= sessions <= int(
        cfg["max_sessions_after_announcement"]
    ):
        return None

    price = float(row["close"])
    before = _pre_event_close(history, published)
    if before is None or before <= 0:
        return None
    reaction = price / before - 1.0
    min_reaction = float(cfg.get("fallback_min_post_event_return_pct", 1.0)) / 100.0
    max_reaction = float(cfg.get("fallback_max_post_event_return_pct", 12.0)) / 100.0
    if not min_reaction <= reaction <= max_reaction:
        return None

    min_score = float(cfg.get("fallback_min_technical_score", 68.0))
    min_volume = float(cfg.get("fallback_min_volume_ratio", 1.0))
    valid = (
        base.total_score >= min_score
        and price > float(row["ema50"])
        and float(row["roc20"]) > 0
        and float(row["vol_ratio"]) >= min_volume
        and float(row["avg_value20"]) >= float(cfg["min_avg_value_sar_20d"])
    )
    if not valid:
        return None

    risk_distance = max(0.01, float(base.atr) * float(cfg["atr_stop_multiple"]))
    stop = max(0.01, price - risk_distance)
    target = price + float(cfg["reward_risk"]) * risk_distance
    score = min(95.0, 60.0 + 0.40 * (base.total_score - 60.0) + reaction * 200.0)
    return replace(
        base,
        state=SignalState.READY,
        stop=round(stop, 2),
        target=round(target, 2),
        strategy="pead_price_confirmed_shadow",
        strategy_score=round(score, 2),
        rationale=base.rationale
        + (
            "Official financial-results event; detail table unavailable",
            f"price-confirmed PEAD sessions={sessions} return={reaction * 100:.1f}%",
            f"volume ratio={float(row['vol_ratio']):.2f}",
        ),
    )


def run() -> int:
    cfg = load_settings()
    edge = cfg.section("edge_lab")
    pead_cfg = edge["pead"]
    data_cfg = cfg.section("data")
    disc_cfg = cfg.section("disclosures")
    signal_cfg = cfg.section("signals")
    now = datetime.now(RIYADH)

    event_cache = cfg.path(edge.get("pead_event_cache_file", "data/pead_official_events.json"))
    urls = [
        edge.get("official_home_fallback_url", ""),
        disc_cfg.get("source_url", ""),
    ]
    events, source, errors = _refresh_official_events(
        urls,
        event_cache,
        now,
        int(pead_cfg["announcement_lookback_days"]),
        int(disc_cfg["timeout_seconds"]),
    )

    allowlist = pd.read_csv(cfg.path(cfg.section("sharia")["allowlist_file"]), dtype={"symbol": str})
    allowed = set(
        allowlist.loc[
            allowlist["status"].astype(str).str.lower().eq("allowed"),
            "symbol",
        ].astype(str)
    )
    symbols = sorted({item.symbol for item in events if item.symbol in allowed})
    histories = _histories_from_cache(
        MarketDataCache(cfg.path(data_cfg["cache_dir"])),
        symbols,
    )
    rows = latest_strategy_rows(histories)
    engine = SignalEngine(signal_cfg)
    reader = SaudiFinancialResultReader(
        cfg.path(edge["earnings_cache_file"]),
        timeout=int(disc_cfg["timeout_seconds"]),
    )
    portfolio = _portfolio(cfg, pead_cfg)

    detailed = 0
    price_fallback = 0
    candidates = []
    inspected = 0
    for event in events:
        if event.symbol not in allowed or event.symbol not in histories:
            continue
        published = _event_time(event)
        if published is None:
            continue
        if now - published.astimezone(RIYADH) > timedelta(
            days=int(pead_cfg["announcement_lookback_days"])
        ):
            continue
        row = rows.get(event.symbol)
        if row is None:
            continue
        history = histories[event.symbol]
        try:
            base = engine.score(event.symbol, history)
        except ValueError:
            continue
        inspected += 1

        snapshot = reader.read(event.symbol, event.url) if event.url else None
        candidate = None
        if snapshot is not None:
            detailed += 1
            candidate = _pead_candidate(base, row, history, snapshot, pead_cfg)
        if candidate is None:
            candidate = _price_confirmed_candidate(base, row, history, event, pead_cfg)
            if candidate is not None:
                price_fallback += 1
        if candidate is not None:
            candidates.append((candidate, pd.Timestamp(history.index[-1]).date()))

    queued = []
    for signal, signal_date in sorted(
        candidates,
        key=lambda item: item[0].strategy_score,
        reverse=True,
    ):
        if len(queued) >= int(pead_cfg["max_daily_new_positions"]):
            break
        item = portfolio.queue(
            signal,
            signal_bar_date=signal_date,
            reward_risk=float(pead_cfg["reward_risk"]),
        )
        if item is not None:
            queued.append(item)

    diagnostic = {
        "updated_at": now.isoformat(),
        "source": source,
        "source_errors": errors,
        "financial_events_retained": len(events),
        "eligible_symbols_with_events": len(symbols),
        "events_inspected": inspected,
        "official_detail_tables_parsed": detailed,
        "price_confirmed_candidates": price_fallback,
        "queued": [item.symbol for item in queued],
        "production_unchanged": "CASH",
    }
    health_path = cfg.path(edge.get("pead_source_health_file", "artifacts/pead_source_health.json"))
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        "PEAD_SOURCE "
        f"source={source} retained={len(events)} eligible={len(symbols)} "
        f"inspected={inspected} detailed={detailed} fallback={price_fallback} "
        f"queued={','.join(item.symbol for item in queued) or '-'}"
    )
    if errors:
        print("PEAD_SOURCE_ERRORS " + " | ".join(errors))
    print(f"PEAD_SOURCE_HEALTH {health_path}")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
