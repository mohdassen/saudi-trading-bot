from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from saudi_trading_bot.backtest.core import run_symbol_backtest
from saudi_trading_bot.backtest.rotation import (
    evaluate_rotation_lab,
    write_rotation_report,
)
from saudi_trading_bot.backtest.validation import (
    decide,
    evaluate_strategy_lab,
    write_report,
)
from saudi_trading_bot.config import load_settings
from saudi_trading_bot.data.cache import MarketDataCache
from saudi_trading_bot.data.market_breadth import MarketBreadthResult, SaudiMarketBreadth
from saudi_trading_bot.data.quality import validate_market_data
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
from saudi_trading_bot.signals.strategies import (
    STRATEGIES,
    gate_signal,
    latest_strategy_rows,
)

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


def _completed_history(history: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Exclude an in-progress Saudi daily candle from signal/Paper logic."""
    if history.empty:
        return history
    result = history.sort_index().copy()
    latest_date = pd.Timestamp(result.index[-1]).date()
    if latest_date == now.date() and now.time() < time(15, 10):
        result = result.iloc[:-1].copy()
    return result


def _market_regime(
    cfg,
    histories: dict[str, pd.DataFrame],
) -> MarketBreadthResult:
    s_risk = cfg.section("risk")
    if not bool(s_risk.get("no_trade_if_market_breadth_weak", True)):
        return MarketBreadthResult(
            True,
            "RISK_ON",
            "Saudi breadth gate disabled",
            len(histories),
        )

    settings = cfg.section("market_regime")
    if settings.get("method") != "sharia_breadth":
        raise RuntimeError("Only sharia_breadth market regime is approved")

    return SaudiMarketBreadth(
        min_eligible_symbols=int(settings["min_eligible_symbols"]),
        min_history_rows=int(settings["min_history_rows"]),
        risk_on_min_pct_above_ema50=float(
            settings["risk_on_min_pct_above_ema50"]
        ),
        risk_on_min_pct_above_ema200=float(
            settings["risk_on_min_pct_above_ema200"]
        ),
        risk_on_min_median_mom20_pct=float(
            settings["risk_on_min_median_mom20_pct"]
        ),
        recovery_min_pct_above_ema50=float(
            settings["recovery_min_pct_above_ema50"]
        ),
        recovery_min_pct_above_ema200=float(
            settings["recovery_min_pct_above_ema200"]
        ),
        recovery_min_median_mom20_pct=float(
            settings["recovery_min_median_mom20_pct"]
        ),
    ).evaluate(histories)


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
    s_regime = cfg.section("market_regime")

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

    now = datetime.now(RIYADH)
    end = now.date() + timedelta(days=1)
    start = end - timedelta(days=int(s_data["lookback_days"]) * 2)

    records: list[dict] = []
    histories: dict[str, pd.DataFrame] = {}
    blocked_sharia = 0
    no_data = 0

    # First pass uses completed Saudi daily bars only. This same dataset powers
    # the market regime and Paper execution, avoiding fragile external TASI APIs.
    for _, universe_row in _universe(cfg.path(s_market["symbols_file"])).iterrows():
        symbol = str(universe_row["symbol"])
        decision = sharia.check(symbol)
        if not decision.allowed:
            blocked_sharia += 1
            print(f"BLOCKED {symbol}: Sharia {decision.reason}")
            continue

        raw_history = provider.history(symbol, start, end, s_data["interval"])
        hist = _completed_history(raw_history, now)
        if hist.empty:
            no_data += 1
            print(f"NO_DATA {symbol}: {provider.last_error}")
            continue

        histories[symbol] = hist
        records.append(
            {
                "row": universe_row,
                "decision": decision,
                "history": hist,
                "data_source": provider.last_source,
            }
        )

    regime = _market_regime(cfg, histories)
    quality = validate_market_data(
        histories,
        now,
        min_symbols=int(s_data["min_fresh_symbols"]),
        min_consensus_pct=float(s_data["min_session_consensus_pct"]),
    )
    print(f"DATA_QUALITY {'PASS' if quality.allowed else 'BLOCK'}: {quality.note}")
    print(f"MARKET_REGIME {regime.state}: {regime.note}")
    if disclosures.last_error:
        print(f"DISCLOSURES_FALLBACK: {disclosures.last_error}")

    active_strategy = str(s_signals.get("active_strategy", "CASH"))
    if active_strategy != "CASH" and active_strategy not in STRATEGIES:
        raise RuntimeError(f"Unknown active_strategy: {active_strategy}")
    strategy_rows = (
        latest_strategy_rows(histories) if active_strategy != "CASH" else {}
    )
    print(f"ACTIVE_STRATEGY {active_strategy}")
    for symbol in portfolio.discard_unapproved_pending(active_strategy):
        print(f"PAPER_PENDING_CANCELLED {symbol}: strategy validation gate")

    # Execute signals queued from an earlier completed bar at the first later
    # session open. Only after that do we evaluate that session's stop/target.
    safe_histories = histories if quality.allowed else {}
    for opened in portfolio.execute_pending(safe_histories):
        print(
            f"PAPER_ENTRY {opened.symbol} strategy={opened.strategy} qty={opened.qty} "
            f"entry={opened.entry:.2f} stop={opened.stop:.2f} "
            f"target={opened.target:.2f} score={opened.score:.1f} "
            f"opened_on={opened.opened_on}"
        )
    for closed in portfolio.mark_histories(
        safe_histories,
        max_hold_days=int(s_paper["max_hold_days"]),
    ):
        print(
            f"PAPER_EXIT {closed.symbol} {closed.reason} "
            f"PnL={closed.pnl_sar:.2f} SAR closed_on={closed.closed_on}"
        )

    recovery_breakout_min_score = float(
        s_regime["recovery_breakout_min_strategy_score"]
    )
    rows = []
    ready_candidates = []
    for record in records:
        universe_row = record["row"]
        decision = record["decision"]
        hist = record["history"]
        symbol = str(universe_row["symbol"])

        try:
            impact = disclosures.impact_for(
                symbol, announcements, str(universe_row.get("name_en", ""))
            )
            signal = engine.score(symbol, hist, impact)
            signal = gate_signal(
                signal,
                strategy_rows.get(symbol),
                s_signals,
                active_strategy,
                float(s_risk["reward_risk"]),
            )
        except ValueError as exc:
            print(exc)
            continue

        if regime.state == "RISK_OFF" and signal.state.value == "READY":
            signal = replace(
                signal,
                state=SignalState.WATCH,
                rationale=signal.rationale
                + ("Saudi market RISK_OFF يمنع دخول Paper جديد",),
            )
        elif (
            regime.state == "RECOVERY"
            and signal.state.value == "READY"
            and active_strategy == "breakout"
            and signal.strategy_score < recovery_breakout_min_score
        ):
            signal = replace(
                signal,
                state=SignalState.WATCH,
                rationale=signal.rationale
                + (
                    "RECOVERY market: breakout strategy يتطلب تقييم "
                    f"{recovery_breakout_min_score:.0f}+",
                ),
            )

        rows.append(
            {
                "symbol": symbol,
                "state": signal.state.value,
                "score": signal.total_score,
                "strategy": signal.strategy,
                "strategy_score": signal.strategy_score,
                "price": signal.price,
                "data_source": record["data_source"],
            }
        )
        print(format_signal(signal, decision.source, decision.source_period))

        if quality.allowed and regime.allowed and signal.state == SignalState.READY:
            signal_bar_date = pd.Timestamp(hist.index[-1]).date()
            ready_candidates.append((signal, signal_bar_date))

        changed = alert_state.changed(signal)
        should_send = send and signal.state.value in {"READY", "WATCH"}
        if should_send and s_notify.get("send_only_state_changes", True):
            should_send = changed
        if should_send:
            send_telegram(
                format_signal(signal, decision.source, decision.source_period)
            )

    # Rank the whole market first, then queue only the strongest daily setups.
    # This avoids symbol-order bias and mirrors the daily new-position limit.
    queued_count = 0
    for signal, signal_bar_date in sorted(
        ready_candidates,
        key=lambda item: item[0].strategy_score,
        reverse=True,
    ):
        if queued_count >= int(s_risk["max_daily_new_positions"]):
            break
        queued = portfolio.queue(
            signal,
            signal_bar_date=signal_bar_date,
            reward_risk=float(s_risk["reward_risk"]),
        )
        if queued is None:
            continue
        queued_count += 1
        print(
            f"PAPER_QUEUED {queued.symbol} strategy={queued.strategy} "
            f"score={queued.score:.1f} "
            f"signal_bar={queued.signal_bar_date} execute=next_session_open"
        )

    out = cfg.root / "artifacts/latest_scan.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "symbol",
        "state",
        "score",
        "strategy",
        "strategy_score",
        "price",
        "data_source",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        rank_column = "strategy_score" if active_strategy != "CASH" else "score"
        frame = frame.sort_values(rank_column, ascending=False)
    frame.to_csv(out, index=False)
    print(
        f"SUMMARY scanned={len(rows)} sharia_blocked={blocked_sharia} "
        f"no_data={no_data} open_paper={len(portfolio.positions)} "
        f"pending={len(portfolio.pending)} equity={portfolio.equity_sar:.2f} "
        f"regime={regime.state} data_quality={'PASS' if quality.allowed else 'BLOCK'}"
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


def validate_strategy() -> int:
    """Run long-horizon nested out-of-sample strategy validation."""
    cfg = load_settings()
    s_market = cfg.section("market")
    s_sharia = cfg.section("sharia")
    s_validation = cfg.section("validation")
    provider = _provider(cfg)
    sharia = StrictShariaFilter(
        cfg.path(s_sharia["allowlist_file"]),
        99999,
        s_sharia["block_unknown"],
    )
    end = datetime.now(RIYADH).date() + timedelta(days=1)
    start = end - timedelta(days=365 * int(s_validation["years"]) + 250)
    histories: dict[str, pd.DataFrame] = {}
    for _, row in _universe(cfg.path(s_market["symbols_file"])).iterrows():
        symbol = str(row["symbol"])
        if not sharia.check(symbol).allowed:
            continue
        history = provider.history(symbol, start, end, "1d")
        if len(history) >= 300:
            histories[symbol] = history

    folds, matrix = evaluate_strategy_lab(histories, cfg.raw)
    decision = decide(folds, s_validation)
    write_report(cfg.root / "artifacts", folds, decision, matrix)
    print(f"VALIDATION {decision.status}: symbols={len(histories)} folds={len(folds)}")
    for fold in folds:
        print(
            f"OOS {fold.year}: strategy={fold.strategy} trades={fold.trades} "
            f"win={fold.win_rate:.1f}% "
            f"return={fold.return_pct:.1f}% dd={fold.max_drawdown_pct:.1f}% "
            f"pf={fold.profit_factor:.2f}"
        )
    for key in sorted(matrix, key=lambda item: (item[1], item[0])):
        fold = matrix[key]
        print(
            f"LAB {fold.year}: strategy={fold.strategy} trades={fold.trades} "
            f"return={fold.return_pct:.1f}% dd={fold.max_drawdown_pct:.1f}% "
            f"pf={fold.profit_factor:.2f}"
        )
    if decision.reasons:
        print("BLOCK_REASONS: " + "; ".join(decision.reasons))
    return 0


def validate_rotation() -> int:
    """Run the independent monthly/quarterly Saudi rotation lab."""
    cfg = load_settings()
    s_market = cfg.section("market")
    s_sharia = cfg.section("sharia")
    s_validation = cfg.section("validation")
    provider = _provider(cfg)
    sharia = StrictShariaFilter(
        cfg.path(s_sharia["allowlist_file"]),
        99999,
        s_sharia["block_unknown"],
    )
    end = datetime.now(RIYADH).date() + timedelta(days=1)
    start = end - timedelta(days=365 * int(s_validation["years"]) + 300)
    histories: dict[str, pd.DataFrame] = {}
    for _, row in _universe(cfg.path(s_market["symbols_file"])).iterrows():
        symbol = str(row["symbol"])
        if not sharia.check(symbol).allowed:
            continue
        history = provider.history(symbol, start, end, "1d")
        if len(history) >= 320:
            histories[symbol] = history

    folds, matrix = evaluate_rotation_lab(histories, cfg.raw)
    decision = decide(folds, s_validation)
    write_rotation_report(cfg.root / "artifacts", folds, matrix, decision)
    print(
        f"ROTATION_VALIDATION {decision.status}: "
        f"symbols={len(histories)} folds={len(folds)}"
    )
    for fold in folds:
        print(
            f"ROTATION_OOS {fold.year}: strategy={fold.strategy} "
            f"trades={fold.trades} win={fold.win_rate:.1f}% "
            f"return={fold.return_pct:.1f}% dd={fold.max_drawdown_pct:.1f}% "
            f"pf={fold.profit_factor:.2f}"
        )
    for key in sorted(matrix, key=lambda item: (item[1], item[0])):
        fold = matrix[key]
        print(
            f"ROTATION_LAB {fold.year}: strategy={fold.strategy} "
            f"trades={fold.trades} return={fold.return_pct:.1f}% "
            f"dd={fold.max_drawdown_pct:.1f}% pf={fold.profit_factor:.2f}"
        )
    if decision.reasons:
        print("ROTATION_BLOCK_REASONS: " + "; ".join(decision.reasons))
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
    sub.add_parser("validate")
    sub.add_parser("validate-rotation")
    sharia_parser = sub.add_parser("sync-sharia")
    sharia_parser.add_argument("--best-effort", action="store_true")
    sub.add_parser("doctor")
    args = parser.parse_args()

    if args.cmd == "scan":
        return scan(send=args.send)
    if args.cmd == "backtest":
        return backtest()
    if args.cmd == "validate":
        return validate_strategy()
    if args.cmd == "sync-sharia":
        return sync_sharia(best_effort=args.best_effort)
    if args.cmd == "validate-rotation":
        return validate_rotation()

    cfg = load_settings()
    checks = run_doctor(cfg)
    for name, ok, note in checks:
        print(f"{'PASS' if ok else 'WARN'} {name}: {note}")
    required = [check for check in checks if check[0] != "telegram"]
    return 0 if all(ok for _, ok, _ in required) else 2


if __name__ == "__main__":
    raise SystemExit(main())
