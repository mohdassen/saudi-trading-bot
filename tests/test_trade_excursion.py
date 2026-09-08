from saudi_trading_bot.trade_excursion import update_excursions


def test_update_excursions_tracks_best_and_worst_r() -> None:
    trade = {"entry_price": 100.0, "stop_price": 95.0}
    update_excursions(trade, high=110.0, low=97.5)
    update_excursions(trade, high=106.0, low=92.5)
    assert trade["mfe_r"] == 2.0
    assert trade["mae_r"] == -1.5
