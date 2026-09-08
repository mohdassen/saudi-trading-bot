from saudi_trading_bot.data.argaam import ArgaamSaudiProvider


def test_parse_symbol_map() -> None:
    html = '''<html><a href="/en/tadawul/tasi/petro-rabigh">2380 - PETRO RABIGH</a></html>'''
    result = ArgaamSaudiProvider.parse_symbol_map(html)
    assert result["2380"].endswith("/en/tadawul/tasi/petro-rabigh/chart?isNoHeaderFooter=true")


def test_parse_chart_daily_ohlc() -> None:
    html = '''
    <table>
      <thead><tr><th>Date</th><th>Price</th><th>Change</th><th>Change (%)</th><th>Volume</th><th>Turnover</th><th>Open</th><th>High</th><th>Low</th></tr></thead>
      <tbody>
        <tr><td>08/09/2026</td><td>18.35</td><td>0.20</td><td>1.10%</td><td>1,234,567</td><td>22,000,000</td><td>18.10</td><td>18.60</td><td>17.95</td></tr>
      </tbody>
    </table>
    '''
    frame = ArgaamSaudiProvider.parse_chart(html)
    assert frame.index[-1].date().isoformat() == "2026-09-08"
    assert frame.iloc[-1]["close"] == 18.35
    assert frame.iloc[-1]["open"] == 18.10
    assert frame.iloc[-1]["high"] == 18.60
    assert frame.iloc[-1]["low"] == 17.95
    assert frame.iloc[-1]["volume"] == 1234567
