from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from saudi_trading_bot.backtest.core import run_symbol_backtest
from saudi_trading_bot.config import load_settings
from saudi_trading_bot.data.cache import MarketDataCache
from saudi_trading_bot.data.resilient import ResilientFreeProvider
from saudi_trading_bot.disclosures.saudi_exchange import SaudiExchangeDisclosures
from saudi_trading_bot.doctor import run_doctor
from saudi_trading_bot.models import SignalState
from saudi_trading_bot.notify.state import AlertState
from saudi_trading_bot.notify.telegram import format_signal, send_telegram
from saudi_trading_bot.paper.portfolio import PaperPortfolio
from saudi_trading_bot.sharia.alrajhi import sync_allowlist
from saudi_trading_bot.sharia.filter import StrictShariaFilter
from saudi_trading_bot.signals.engine import SignalEngine
from saudi_trading_bot.signals.indicators import enrich


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
    return ResilientFreeProvider(primary, MarketDataCache(cfg.path(s_data["cache_dir"])))


def _market_regime_ok(provider, cfg, start: date, end: date) -> tuple[bool, str]:
    s_market, s_risk = cfg.section("market"), cfg.section("risk")
    if not bool(s_risk.get("no_trade_if_tasi_below_ema200", True)):
        return True, "gate disabled"
    hist = provider.history(s_market.get("tasi_symbol", "^TASI.SR"), start, end, "1d")
    if hist.empty or len(hist) < 220:
        return False, "TASI data unavailable/insufficient"
    x = enrich(hist).dropna(subset=["ema200"])
    if x.empty:
        return False, "TASI EMA200 unavailable"
    last = x.iloc[-1]
    ok = float(last["close"]) >= float(last["ema200"])
    return ok, f"TASI {last['close']:.1f} vs EMA200 {last['ema200']:.1f}"


def scan(send: bool = False) -> int:
    load_dotenv()
    cfg = load_settings()
    s_market, s_data = cfg.section("market"), cfg.section("data")
    s_sharia, s_signals, s_risk = cfg.section("sharia"), cfg.section("signals"), cfg.section("risk")
    s_paper, s_disc, s_notify = cfg.section("paper"), cfg.section("disclosures"), cfg.section("notifications")
    provider = _provider(cfg)
    sharia = StrictShariaFilter(cfg.path(s_sharia["allowlist_file"]), s_sharia["max_source_check_age_days"], s_sharia["block_unknown"])
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

    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=int(s_data["lookback_days"]) * 2)
    regime_ok, regime_note = _market_regime_ok(provider, cfg, start, end)
    print(f"MARKET_REGIME {'PASS' if regime_ok else 'BLOCK'}: {regime_note}")
    if disclosures.last_error:
        print(f"DISCLOSURES_FALLBACK: {disclosures.last_error}")

    rows = []
    blocked_sharia = no_data = 0
    for _, u in _universe(cfg.path(s_market["symbols_file"])).iterrows():
        symbol = str(u["symbol"])
        sd = sharia.check(symbol)
        if not sd.allowed:
            blocked_sharia += 1
            print(f"BLOCKED {symbol}: Sharia {sd.reason}")
            continue
        hist = provider.history(symbol, start, end, s_data["interval"])
        if hist.empty:
            no_data += 1
            print(f"NO_DATA {symbol}: {provider.last_error}")
            continue
        last = hist.iloc[-1]
        closed = portfolio.mark_daily_bar(symbol, float(last["low"]), float(last["high"]), float(last["close"]), max_hold_days=s_paper["max_hold_days"])
        if closed:
            print(f"PAPER_EXIT {closed.symbol} {closed.reason} PnL={closed.pnl_sar:.2f} SAR")
        try:
            impact = disclosures.impact_for(symbol, announcements)
            sig = engine.score(symbol, hist, impact)
        except ValueError as exc:
            print(exc)
            continue
        if not regime_ok and sig.state.value == "READY":
            sig = replace(
                sig,
                state=SignalState.WATCH,
                rationale=sig.rationale + ("TASI regime gate يمنع دخول Paper جديد",),
            )
        rows.append({"symbol": symbol, "state": sig.state.value, "score": sig.total_score, "price": sig.price, "data_source": provider.last_source})
        print(format_signal(sig, sd.source, sd.source_period))
        if regime_ok:
            portfolio.consider(sig)
        changed = alert_state.changed(sig)
        should_send = send and sig.state.value in {"READY", "WATCH"}
        if should_send and s_notify.get("send_only_state_changes", True):
            should_send = changed
        if should_send:
            send_telegram(format_signal(sig, sd.source, sd.source_period))

    out = cfg.root / "artifacts/latest_scan.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values("score", ascending=False).to_csv(out, index=False) if rows else pd.DataFrame(columns=["symbol", "state", "score", "price", "data_source"]).to_csv(out, index=False)
    print(f"SUMMARY scanned={len(rows)} sharia_blocked={blocked_sharia} no_data={no_data} open_paper={len(portfolio.positions)} equity={portfolio.equity_sar:.2f}")
    print(f"Saved {out}")
    return 0


def backtest() -> int:
    cfg = load_settings()
    s_market, s_paper, s_sharia = cfg.section("market"), cfg.section("paper"), cfg.section("sharia")
    provider = _provider(cfg)
    sharia = StrictShariaFilter(cfg.path(s_sharia["allowlist_file"]), 99999, s_sharia["block_unknown"])
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=365 * 5)
    results = []
    for _, u in _universe(cfg.path(s_market["symbols_file"])).iterrows():
        symbol = str(u["symbol"])
        if sharia.check(symbol).status != "allowed":
            continue
        hist = provider.history(symbol, start, end, "1d")
        if hist.empty:
            continue
        r = run_symbol_backtest(symbol, hist, s_paper["commission_bps"], s_paper["slippage_bps"], s_paper["max_hold_days"])
        results.append(r.__dict__)
    out = cfg.root / "artifacts/backtest.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(results)
    frame.to_csv(out, index=False)
    print(frame.to_string(index=False) if not frame.empty else "No backtest data available")
    print(f"Saved {out}")
    return 0


def sync_sharia(best_effort: bool = False) -> int:
    cfg = load_settings()
    s = cfg.section("sharia")
    try:
        allow_path = cfg.path(s["allowlist_file"])
        r = sync_allowlist(allow_path, s["source_url"])
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
        universe.to_csv(cfg.path(cfg.section("market")["symbols_file"]), index=False)
        print(
            f"Sharia sync OK: {r.symbols} compliant Main Market symbols, "
            f"{r.period}, checked {r.checked_on}"
        )
        return 0
    except Exception as exc:
        print(f"Sharia sync failed; keeping last-known-good list: {type(exc).__name__}: {exc}")
        return 0 if best_effort else 2


def main() -> int:
    p = argparse.ArgumentParser(prog="saudi-bot")
    sub = p.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("scan")
    ps.add_argument("--send", action="store_true")
    sub.add_parser("backtest")
    sh = sub.add_parser("sync-sharia")
    sh.add_argument("--best-effort", action="store_true")
    sub.add_parser("doctor")
    args = p.parse_args()
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
    required = [x for x in checks if x[0] != "telegram"]
    return 0 if all(ok for _, ok, _ in required) else 2


if __name__ == "__main__":
    raise SystemExit(main())
