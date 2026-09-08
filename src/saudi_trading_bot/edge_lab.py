from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.config import load_settings
from saudi_trading_bot.data.cache import MarketDataCache
from saudi_trading_bot.data.quality import validate_market_data
from saudi_trading_bot.data.resilient import ResilientFreeProvider
from saudi_trading_bot.data.yahoo import YahooSaudiProvider
from saudi_trading_bot.disclosures.financials import EarningsSnapshot, SaudiFinancialResultReader
from saudi_trading_bot.disclosures.saudi_exchange import Announcement, SaudiExchangeDisclosures
from saudi_trading_bot.models import Signal, SignalState
from saudi_trading_bot.paper.portfolio import PaperPortfolio
from saudi_trading_bot.sharia.filter import StrictShariaFilter
from saudi_trading_bot.signals.engine import SignalEngine
from saudi_trading_bot.signals.strategies import latest_strategy_rows

RIYADH = ZoneInfo("Asia/Riyadh")


@dataclass(frozen=True)
class EdgeProgress:
    name: str
    closed_trades: int
    target_trades: int
    win_rate_pct: float
    expectancy_r: float | None
    profit_factor: float
    max_drawdown_r: float | None
    pnl_sar: float
    open_positions: int
    pending: int
    qualified: bool
    status: str


def _universe(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"symbol": str})
    return frame[frame["enabled"].astype(str).str.lower().eq("true")]


def _completed_history(history: pd.DataFrame, now: datetime) -> pd.DataFrame:
    if history.empty:
        return history
    result = history.sort_index().copy()
    latest_date = pd.Timestamp(result.index[-1]).date()
    if latest_date == now.date() and now.time() < time(15, 10):
        result = result.iloc[:-1].copy()
    return result


def _provider(cfg) -> ResilientFreeProvider:
    settings = cfg.section("data")
    if not bool(settings.get("free_only", True)):
        raise RuntimeError("Edge Lab enforces free_only=true")
    primary = YahooSaudiProvider(suffix=settings.get("suffix", ".SR"))
    return ResilientFreeProvider(
        primary,
        MarketDataCache(cfg.path(settings["cache_dir"])),
    )


def _portfolio(cfg, edge_cfg: dict) -> PaperPortfolio:
    risk = cfg.section("risk")
    paper = cfg.section("paper")
    return PaperPortfolio(
        cfg.path(edge_cfg["portfolio_file"]),
        float(risk["paper_equity_sar"]),
        float(edge_cfg["risk_per_trade_pct"]),
        float(edge_cfg["max_position_pct"]),
        int(edge_cfg["max_open_positions"]),
        int(edge_cfg["max_daily_new_positions"]),
        float(paper["commission_bps"]),
        float(paper["slippage_bps"]),
    )


def _performance(
    name: str,
    portfolio: PaperPortfolio,
    target_trades: int,
    qualification: dict,
) -> EdgeProgress:
    trades = portfolio.closed
    pnl = sum(float(item.pnl_sar) for item in trades)
    gains = sum(max(0.0, float(item.pnl_sar)) for item in trades)
    losses = abs(sum(min(0.0, float(item.pnl_sar)) for item in trades))
    profit_factor = gains / losses if losses else (999.0 if gains else 0.0)
    win_rate = (
        sum(float(item.pnl_sar) > 0 for item in trades) / len(trades) * 100.0
        if trades
        else 0.0
    )
    r_values = [
        float(item.pnl_sar) / float(item.initial_risk_sar)
        for item in trades
        if float(getattr(item, "initial_risk_sar", 0.0)) > 0
    ]
    expectancy = sum(r_values) / len(r_values) if r_values else None
    max_dd_r = None
    if r_values:
        cumulative = 0.0
        peak = 0.0
        worst = 0.0
        for value in r_values:
            cumulative += value
            peak = max(peak, cumulative)
            worst = min(worst, cumulative - peak)
        max_dd_r = abs(worst)

    qualified = (
        len(trades) >= int(target_trades)
        and expectancy is not None
        and expectancy >= float(qualification["min_expectancy_r"])
        and profit_factor >= float(qualification["min_profit_factor"])
        and max_dd_r is not None
        and max_dd_r <= float(qualification["max_drawdown_r"])
    )
    if len(trades) < int(target_trades):
        status = f"LEARNING {len(trades)}/{target_trades}"
    elif qualified:
        status = "FORWARD_PASS"
    else:
        status = "FORWARD_FAIL"

    return EdgeProgress(
        name=name,
        closed_trades=len(trades),
        target_trades=int(target_trades),
        win_rate_pct=round(win_rate, 2),
        expectancy_r=None if expectancy is None else round(expectancy, 3),
        profit_factor=round(profit_factor, 3),
        max_drawdown_r=None if max_dd_r is None else round(max_dd_r, 3),
        pnl_sar=round(pnl, 2),
        open_positions=len(portfolio.positions),
        pending=len(portfolio.pending),
        qualified=qualified,
        status=status,
    )


