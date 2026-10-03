from saudi_trading_bot.midtrade_forensics import _risk_bucket


def test_profit_protection_candidate():
    assert _risk_bucket(-0.1, 1.2, 4) == "PROFIT_PROTECTION_CANDIDATE"


def test_weak_follow_through_after_five_bars():
    assert _risk_bucket(-0.5, 0.1, 6) == "WEAK_FOLLOW_THROUGH"


def test_loss_pattern_risk():
    assert _risk_bucket(-0.8, 0.3, 3) == "LOSS_PATTERN_RISK"


def test_developing():
    assert _risk_bucket(-0.2, 0.6, 3) == "DEVELOPING"
