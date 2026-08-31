from saudi_trading_bot.disclosures.saudi_exchange import classify_disclosure


def test_positive_disclosure():
    d = classify_disclosure("تعلن الشركة عن ارتفاع صافي الربح وتوزيع أرباح")
    assert d.score > 50 and d.label == "positive"


def test_negative_disclosure():
    d = classify_disclosure("تعلن الشركة عن صافي خسارة وانخفاض الإيرادات")
    assert d.score < 50 and d.label == "negative"


def test_mubasher_fallback_matches_company_name():
    from datetime import UTC, datetime

    from saudi_trading_bot.disclosures.saudi_exchange import SaudiExchangeDisclosures

    html = """
    <html><body>
      <h1>Market Announcements</h1>
      <a href="/news/123">Al Moammar Information Systems Co. announces a contract award</a>
      <a href="/news/124">Another Company announces its financial results</a>
    </body></html>
    """
    items = SaudiExchangeDisclosures.parse_mubasher(html)
    assert len(items) == 2
    items = [
        type(item)(item.symbol, item.title, item.url, datetime.now(UTC).isoformat())
        for item in items
    ]
    reader = SaudiExchangeDisclosures("", "unused.json")
    impact = reader.impact_for("7200", items, "AL MOAMMAR INFORMATION SYSTEMS CO.")
    assert impact is not None
    assert impact.label == "positive"
