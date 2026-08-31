from saudi_trading_bot.data.tasi import TasiRegimeVerifier


def test_marketscreener_parser() -> None:
    html = """
    <html><body>
    <h1>Quotes Tadawul All Share</h1>
    <h2>TASI</h2>
    <div>Market Closed - Saudi Arabian S.E. 16:05:01 31/08/2026 +03</div>
    <div>11,127.08 PTS</div>
    <div>Moving average 100 days</div>
    <div>11,005.01</div>
    </body></html>
    """
    close, ma100 = TasiRegimeVerifier._marketscreener_from_html(html)
    assert close == 11127.08
    assert ma100 == 11005.01


def test_mubasher_parser() -> None:
    html = """
    <html><body>
    <h1>Tadawul All Share Index (TASI)</h1>
    <div>Last update: Monday, August 31</div>
    <div>11,127.08</div>
    </body></html>
    """
    assert TasiRegimeVerifier._mubasher_close_from_html(html) == 11127.08


def test_verified_regime_passes_with_matching_sources() -> None:
    verifier = TasiRegimeVerifier(max_reference_gap_pct=1.5)
    verifier.fetch_primary = lambda: (11127.08, 11005.01)
    verifier.fetch_reference_close = lambda: 11127.08

    result = verifier.evaluate()

    assert result.allowed is True
    assert result.gap_pct == 0.0
    assert result.market_ma == 11005.01
    assert "TASI verified" in result.note


def test_verified_regime_blocks_below_market_ma() -> None:
    verifier = TasiRegimeVerifier(max_reference_gap_pct=1.5)
    verifier.fetch_primary = lambda: (10950.0, 11005.01)
    verifier.fetch_reference_close = lambda: 10950.0

    result = verifier.evaluate()

    assert result.allowed is False
    assert "below MA100" in result.note


def test_verified_regime_fails_closed_on_source_mismatch() -> None:
    verifier = TasiRegimeVerifier(max_reference_gap_pct=1.5)
    verifier.fetch_primary = lambda: (10000.0, 9900.0)
    verifier.fetch_reference_close = lambda: 11127.08

    result = verifier.evaluate()

    assert result.allowed is False
    assert result.gap_pct is not None and result.gap_pct > 1.5
    assert "source mismatch" in result.note