def _financial_title(title: str) -> bool:
    text = title.lower()
    return any(
        token in text
        for token in (
            "financial result",
            "financial results",
            "interim financial",
            "annual financial",
            "النتائج المالية",
            "النتائج الماليه",
        )
    )


def _published(snapshot: EarningsSnapshot) -> datetime | None:
    if not snapshot.published_at:
        return None
    try:
        value = datetime.fromisoformat(snapshot.published_at)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=RIYADH)


def _sessions_since(history: pd.DataFrame, published: datetime) -> int:
    dates = [pd.Timestamp(index).date() for index in history.index]
    if published.time() < time(10, 0):
        return sum(session >= published.date() for session in dates)
    return sum(session > published.date() for session in dates)


def _pre_event_close(history: pd.DataFrame, published: datetime) -> float | None:
    rows = history.sort_index()
    eligible = []
    for index, row in rows.iterrows():
        session = pd.Timestamp(index).date()
        if published.time() < time(10, 0):
            if session < published.date():
                eligible.append(float(row["close"]))
        elif session <= published.date():
            eligible.append(float(row["close"]))
    return eligible[-1] if eligible else None


def _pead_candidate(
    base: Signal,
    row: pd.Series,
    history: pd.DataFrame,
    snapshot: EarningsSnapshot | None,
    cfg: dict,
) -> Signal | None:
    if snapshot is None:
        return None
    published = _published(snapshot)
    if published is None:
        return None

    sessions = _sessions_since(history, published)
    if not int(cfg["min_sessions_after_announcement"]) <= sessions <= int(
        cfg["max_sessions_after_announcement"]
    ):
        return None
    if snapshot.earnings_score < float(cfg["min_earnings_score"]):
        return None
    if snapshot.net_current is None or snapshot.net_current <= 0:
        return None

    price = float(row["close"])
    if not (
        price > float(row["ema50"])
        and float(row["avg_value20"]) >= float(cfg["min_avg_value_sar_20d"])
        and base.total_score >= float(cfg["min_technical_score"])
    ):
        return None

    before = _pre_event_close(history, published)
    if before is None or before <= 0:
        return None
    reaction = price / before - 1.0
    if not float(cfg["min_post_event_return_pct"]) / 100 <= reaction <= float(
        cfg["max_post_event_return_pct"]
    ) / 100:
        return None

    score = 0.70 * snapshot.earnings_score + 0.30 * base.total_score
    risk_distance = max(0.01, float(base.atr) * float(cfg["atr_stop_multiple"]))
    stop = max(0.01, price - risk_distance)
    target = price + float(cfg["reward_risk"]) * risk_distance
    return replace(
        base,
        state=SignalState.READY,
        stop=round(stop, 2),
        target=round(target, 2),
        strategy="pead_shadow",
        strategy_score=round(score, 2),
        rationale=base.rationale
        + (
            f"PEAD earnings={snapshot.earnings_score:.0f}/100",
            f"post-event sessions={sessions} return={reaction * 100:.1f}%",
        ),
    )


