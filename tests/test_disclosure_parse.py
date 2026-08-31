from saudi_trading_bot.disclosures.saudi_exchange import SaudiExchangeDisclosures


def test_parse_sibling_symbol_from_saudi_exchange_style_html():
    html = """
    <html><body>
      <div>
        <h3>National Industrialization Co. Announces Resignation and Appointment of a CEO</h3>
        <span>2060</span><span>TASNEE</span>
      </div>
      <div>
        <a href="/details/123">Saudi Steel Pipe Co. Announces a contract sign off</a>
        <span>1320</span><span>SSP</span>
      </div>
    </body></html>
    """
    items = SaudiExchangeDisclosures.parse(html)
    pairs = {(x.symbol, x.title) for x in items}
    assert any(s == "2060" for s, _ in pairs)
    assert any(s == "1320" for s, _ in pairs)
