from saudi_trading_bot.disclosures.saudi_exchange import classify_disclosure


def test_positive_disclosure():
    d = classify_disclosure("تعلن الشركة عن ارتفاع صافي الربح وتوزيع أرباح")
    assert d.score > 50 and d.label == "positive"


def test_negative_disclosure():
    d = classify_disclosure("تعلن الشركة عن صافي خسارة وانخفاض الإيرادات")
    assert d.score < 50 and d.label == "negative"
