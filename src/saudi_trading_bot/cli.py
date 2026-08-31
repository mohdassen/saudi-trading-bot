from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from saudi_trading_bot.backtest.core import run_symbol_backtest
from saudi_trading_bot.config import load_settings
from saudi_trading_bot.data.cache import MarketDataCache
from saudi_trading_bot.data.resilient import ResilientFreeProvider
from saudi_trading_bot.data.tasi import TasiRegimeVerifier
from saudi_trading_bot.disclosures.saudi_exchange import SaudiExchangeDisclosures
from saudi_trading_bot.doctor import run_doctor
from saudi_trading_bot.models import SignalState
from saudi_trading_bot.notify.state import AlertState
from saudi_trading_bot.notify.telegram import format_signal, send_telegram
from saudi_trading_bot.paper.portfolio import PaperPortfolio
from saudi_trading_bot.sharia.alrajhi import sync_allowlist
from saudi_trading_bot.sharia.filter import StrictShariaFilter
from saudi_trading_bot.signals.engine import SignalEngine

RIYADH = ZoneInfo("Asia/Riyadh")


def _universe(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"symbol": str})
    return df[df["enabled"].astype(str).str.lower().eq("true")]


def _provider(cfg):
    s_data = cfg.section("data")
    if not bool(s_data.get("free_only", True)):
        raise RuntimeError("This project enforces free_only=true")
    if s_data.get("provider") != "yfinance":
        raise RuntimeError("Only the approved free yfinance provider is enabled")
    from saudi_trading_bot.data.yahoo import YahooSaudiProvider

    primary = YahooSaudiProvider(suffix=s_data.get("suffix", ".SR"))
    return ResilientFreeProvider(
        primary,
        MarketDataCache(cfg.path(s_data["cache_dir"])),
    )


def _market_regime_ok(cfg) -> tuple[bool, str]:
    s_risk = cfg.section("risk")
    if not bool(s_risk.get("no_trade_if_tasi_below_market_ma", True)):
        return True, "gate disabled"

    s_tasi = cfg.section("tasi")
    verifier = TasiRegimeVerifier(
        primary_url=s_tasi["primary_url"],
        reference_url=s_tasi["reference_url"],
        timeout_seconds=int(s_tasi["timeout_seconds"]),
        max_reference_gap_pct=float(s_tasi["max_reference_gap_pct"]),
    )
    result = verifier.evaluate()
    return result.allowed, result.note


def scan(send: bool = False) -> int:
    load_dotenv()
    cfg = load_settings()
    s_market, s_data = cfg.section("market"), cfg.section("data")
    s_sharia = cfg.section("sharia")
    s_signals = cfg.section("signals")
    s_risk = cfg.section("risk")
    s_paper = cfg.section("paper")
    s_disc = cfg.section("disclosures")
    s_notify = cfg.section("notifications")

    provider = _provider(cfg)
    sharia = StrictShariaFilter(
        cfg.path(s_sharia["allowlist_file"]),
        s_sharia["max_source_check_age_days"],
        s_sharia["block_unknown"],
    )
    engine = SignalEngine(s_signals)
    portfolio = PaperPortfolio(
        cfg.path(s_paper["portfolio_file"]),
        s_risk["paper_equity_sar"],
        s_risk["risk_per_trade_pct"],
        s_risk["max_position_pct"],
        s_risk["max_open_positions"],
        s_risk["max_daily_new_positions"],
        s_paper["commission_bps"],
        s_paper["slippage_bps"],
    )
    alert_state = AlertState(cfg.path(s_notify["state_file"]))
    disclosures = SaudiExchangeDisclosures(
        s_disc["source_url"],
        cfg.path(s_disc["cache_file"]),
        s_disc["timeout_seconds"],
        s_disc["lookback_days"],
        s_disc.get("fallback_url", ""),
    )
    announcements = disclosures.refresh() if s_disc.get("enabled", True) else []

    end = datetime.now(RIYADH).date() + timedelta(days=1)
    start = end - timedelta(days=int(s_data["lookback_days"]) * 2)
    regime_ok, regime_note = _market_regime_ok(cfg)
    print(f"MARKET_REGIME {'PASS' if regime_ok else 'BLOCK'}: {regime_note}")
    if disclosures.last_error:
        print(f"DISCLOSURES_FALLBACK: {disclosures.last_error}")

    rows = []
    blocked_sharia = 0
    no_data = 0
    for _, universe_row in _universe(cfg.path(s_market["symbols_file"])).iterrows():
        symbol = str(universe_row["symbol"])
        decision = sharia.check(symbol)
        if not decision.allowed:
            blocked_sharia += 1
            print(f"BLOCKED {symbol}: Sharia {decision.reason}")
            continue

        hist = provider.history(symbol, start, end, s_data["interval"])
        if hist.empty:
            no_data += 1
            print(f"NO_DATA {symbol}: {provider.last_error}")
            continue

        last = hist.iloc[-1]
        closed = portfolio.mark_daily_bar(
            symbol,
            float(last["low"]),
            float(last["high"]),
            float(last["close"]),
            max_hold_days=s_paper["max_hold_days"],
        )
        if closed:
            print(
                f"PAPER_EXIT {closed.symbol} {closed.reason} "
                f"PnL={closed.pnl_sar:.2f} SAR"
            )

        try:
            impact = disclosures.impact_for(
                symbol, announcements, str(universe_row.get("name_en", ""))
            )
            signal = engine.score(symbol, hist, impact)
        except ValueError as exc:
            print(exc)
            continue

        if not regime_ok and signal.state.value == "READY":
            signal = replace(
                signal,
                state=SignalState.WATCH,
                rationale=signal.rationale
                + ("TASI regime gate يمنع دخول Paper جديد",),
            )

        rows.append(
            {
                "symbol": symbol,
                "state": signal.state.value,
                "score": signal.total_score,
                "price": signal.price,
                "data_source": provider.last_source,
            }
        )
        print(format_signal(signal, decision.source, decision.source_period))
        if regime_ok:
            portfolio.consider(signal)

        changed = alert_state.changed(signal)
        should_send = send and signal.state.value in {"READY", "WATCH"}
        if should_send and s_notify.get("send_only_state_changes", True):
            should_send = changed
        if should_send:
            send_telegram(
                format_signal(signal, decision.source, decision.source_period)
            )

    out = cfg.root / "artifacts/latest_scan.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    columns = ["symbol", "state", "score", "price", "data_source"]
    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame = frame.sort_values("score", ascending=False)
    frame.to_csv(out, index=False)
    print(
        f"SUMMARY scanned={len(rows)} sharia_blocked={blocked_sharia} "
        f"no_data={no_data} open_paper={len(portfolio.positions)} "
        f"equity={portfolio.equity_sar:.2f}"
    )
    print(f"Saved {out}")
    return 0


