from saudi_trading_bot.regime_tagging import attach_entry_regime


def test_attach_entry_regime_only_once() -> None:
    trade = {}
    attach_entry_regime(trade, "RISK_OFF")
    attach_entry_regime(trade, "RISK_ON")
    assert trade["entry_regime"] == "RISK_OFF"
