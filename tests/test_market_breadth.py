import pandas as pd

from saudi_trading_bot.data.market_breadth import SaudiMarketBreadth


def _history(start: float, step: float, rows: int = 260) -> pd.DataFrame:
    close = [start + step * i for i in range(rows)]
    return pd.DataFrame({"close": close})


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
    for i in range(160):
        if i < 100:
            # Strong recent recovery, but not enough time to repair EMA200 fully.
            values = [100.0 - 0.12 * n for n in range(200)]
            last = values[-1]
            values.extend(last + 0.8 * n for n in range(1, 61))
            histories[str(i)] = pd.DataFrame({"close": values})
        else:
            histories[str(i)] = _history(100.0, -0.05)

    result = SaudiMarketBreadth(min_eligible_symbols=150).evaluate(histories)
    assert result.allowed is True
    assert result.state in {"RECOVERY", "RISK_ON"}
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
