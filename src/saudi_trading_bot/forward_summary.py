from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from saudi_trading_bot.config import load_settings
from saudi_trading_bot.edge_lab import _performance, _portfolio


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _published_key(event: dict[str, Any]) -> datetime:
    raw = str(event.get("published_at", ""))
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min


def _queued_event_audit(
    health: dict[str, Any],
    event_cache: dict[str, Any],
) -> list[dict[str, str]]:
    direct = health.get("queued_events", [])
    if isinstance(direct, list) and direct:
        return [
            {
                "symbol": str(item.get("symbol", "")),
                "title": str(item.get("title", "")),
                "published_at": str(item.get("published_at", "")),
                "url": str(item.get("url", "")),
                "discovery_source": str(health.get("source", "")),
                "mode": str(item.get("mode", "")),
            }
            for item in direct
            if isinstance(item, dict)
        ]

    queued = {str(symbol) for symbol in health.get("queued", [])}
    if not queued:
        return []

    events = [
        item
        for item in event_cache.get("events", [])
        if isinstance(item, dict) and str(item.get("symbol", "")) in queued
    ]
    by_symbol: dict[str, dict[str, Any]] = {}
    for item in events:
        symbol = str(item.get("symbol", ""))
        current = by_symbol.get(symbol)
        if current is None or _published_key(item) > _published_key(current):
            by_symbol[symbol] = item

    audit = []
    for symbol in sorted(queued):
        event = by_symbol.get(symbol, {})
        audit.append(
            {
                "symbol": symbol,
                "title": str(event.get("title", "")),
                "published_at": str(event.get("published_at", "")),
                "url": str(event.get("url", "")),
                "discovery_source": str(health.get("source", "")),
                "mode": "",
            }
        )
    return audit


def build_summary() -> dict[str, Any]:
    cfg = load_settings()
    edge = cfg.section("edge_lab")
    qualification = edge["qualification"]
    target = int(edge["target_closed_trades"])

    pead_portfolio = _portfolio(cfg, edge["pead"])
    momentum_portfolio = _portfolio(cfg, edge["momentum"])
    pead = _performance("PEAD", pead_portfolio, target, qualification)
    momentum = _performance(
        "Momentum Leadership",
        momentum_portfolio,
        target,
        qualification,
    )

    health_path = cfg.path(
        edge.get("pead_source_health_file", "artifacts/pead_source_health.json")
    )
    events_path = cfg.path(
        edge.get("pead_verified_event_cache_file", "data/pead_verified_events.json")
    )
    health = _load_json(health_path)
    event_cache = _load_json(events_path)

    return {
        "production_strategy": "CASH",
        "qualification": {
            "target_closed_trades": target,
            "min_expectancy_r": float(qualification["min_expectancy_r"]),
            "min_profit_factor": float(qualification["min_profit_factor"]),
            "max_drawdown_r": float(qualification["max_drawdown_r"]),
        },
        "pead": asdict(pead),
        "momentum": asdict(momentum),
        "pead_source": {
            "verification": health.get("verification", ""),
            "source": health.get("source", ""),
            "verified_recent_financial_events": health.get(
                "verified_recent_financial_events", 0
            ),
            "eligible_symbols_with_events": health.get(
                "eligible_symbols_with_events", 0
            ),
            "events_inspected": health.get("events_inspected", 0),
            "financial_tables_parsed": health.get("financial_tables_parsed", 0),
            "fundamental_candidates": health.get("fundamental_candidates", 0),
            "price_only_candidates": health.get("price_only_candidates", 0),
            "purged_invalid_pending": health.get("purged_invalid_pending", []),
            "pending_after_reconcile": health.get("pending_after_reconcile", []),
            "queued_events": _queued_event_audit(health, event_cache),
        },
    }


def run() -> int:
    cfg = load_settings()
    edge = cfg.section("edge_lab")
    summary = build_summary()
    output = cfg.path(
        edge.get("forward_summary_file", "artifacts/forward_summary.json")
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pead = summary["pead"]
    momentum = summary["momentum"]
    source = summary["pead_source"]
    print(
        "FORWARD_SUMMARY "
        f"production={summary['production_strategy']} "
        f"PEAD closed={pead['closed_trades']}/{pead['target_trades']} "
        f"open={pead['open_positions']} pending={pead['pending']} "
        f"status={pead['status']} | "
        f"MOMENTUM closed={momentum['closed_trades']}/{momentum['target_trades']} "
        f"open={momentum['open_positions']} pending={momentum['pending']} "
        f"status={momentum['status']}"
    )
    print(
        "PEAD_PROVENANCE "
        f"verification={source['verification'] or '-'} "
        f"recent={source['verified_recent_financial_events']} "
        f"tables={source['financial_tables_parsed']} "
        f"fundamental={source['fundamental_candidates']} "
        f"price_only={source['price_only_candidates']} "
        f"purged={','.join(source['purged_invalid_pending']) or '-'}"
    )
    for event in source["queued_events"]:
        print(
            "PEAD_AUDIT "
            f"symbol={event['symbol']} mode={event.get('mode', '') or '-'} "
            f"published={event['published_at']} "
            f"title={event['title']} url={event['url']}"
        )
    print(f"FORWARD_SUMMARY_FILE {output}")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
