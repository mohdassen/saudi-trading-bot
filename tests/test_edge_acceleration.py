from saudi_trading_bot.edge_acceleration import _clean_trade, _evidence


def test_clean_trade_requires_risk_and_strategy():
    assert _clean_trade({"initial_risk_sar": 100, "pnl_sar": 50, "strategy": "x"})
    assert not _clean_trade({"initial_risk_sar": 0, "pnl_sar": 50, "strategy": "x"})
    assert not _clean_trade({"initial_risk_sar": 100, "pnl_sar": 50, "strategy": ""})


def test_evidence_counts_only_clean_closed_trades():
    payload = {
        "closed": [
            {
                "initial_risk_sar": 100,
                "pnl_sar": 50,
                "strategy": "momentum",
                "entry_regime": "RISK_ON",
                "mae_r": -0.3,
                "mfe_r": 0.8,
            },
            {"initial_risk_sar": 0, "pnl_sar": -20, "strategy": "legacy"},
        ],
        "positions": {"2290": {}},
        "pending": {"7200": {}},
    }
    result = _evidence("Momentum", payload, 30)
    assert result["clean_closed"] == 1
    assert result["raw_closed"] == 2
    assert result["open_positions"] == 1
    assert result["pending"] == 1
    assert result["regime_coverage_pct"] == 100.0
    assert result["mae_coverage_pct"] == 100.0
    assert result["mfe_coverage_pct"] == 100.0
