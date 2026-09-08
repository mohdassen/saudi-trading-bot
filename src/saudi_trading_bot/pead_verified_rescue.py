from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup

from saudi_trading_bot.config import load_settings
from saudi_trading_bot.data.cache import MarketDataCache
from saudi_trading_bot.disclosures.financials import (
    EarningsSnapshot,
    parse_financial_result,
)
from saudi_trading_bot.disclosures.saudi_exchange import Announcement
from saudi_trading_bot.edge_lab import (
    _pead_candidate,
    _portfolio,
    _sessions_since,
)
from saudi_trading_bot.pead_rescue import (
    _allowed_companies,
    _histories_from_cache,
    _parse_mubasher_financial_events,
    _price_confirmed_candidate,
)
from saudi_trading_bot.signals.engine import SignalEngine
from saudi_trading_bot.signals.strategies import latest_strategy_rows

RIYADH = ZoneInfo("Asia/Riyadh")
UTC = ZoneInfo("UTC")
MUBASHER_ANNOUNCEMENTS = "https://english.mubasher.info/news/sa/now/announcements"

_DETAIL_DATE = re.compile(
    r"\b(\d{1,2}\s+"
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+20\d{2}\s+\d{1,2}:\d{2}\s+[AP]M)\b",
    re.IGNORECASE,
)


def _detail_published_at(html: str) -> datetime | None:
    text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
    match = _DETAIL_DATE.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(
            match.group(1).title(),
            "%d %B %Y %I:%M %p",
        ).replace(tzinfo=RIYADH)
    except ValueError:
        return None


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }


def _verify_one(
    event: Announcement,
    timeout: int,
) -> tuple[Announcement | None, EarningsSnapshot | None, str | None]:
    try:
        response = requests.get(
            event.url,
            timeout=timeout,
            headers=_headers(),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return None, None, f"{event.symbol}: detail HTTP error: {exc}"

    published = _detail_published_at(response.text)
    if published is None:
        return None, None, f"{event.symbol}: exact detail timestamp not found"

    verified = replace(event, published_at=published.isoformat())
    snapshot = parse_financial_result(response.text, event.symbol, event.url)
    if snapshot is not None:
        snapshot = replace(snapshot, published_at=published.isoformat())
    return verified, snapshot, None


def _verified_mubasher_events(
    listing_html: str,
    now: datetime,
    companies: list[tuple[str, str]],
    timeout: int,
) -> tuple[list[Announcement], dict[str, EarningsSnapshot], list[str]]:
    discovered = _parse_mubasher_financial_events(listing_html, now, companies)
    verified: list[Announcement] = []
    snapshots: dict[str, EarningsSnapshot] = {}
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_verify_one, event, timeout): event for event in discovered[:60]
        }
        for future in as_completed(futures):
            event, snapshot, error = future.result()
            if error:
                errors.append(error)
                continue
            if event is None:
                continue
            verified.append(event)
            if snapshot is not None:
                snapshots[event.url] = snapshot

    verified.sort(
        key=lambda item: datetime.fromisoformat(item.published_at),
        reverse=True,
    )
    return verified, snapshots, errors


def _save_verified_events(
    path: Path,
    events: list[Announcement],
    now: datetime,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "verification": "detail-page-v1",
                "updated_at": now.astimezone(UTC).isoformat(),
                "events": [asdict(event) for event in events],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _load_verified_events(path: Path) -> list[Announcement]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if payload.get("verification") != "detail-page-v1":
        return []
    events = []
    for item in payload.get("events", []):
        try:
            events.append(Announcement(**item))
        except TypeError:
            continue
    return events


def _fetch_verified_events(
    url: str,
    cache_path: Path,
    now: datetime,
    companies: list[tuple[str, str]],
    timeout: int,
) -> tuple[list[Announcement], dict[str, EarningsSnapshot], list[str], str]:
    try:
        response = requests.get(url, timeout=timeout, headers=_headers())
        response.raise_for_status()
        events, snapshots, errors = _verified_mubasher_events(
            response.text,
            now,
            companies,
            timeout,
        )
        if not events:
            raise ValueError("no detail-page-verified financial events")
        _save_verified_events(cache_path, events, now)
        return events, snapshots, errors, url
    except (requests.RequestException, ValueError) as exc:
        cached = _load_verified_events(cache_path)
        return cached, {}, [f"verified listing refresh failed: {exc}"], "verified-cache"


def _recent_event(
    event: Announcement,
    now: datetime,
    lookback_days: int,
) -> bool:
    try:
        published = datetime.fromisoformat(event.published_at)
    except ValueError:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=RIYADH)
    age = now - published.astimezone(RIYADH)
    return timedelta(0) <= age <= timedelta(days=lookback_days)


