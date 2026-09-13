from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import pandas as pd

from saudi_trading_bot.config import load_settings
from saudi_trading_bot.data.cache import MarketDataCache
from saudi_trading_bot.data.resilient import ResilientFreeProvider
from saudi_trading_bot.data.yahoo import YahooSaudiProvider
from saudi_trading_bot.sharia.filter import StrictShariaFilter
from saudi_trading_bot.signals.indicators import enrich

RIYADH = ZoneInfo("Asia/Riyadh")


@dataclass(frozen=True)
class HistoricalTrade:
    symbol: str
    signal_date: str
    entry_date: str
    exit_date: str
    regime: str
    entry: float
    exit: float
    stop: float
    target: float
    reason: str
    r_multiple: float
    net_return_pct: float


@dataclass(frozen=True)
class SliceResult:
    name: str
    trades: int
    win_rate_pct: float
    expectancy_r: float | None
    profit_factor: float
    max_drawdown_r: float | None
    net_return_pct_sum: float


def _universe(cfg) -> list[str]:
    market = cfg.section("market")
    frame = pd.read_csv(cfg.path(market["symbols_file"]), dtype={"symbol": str})
    enabled = frame[frame["enabled"].astype(str).str.lower().eq("true")]
    return enabled["symbol"].astype(str).tolist()


def _provider(cfg) -> ResilientFreeProvider:
    settings = cfg.section("data")
    if not bool(settings.get("free_only", True)):
        raise RuntimeError("Historical Momentum validation enforces free_only=true")
    return ResilientFreeProvider(
        YahooSaudiProvider(suffix=settings.get("suffix", ".SR")),
        MarketDataCache(cfg.path(settings["cache_dir"])),
    )


def _regime(breadth: dict[str, float], rules: dict) -> str:
    pct50 = breadth["pct50"]
    pct200 = breadth["pct200"]
    med20 = breadth["med20"]
    risk_on = (
        pct50 >= float(rules["risk_on_min_pct_above_ema50"])
        and pct200 >= float(rules["risk_on_min_pct_above_ema200"])
        and med20 >= float(rules["risk_on_min_median_mom20_pct"])
    )
    recovery = (
        pct50 >= float(rules["recovery_min_pct_above_ema50"])
        and pct200 >= float(rules["recovery_min_pct_above_ema200"])
        and med20 >= float(rules["recovery_min_median_mom20_pct"])
    )
    return "RISK_ON" if risk_on else ("RECOVERY" if recovery else "RISK_OFF")


def _summarize(name: str, trades: list[HistoricalTrade]) -> SliceResult:
    if not trades:
        return SliceResult(name, 0, 0.0, None, 0.0, None, 0.0)
    r_values = [x.r_multiple for x in trades]
    wins = [x for x in r_values if x > 0]
    losses = [x for x in r_values if x < 0]
    pf = sum(wins) / abs(sum(losses)) if losses else (999.0 if wins else 0.0)
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for value in r_values:
        cumulative += value
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return SliceResult(
        name=name,
        trades=len(trades),
        win_rate_pct=round(len(wins) / len(trades) * 100.0, 2),
        expectancy_r=round(sum(r_values) / len(r_values), 3),
        profit_factor=round(pf, 3),
        max_drawdown_r=round(abs(worst), 3),
        net_return_pct_sum=round(sum(x.net_return_pct for x in trades), 2),
    )


