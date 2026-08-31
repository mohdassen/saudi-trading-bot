import pandas as pd

from saudi_trading_bot.data.market_breadth import SaudiMarketBreadth


def _history(start: float, step: float, rows: int = 260) -> pd.DataFrame:
    close = [start + step * i for i in range(rows)]
    return pd.DataFrame({"close": close})


def test_breadth_passes_for_broad_uptrend() -> None:
    histories = {str(i): _history(50.0, 0.1) for i in range(160)}
    result = SaudiMarketBreadth(min_eligible_symbols=150).evaluate(histories)
    assert result.allowed is True
    assert result.eligible_symbols == 160
    assert result.pct_above_ema50 == 100.0
    assert result.pct_above_ema200 == 100.0


def test_breadth_blocks_for_broad_downtrend() -> None:
    histories = {str(i): _history(100.0, -0.1) for i in range(160)}
    result = SaudiMarketBreadth(min_eligible_symbols=150).evaluate(histories)
    assert result.allowed is False
    assert "BLOCK" in result.note


def test_breadth_fails_closed_when_history_sample_is_too_small() -> None:
    histories = {str(i): _history(50.0, 0.1) for i in range(50)}
    result = SaudiMarketBreadth(min_eligible_symbols=150).evaluate(histories)
    assert result.allowed is False
    assert result.eligible_symbols == 50
    assert "insufficient" in result.note
