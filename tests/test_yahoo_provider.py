from saudi_trading_bot.data.yahoo import YahooSaudiProvider


def test_ticker_suffix_is_not_duplicated():
    provider = YahooSaudiProvider(".SR")
    assert provider.ticker_for("1120") == "1120.SR"
    assert provider.ticker_for("1120.SR") == "1120.SR"
    assert provider.ticker_for("^TASI.SR") == "^TASI.SR"