def _purge_invalid_pending(
    portfolio,
    histories: dict[str, pd.DataFrame],
    events: list[Announcement],
    now: datetime,
    cfg: dict,
) -> list[str]:
    valid_symbols: set[str] = set()
    for event in events:
        history = histories.get(event.symbol)
        if history is None or not _recent_event(
            event,
            now,
            int(cfg["announcement_lookback_days"]),
        ):
            continue
        try:
            published = datetime.fromisoformat(event.published_at)
        except ValueError:
            continue
        sessions = _sessions_since(history, published)
        if sessions <= int(cfg["max_sessions_after_announcement"]):
            valid_symbols.add(event.symbol)

    removed = []
    for symbol, pending in list(portfolio.pending.items()):
        if not str(pending.strategy).startswith("pead"):
            continue
        if symbol in valid_symbols:
            continue
        portfolio.pending.pop(symbol, None)
        removed.append(symbol)
    if removed:
        portfolio.save()
    return removed


def run() -> int:
    cfg = load_settings()
    edge = cfg.section("edge_lab")
    pead_cfg = edge["pead"]
    data_cfg = cfg.section("data")
    disc_cfg = cfg.section("disclosures")
    signal_cfg = cfg.section("signals")
    now = datetime.now(RIYADH)

    allowlist = pd.read_csv(
        cfg.path(cfg.section("sharia")["allowlist_file"]),
        dtype={"symbol": str},
    )
    allowed = set(
        allowlist.loc[
            allowlist["status"].astype(str).str.lower().eq("allowed"),
            "symbol",
        ].astype(str)
    )
    companies = _allowed_companies(allowlist)
    cache_path = cfg.path(
        edge.get("pead_verified_event_cache_file", "data/pead_verified_events.json")
    )

    events, snapshots, errors, source = _fetch_verified_events(
        str(disc_cfg.get("fallback_url", MUBASHER_ANNOUNCEMENTS)),
        cache_path,
        now,
        companies,
        int(disc_cfg["timeout_seconds"]),
    )
    events = [
        event
        for event in events
        if event.symbol in allowed
        and _recent_event(
            event,
            now,
            int(pead_cfg["announcement_lookback_days"]),
        )
    ]

    symbols = sorted({event.symbol for event in events})
    histories = _histories_from_cache(
        MarketDataCache(cfg.path(data_cfg["cache_dir"])),
        symbols,
    )
    rows = latest_strategy_rows(histories)
    engine = SignalEngine(signal_cfg)
    portfolio = _portfolio(cfg, pead_cfg)

    purged = _purge_invalid_pending(portfolio, histories, events, now, pead_cfg)
    candidates: list[tuple[object, object, Announcement, str]] = []
    inspected = 0
    detailed_parsed = 0
    detailed_passed = 0
    fallback_passed = 0

    for event in events:
        history = histories.get(event.symbol)
        row = rows.get(event.symbol)
        if history is None or row is None:
            continue
        try:
            base = engine.score(event.symbol, history)
        except ValueError:
            continue
        inspected += 1

        snapshot = snapshots.get(event.url)
        candidate = None
        mode = ""
        if snapshot is not None:
            detailed_parsed += 1
            candidate = _pead_candidate(base, row, history, snapshot, pead_cfg)
            if candidate is not None:
                detailed_passed += 1
                mode = "fundamental+price"
        else:
            candidate = _price_confirmed_candidate(
                base,
                row,
                history,
                event,
                pead_cfg,
            )
            if candidate is not None:
                fallback_passed += 1
                mode = "price-confirmed-no-table"

        if candidate is not None:
            candidates.append(
                (
                    candidate,
                    pd.Timestamp(history.index[-1]).date(),
                    event,
                    mode,
                )
            )

    queued = []
    queued_audit = []
    for signal, signal_date, event, mode in sorted(
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
        if item is None:
            continue
        queued.append(item)
        queued_audit.append(
            {
                "symbol": item.symbol,
                "strategy": item.strategy,
                "score": item.score,
                "mode": mode,
                "title": event.title,
                "published_at": event.published_at,
                "url": event.url,
            }
        )

    health = {
        "verification": "detail-page-v1",
        "updated_at": now.isoformat(),
        "source": source,
        "source_errors": errors,
        "verified_recent_financial_events": len(events),
        "eligible_symbols_with_events": len(symbols),
        "events_inspected": inspected,
        "financial_tables_parsed": detailed_parsed,
        "fundamental_candidates": detailed_passed,
        "price_only_candidates": fallback_passed,
        "purged_invalid_pending": purged,
        "queued": [item.symbol for item in queued],
        "queued_events": queued_audit,
        "pending_after_reconcile": sorted(portfolio.pending),
        "production_unchanged": "CASH",
    }
    health_path = cfg.path(
        edge.get("pead_source_health_file", "artifacts/pead_source_health.json")
    )
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(
        json.dumps(health, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        "PEAD_VERIFIED "
        f"source={source} recent={len(events)} inspected={inspected} "
        f"tables={detailed_parsed} fundamental={detailed_passed} "
        f"fallback={fallback_passed} purged={','.join(purged) or '-'} "
        f"queued={','.join(item.symbol for item in queued) or '-'} "
        f"pending={','.join(sorted(portfolio.pending)) or '-'}"
    )
    for item in queued_audit:
        print(
            "PEAD_VERIFIED_AUDIT "
            f"symbol={item['symbol']} mode={item['mode']} "
            f"published={item['published_at']} title={item['title']} "
            f"url={item['url']}"
        )
    if errors:
        print("PEAD_VERIFICATION_ERRORS " + " | ".join(errors[:10]))
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
