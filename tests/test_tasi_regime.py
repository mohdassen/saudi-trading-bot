from datetime import datetime, timezone

import pandas as pd

from saudi_trading_bot.data.tasi import TasiRegimeVerifier


def test_reference_close_parser() -> None:
    html = """
    <html><body>
    <h1>Tadawul All Share Index (TASI) (TASI)</h1>
    <div>Last update: Monday, August 31</div>
    <div>11,127.08</div>
    <div>Open 11,163.76</div>
    </body></html>
    """
    assert TasiRegimeVerifier._reference_close_from_html(html) == 11127.08


def test_yahoo_payload_parser() -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1_700_000_000, 1_700_086_400],
                    "indicators": {
                        "quote": [{"close": [11000.0, 11100.0]}],
                    },
                }
            ]
        }
    }
    frame = TasiRegimeVerifier._history_from_payload(payload)
    assert list(frame["close"]) == [11000.0, 11100.0]


def test_verified_regime_passes_with_matching_sources() -> None:
    verifier = TasiRegimeVerifier(max_reference_gap_pct=1.5)
    end = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    index = pd.date_range(end=end, periods=260, freq="B", tz="UTC")
    history = pd.DataFrame({"close": range(10800, 11060)}, index=index)
    verifier.fetch_history = lambda: history
    verifier.fetch_reference_close = lambda: 11060.0

    result = verifier.evaluate()

    assert result.allowed is True
    assert result.gap_pct == 0.0
    assert "verified" in result.note


def test_verified_regime_fails_closed_on_source_mismatch() -> None:
    verifier = TasiRegimeVerifier(max_reference_gap_pct=1.5)
    end = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
    index = pd.date_range(end=end, periods=260, freq="B", tz="UTC")
    history = pd.DataFrame({"close": [10000.0] * 260}, index=index)
    verifier.fetch_history = lambda: history
    verifier.fetch_reference_close = lambda: 11127.08

    result = verifier.evaluate()

    assert result.allowed is False
    assert result.gap_pct is not None and result.gap_pct > 1.5
    assert "source mismatch" in result.note