def _simulate_trade(
    symbol: str,
    frame: pd.DataFrame,
    signal_i: int,
    regime: str,
    momentum_cfg: dict,
    paper_cfg: dict,
) -> HistoricalTrade | None:
    if signal_i + 1 >= len(frame):
        return None
    signal_row = frame.iloc[signal_i]
    entry_i = signal_i + 1
    entry = float(frame.iloc[entry_i]["open"])
    atr = float(signal_row["atr14"])
    if entry <= 0 or atr <= 0:
        return None
    risk_distance = max(0.01, atr * float(momentum_cfg["atr_stop_multiple"]))
    stop = max(0.01, entry - risk_distance)
    target = entry + float(momentum_cfg["reward_risk"]) * risk_distance
    max_hold = int(momentum_cfg["max_hold_days"])
    exit_i = min(entry_i + max_hold, len(frame) - 1)
    exit_price = float(frame.iloc[exit_i]["close"])
    reason = "max_hold"
    for j in range(entry_i, exit_i + 1):
        day = frame.iloc[j]
        if float(day["low"]) <= stop:
            exit_price = stop
            exit_i = j
            reason = "stop"
            break
        if float(day["high"]) >= target:
            exit_price = target
            exit_i = j
            reason = "target"
            break
    friction = 2.0 * (
        float(paper_cfg["commission_bps"]) + float(paper_cfg["slippage_bps"])
    ) / 10000.0
    net_return = exit_price / entry - 1.0 - friction
    r_multiple = net_return / (risk_distance / entry)
    return HistoricalTrade(
        symbol=symbol,
        signal_date=str(pd.Timestamp(frame.index[signal_i]).date()),
        entry_date=str(pd.Timestamp(frame.index[entry_i]).date()),
        exit_date=str(pd.Timestamp(frame.index[exit_i]).date()),
        regime=regime,
        entry=round(entry, 4),
        exit=round(exit_price, 4),
        stop=round(stop, 4),
        target=round(target, 4),
        reason=reason,
        r_multiple=round(r_multiple, 4),
        net_return_pct=round(net_return * 100.0, 4),
    )