def backtest() -> int:
    cfg = load_settings()
    s_market = cfg.section("market")
    s_paper = cfg.section("paper")
    s_sharia = cfg.section("sharia")
    provider = _provider(cfg)
    sharia = StrictShariaFilter(
        cfg.path(s_sharia["allowlist_file"]),
        99999,
        s_sharia["block_unknown"],
    )
    end = datetime.now(RIYADH).date() + timedelta(days=1)
    start = end - timedelta(days=365 * 5)
    results = []
    for _, universe_row in _universe(cfg.path(s_market["symbols_file"])).iterrows():
        symbol = str(universe_row["symbol"])
        if sharia.check(symbol).status != "allowed":
            continue
        hist = provider.history(symbol, start, end, "1d")
        if hist.empty:
            continue
        result = run_symbol_backtest(
            symbol,
            hist,
            s_paper["commission_bps"],
            s_paper["slippage_bps"],
            s_paper["max_hold_days"],
        )
        results.append(result.__dict__)
    out = cfg.root / "artifacts/backtest.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(results)
    frame.to_csv(out, index=False)
    print(
        frame.to_string(index=False)
        if not frame.empty
        else "No backtest data available"
    )
    print(f"Saved {out}")
    return 0


def sync_sharia(best_effort: bool = False) -> int:
    cfg = load_settings()
    settings = cfg.section("sharia")
    try:
        allow_path = cfg.path(settings["allowlist_file"])
        result = sync_allowlist(allow_path, settings["source_url"])
        refreshed = pd.read_csv(allow_path, dtype={"symbol": str})
        universe = pd.DataFrame(
            {
                "symbol": refreshed["symbol"],
                "name_en": refreshed.get("name", ""),
                "name_ar": "",
                "market": "MAIN",
                "enabled": True,
            }
        )
        universe.to_csv(
            cfg.path(cfg.section("market")["symbols_file"]),
            index=False,
        )
        print(
            f"Sharia sync OK: {result.symbols} compliant Main Market symbols, "
            f"{result.period}, checked {result.checked_on}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - best-effort external sync boundary
        print(
            "Sharia sync failed; keeping last-known-good list: "
            f"{type(exc).__name__}: {exc}"
        )
        return 0 if best_effort else 2


def main() -> int:
    parser = argparse.ArgumentParser(prog="saudi-bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("--send", action="store_true")
    sub.add_parser("backtest")
    sharia_parser = sub.add_parser("sync-sharia")
    sharia_parser.add_argument("--best-effort", action="store_true")
    sub.add_parser("doctor")
    args = parser.parse_args()

    if args.cmd == "scan":
        return scan(send=args.send)
    if args.cmd == "backtest":
        return backtest()
    if args.cmd == "sync-sharia":
        return sync_sharia(best_effort=args.best_effort)

    cfg = load_settings()
    checks = run_doctor(cfg)
    for name, ok, note in checks:
        print(f"{'PASS' if ok else 'WARN'} {name}: {note}")
    required = [check for check in checks if check[0] != "telegram"]
    return 0 if all(ok for _, ok, _ in required) else 2


if __name__ == "__main__":
    raise SystemExit(main())
