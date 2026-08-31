import pandas as pd

from saudi_trading_bot.data.market_breadth import SaudiMarketBreadth


def _history(start: float, step: float, rows: int = 260) -> pd.DataFrame:
    close = [start + step * i for i in range(rows)]
    return pd.DataFrame({"close": close})


def _recovering_history() -> pd.DataFrame:
    values = [120.0 - 0.1 * n for n in range(230)]
    last = values[-1]
    values.extend(last + 0.2 * n for n in range(1, 31))
    return pd.DataFrame({"close": values})


def test_breadth_classifies_broad_uptrend_as_risk_on() -> None:
    histories = {str(i): _history(50.0, 0.1) for i in range(160)}
    result = SaudiMarketBreadth(min_eligible_symbols=150).evaluate(histories)
    assert result.allowed is True
    assert result.state == "RISK_ON"
    assert result.eligible_symbols == 160
    assert result.pct_above_ema50 == 100.0
    assert result.pct_above_ema200 == 100.0


def test_breadth_classifies_recovery_market() -> None:
    histories: dict[str, pd.DataFrame] = {}
    # 60 long-term uptrends + 40 recent recoveries + 60 downtrends yields
    # strong EMA50 breadth, ~37.5% EMA200 breadth, and positive median momentum.
    for i in range(60):
        histories[f"up-{i}"] = _history(50.0, 0.1)
    for i in range(40):
        histories[f"recovery-{i}"] = _recovering_history()
    for i in range(60):
        histories[f"down-{i}"] = _history(100.0, -0.05)

    result = SaudiMarketBreadth(min_eligible_symbols=150).evaluate(histories)
    assert result.allowed is True
    assert result.state == "RECOVERY"
    assert result.pct_above_ema50 == 62.5
    assert result.pct_above_ema200 == 37.5
    assert result.median_mom20_pct is not None and result.median_mom20_pct > 0


def test_breadth_classifies_broad_downtrend_as_risk_off() -> None:
    histories = {str(i): _history(100.0, -0.1) for i in range(160)}
    result = SaudiMarketBreadth(min_eligible_symbols=150).evaluate(histories)
    assert result.allowed is False
    assert result.state == "RISK_OFF"


def test_breadth_fails_closed_when_history_sample_is_too_small() -> None:
    histories = {str(i): _history(50.0, 0.1) for i in range(50)}
    result = SaudiMarketBreadth(min_eligible_symbols=150).evaluate(histories)
    assert result.allowed is False
    assert result.state == "RISK_OFF"
    assert result.eligible_symbols == 50
    assert "insufficient" in result.note