def build_report(start: date | None = None, end: date | None = None) -> dict:
    cfg = load_settings()
    now = datetime.now(RIYADH)
    end = end or now.date()
    years = int(cfg.section("validation").get("years", 10))
    start = start or date(end.year - years, 1, 1)
    data_cfg = cfg.section("data")
    signal_cfg = cfg.section("signals")
    edge_cfg = cfg.section("edge_lab")
    momentum_cfg = edge_cfg["momentum"]
    paper_cfg = cfg.section("paper")
    regime_rules = cfg.section("market_regime")
    provider = _provider(cfg)
    sharia_cfg = cfg.section("sharia")
    sharia = StrictShariaFilter(
        cfg.path(sharia_cfg["allowlist_file"]),
        sharia_cfg["max_source_check_age_days"],
        sharia_cfg["block_unknown"],
    )

    fetch_start = start - timedelta(days=420)
    fetch_end = end + timedelta(days=2)
    frames: dict[str, pd.DataFrame] = {}
    for symbol in _universe(cfg):
        if not sharia.check(symbol).allowed:
            continue
        raw = provider.history(symbol, fetch_start, fetch_end, data_cfg["interval"])
        if len(raw) < 252:
            continue
        x = enrich(raw).copy()
        x["avg_value20"] = (x["close"].astype(float) * x["volume"].astype(float)).rolling(20).mean()
        x["mom6_1"] = x["close"].shift(21) / x["close"].shift(126) - 1.0
        x["mom3_1"] = x["close"].shift(21) / x["close"].shift(63) - 1.0
        x["high252"] = x["high"].rolling(252).max()
        x["recent20"] = x["close"] / x["close"].shift(20) - 1.0
        x["roc63"] = x["close"] / x["close"].shift(63) - 1.0
        frames[symbol] = x

    dates: dict[date, list[tuple[str, int]]] = defaultdict(list)
    for symbol, frame in frames.items():
        for i, idx in enumerate(frame.index):
            d = pd.Timestamp(idx).date()
            if start <= d <= end:
                dates[d].append((symbol, i))

    trades: list[HistoricalTrade] = []
    blocked_until: dict[str, date] = {}
    for d in sorted(dates):
        rows = dates[d]
        eligible = []
        breadth50: list[bool] = []
        breadth200: list[bool] = []
        mom20_values: list[float] = []
        for symbol, i in rows:
            row = frames[symbol].iloc[i]
            required = ["close", "ema50", "ema200", "roc63", "mom6_1", "mom3_1", "high252", "recent20", "atr14", "avg_value20"]
            if any(pd.isna(row.get(k)) for k in required):
                continue
            close = float(row["close"])
            breadth50.append(close > float(row["ema50"]))
            breadth200.append(close > float(row["ema200"]))
            if i >= 20:
                prev = float(frames[symbol].iloc[i - 20]["close"])
                if prev > 0:
                    mom20_values.append(close / prev - 1.0)
            eligible.append((symbol, i, float(row["roc63"])))
        if len(eligible) < int(regime_rules["historical_min_eligible_symbols"]):
            continue
        pct50 = sum(breadth50) / len(breadth50) * 100.0
        pct200 = sum(breadth200) / len(breadth200) * 100.0
        med20 = median(mom20_values) * 100.0 if mom20_values else 0.0
        regime = _regime({"pct50": pct50, "pct200": pct200, "med20": med20}, regime_rules)
        roc_values = pd.Series([x[2] for x in eligible])
        percentiles = roc_values.rank(pct=True, method="average").tolist()
        for (symbol, i, _), xs in zip(eligible, percentiles):
            if blocked_until.get(symbol) and d <= blocked_until[symbol]:
                continue
            row = frames[symbol].iloc[i]
            price = float(row["close"])
            atr_pct = float(row["atr14"]) / price if price > 0 else 99.0
            high_proximity = price / float(row["high252"]) if float(row["high252"]) > 0 else 0.0
            valid = (
                price > float(row["ema50"]) > float(row["ema200"])
                and xs >= float(momentum_cfg["min_cross_section_percentile"])
                and float(row["mom6_1"]) >= float(momentum_cfg["min_mom6_1_pct"]) / 100.0
                and float(row["mom3_1"]) >= float(momentum_cfg["min_mom3_1_pct"]) / 100.0
                and high_proximity >= float(momentum_cfg["min_52w_high_proximity"])
                and float(momentum_cfg["recent20_min_pct"]) / 100.0 <= float(row["recent20"]) <= float(momentum_cfg["recent20_max_pct"]) / 100.0
                and atr_pct <= float(momentum_cfg["max_atr_pct"]) / 100.0
                and float(row["avg_value20"]) >= float(momentum_cfg["min_avg_value_sar_20d"])
                and price >= float(signal_cfg["min_price_sar"])
            )
            if not valid:
                continue
            trade = _simulate_trade(symbol, frames[symbol], i, regime, momentum_cfg, paper_cfg)
            if trade is None:
                continue
            trades.append(trade)
            blocked_until[symbol] = pd.Timestamp(trade.exit_date).date()

    slices = [_summarize("ALL", trades)]
    for regime in ("RISK_ON", "RECOVERY", "RISK_OFF"):
        slices.append(_summarize(regime, [t for t in trades if t.regime == regime]))
    yearly = []
    for year in sorted({pd.Timestamp(t.signal_date).year for t in trades}):
        yearly.append(_summarize(str(year), [t for t in trades if pd.Timestamp(t.signal_date).year == year]))

    qualification = edge_cfg["qualification"]
    all_result = slices[0]
    pass_gate = (
        all_result.trades >= 30
        and all_result.expectancy_r is not None
        and all_result.expectancy_r >= float(qualification["min_expectancy_r"])
        and all_result.profit_factor >= float(qualification["min_profit_factor"])
        and all_result.max_drawdown_r is not None
        and all_result.max_drawdown_r <= float(qualification["max_drawdown_r"])
    )
    return {
        "generated_at": now.isoformat(),
        "strategy": "momentum_leadership_shadow",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "symbols": len(frames),
        "trade_count": len(trades),
        "historical_gate_pass": pass_gate,
        "production_strategy_unchanged": "CASH",
        "thresholds_unchanged": True,
        "free_data_only": True,
        "slices": [asdict(x) for x in slices],
        "yearly": [asdict(x) for x in yearly],
        "trades": [asdict(x) for x in trades],
    }


def run() -> int:
    cfg = load_settings()
    report = build_report()
    output = cfg.path("artifacts/momentum_leadership_historical.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "MOMENTUM_HISTORICAL "
        f"trades={report['trade_count']} gate={'PASS' if report['historical_gate_pass'] else 'FAIL'} "
        f"symbols={report['symbols']} production=CASH"
    )
    for item in report["slices"]:
        print(
            "MOMENTUM_HIST_SLICE "
            f"name={item['name']} trades={item['trades']} WR={item['win_rate_pct']:.1f}% "
            f"E={item['expectancy_r']}R PF={item['profit_factor']} DD={item['max_drawdown_r']}R"
        )
    print(f"MOMENTUM_HISTORICAL_FILE {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