def _momentum_candidate(
    base: Signal,
    row: pd.Series,
    history: pd.DataFrame,
    cfg: dict,
) -> Signal | None:
    frame = history.sort_index()
    if len(frame) < 252:
        return None
    price = float(row["close"])
    closes = frame["close"].astype(float)
    mom6_1 = float(closes.iloc[-21] / closes.iloc[-126] - 1.0)
    mom3_1 = float(closes.iloc[-21] / closes.iloc[-63] - 1.0)
    high252 = float(frame["high"].astype(float).tail(252).max())
    high_proximity = price / high252 if high252 > 0 else 0.0
    recent20 = float(closes.iloc[-1] / closes.iloc[-21] - 1.0)
    xs = float(row.get("xs_roc63_percentile", 0.0))
    atr_pct = float(base.atr) / price if price > 0 else 99.0

    valid = (
        price > float(row["ema50"]) > float(row["ema200"])
        and xs >= float(cfg["min_cross_section_percentile"])
        and mom6_1 >= float(cfg["min_mom6_1_pct"]) / 100.0
        and mom3_1 >= float(cfg["min_mom3_1_pct"]) / 100.0
        and high_proximity >= float(cfg["min_52w_high_proximity"])
        and float(cfg["recent20_min_pct"]) / 100.0
        <= recent20
        <= float(cfg["recent20_max_pct"]) / 100.0
        and atr_pct <= float(cfg["max_atr_pct"]) / 100.0
        and float(row["avg_value20"]) >= float(cfg["min_avg_value_sar_20d"])
    )
    if not valid:
        return None

    score = (
        xs * 45.0
        + min(1.0, mom6_1 / 0.30) * 30.0
        + min(1.0, high_proximity) * 25.0
    )
    risk_distance = max(0.01, float(base.atr) * float(cfg["atr_stop_multiple"]))
    stop = max(0.01, price - risk_distance)
    target = price + float(cfg["reward_risk"]) * risk_distance
    return replace(
        base,
        state=SignalState.READY,
        stop=round(stop, 2),
        target=round(target, 2),
        strategy="momentum_leadership_shadow",
        strategy_score=round(score, 2),
        rationale=base.rationale
        + (
            f"Momentum leadership xs={xs:.2f} 6-1m={mom6_1 * 100:.1f}%",
            f"52w proximity={high_proximity:.2f}",
        ),
    )


def _latest_earnings(
    symbol: str,
    announcements: list[Announcement],
    reader: SaudiFinancialResultReader,
    lookback_days: int,
    now: datetime,
) -> EarningsSnapshot | None:
    matches = [
        item
        for item in announcements
        if item.symbol == symbol
        and _financial_title(item.title)
        and "saudiexchange.sa" in item.url
    ]
    snapshots = []
    for item in matches[:5]:
        snapshot = reader.read(symbol, item.url)
        published = _published(snapshot) if snapshot else None
        if published and now - published <= timedelta(days=lookback_days):
            snapshots.append(snapshot)
    if not snapshots:
        return None
    return max(
        snapshots,
        key=lambda item: _published(item) or datetime.min.replace(tzinfo=RIYADH),
    )


def run() -> int:
    cfg = load_settings()
    edge = cfg.section("edge_lab")
    if not bool(edge.get("enabled", True)):
        print("EDGE_LAB DISABLED")
        return 0

    market = cfg.section("market")
    data_cfg = cfg.section("data")
    sharia_cfg = cfg.section("sharia")
    signal_cfg = cfg.section("signals")
    pead_cfg = edge["pead"]
    momentum_cfg = edge["momentum"]
    qualification = edge["qualification"]
    target_trades = int(edge["target_closed_trades"])

    now = datetime.now(RIYADH)
    provider = _provider(cfg)
    sharia = StrictShariaFilter(
        cfg.path(sharia_cfg["allowlist_file"]),
        sharia_cfg["max_source_check_age_days"],
        sharia_cfg["block_unknown"],
    )
    engine = SignalEngine(signal_cfg)
    end = now.date() + timedelta(days=1)
    start = end - timedelta(days=int(data_cfg["lookback_days"]) * 2)

    histories: dict[str, pd.DataFrame] = {}
    for _, row in _universe(cfg.path(market["symbols_file"])).iterrows():
        symbol = str(row["symbol"])
        if not sharia.check(symbol).allowed:
            continue
        history = _completed_history(
            provider.history(symbol, start, end, data_cfg["interval"]),
            now,
        )
        if len(history) >= int(signal_cfg["min_history_rows"]):
            histories[symbol] = history

    quality = validate_market_data(
        histories,
        now,
        min_symbols=int(data_cfg["min_fresh_symbols"]),
        min_consensus_pct=float(data_cfg["min_session_consensus_pct"]),
    )
    if not quality.allowed:
        print(f"EDGE_LAB DATA_BLOCK: {quality.note}")
        return 0

    pead = _portfolio(cfg, pead_cfg)
    momentum = _portfolio(cfg, momentum_cfg)
    for name, portfolio, settings in (
        ("PEAD", pead, pead_cfg),
        ("MOMENTUM", momentum, momentum_cfg),
    ):
        for opened in portfolio.execute_pending(histories):
            print(
                f"EDGE_{name}_ENTRY {opened.symbol} entry={opened.entry:.2f} "
                f"stop={opened.stop:.2f} target={opened.target:.2f}"
            )
        for closed in portfolio.mark_histories(
            histories,
            max_hold_days=int(settings["max_hold_days"]),
        ):
            print(
                f"EDGE_{name}_EXIT {closed.symbol} {closed.reason} "
                f"pnl={closed.pnl_sar:.2f}SAR"
            )

    disc_cfg = cfg.section("disclosures")
    edge_announcements = SaudiExchangeDisclosures(
        disc_cfg["source_url"],
        cfg.path(edge["announcements_cache_file"]),
        int(disc_cfg["timeout_seconds"]),
        int(pead_cfg["announcement_lookback_days"]),
        edge["official_home_fallback_url"],
    )
    announcements = edge_announcements.refresh()
    financial_reader = SaudiFinancialResultReader(
        cfg.path(edge["earnings_cache_file"]),
        timeout=int(disc_cfg["timeout_seconds"]),
    )

    strategy_rows = latest_strategy_rows(histories)
    pead_candidates: list[tuple[Signal, date]] = []
    momentum_candidates: list[tuple[Signal, date]] = []
    for symbol, history in histories.items():
        row = strategy_rows.get(symbol)
        if row is None:
            continue
        try:
            base = engine.score(symbol, history)
        except ValueError:
            continue

        earnings = _latest_earnings(
            symbol,
            announcements,
            financial_reader,
            int(pead_cfg["announcement_lookback_days"]),
            now,
        )
        pead_signal = _pead_candidate(base, row, history, earnings, pead_cfg)
        if pead_signal is not None:
            pead_candidates.append(
                (pead_signal, pd.Timestamp(history.index[-1]).date())
            )

        momentum_signal = _momentum_candidate(base, row, history, momentum_cfg)
        if momentum_signal is not None:
            momentum_candidates.append(
                (momentum_signal, pd.Timestamp(history.index[-1]).date())
            )

    for portfolio, candidates, settings, label in (
        (pead, pead_candidates, pead_cfg, "PEAD"),
        (momentum, momentum_candidates, momentum_cfg, "MOMENTUM"),
    ):
        queued = 0
        for signal, signal_date in sorted(
            candidates,
            key=lambda item: item[0].strategy_score,
            reverse=True,
        ):
            if queued >= int(settings["max_daily_new_positions"]):
                break
            item = portfolio.queue(
                signal,
                signal_bar_date=signal_date,
                reward_risk=float(settings["reward_risk"]),
            )
            if item is None:
                continue
            queued += 1
            print(
                f"EDGE_{label}_QUEUED {item.symbol} score={item.score:.1f} "
                f"signal_bar={item.signal_bar_date}"
            )

    progress = [
        _performance("PEAD", pead, target_trades, qualification),
        _performance("Momentum Leadership", momentum, target_trades, qualification),
    ]
    payload = {
        "updated_at": now.isoformat(),
        "data_quality": quality.note,
        "production_unchanged": "CASH",
        "qualification": qualification,
        "edges": [asdict(item) for item in progress],
    }
    output = cfg.path(edge["progress_file"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for item in progress:
        exp = "n/a" if item.expectancy_r is None else f"{item.expectancy_r:+.3f}R"
        dd = "n/a" if item.max_drawdown_r is None else f"{item.max_drawdown_r:.2f}R"
        print(
            f"EDGE_PROGRESS {item.name}: {item.closed_trades}/{item.target_trades} "
            f"WR={item.win_rate_pct:.1f}% EXP={exp} PF={item.profit_factor:.2f} "
            f"DD={dd} open={item.open_positions} pending={item.pending} "
            f"status={item.status}"
        )
    print(f"EDGE_LAB_SAVED {output}")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
