from saudi_trading_bot.disclosures.financials import parse_financial_result


def test_parse_official_financial_result_and_score():
    html = """
    <html><body>
      <div>08/09/2026 08:15:00</div>
      <table>
        <tr>
          <th>Element List</th><th>Current Quarter</th>
          <th>Similar quarter for previous year</th><th>%Change</th>
          <th>Previous Quarter</th><th>% Change</th>
        </tr>
        <tr><td>Sales/Revenue</td><td>200</td><td>160</td><td>25</td><td>180</td><td>11</td></tr>
        <tr>
          <td>Operational Profit (Loss)</td><td>60</td><td>40</td><td>50</td>
          <td>50</td><td>20</td>
        </tr>
        <tr>
          <td>Net Profit (Loss) Attributable to Shareholders of the Issuer</td>
          <td>120</td><td>80</td><td>50</td><td>100</td><td>20</td>
        </tr>
      </table>
    </body></html>
    """
    result = parse_financial_result(
        html,
        "7200",
        "https://www.saudiexchange.sa/example",
    )
    assert result is not None
    assert result.symbol == "7200"
    assert result.revenue_yoy_pct == 25.0
    assert result.operating_yoy_pct == 50.0
    assert result.net_yoy_pct == 50.0
    assert result.net_qoq_pct == 20.0
    assert result.earnings_score >= 90
    assert result.published_at.startswith("2026-09-08T08:15:00")


def test_turnaround_gets_positive_earnings_signal():
    html = """
    <html><body>
      <div>07/09/2026 16:01:00</div>
      <table>
        <tr><th>Element List</th><th>Current</th><th>Previous year</th></tr>
        <tr><td>Sales/Revenue</td><td>140</td><td>120</td></tr>
        <tr><td>Operational Profit (Loss)</td><td>20</td><td>-5</td></tr>
        <tr>
          <td>Net Profit (Loss) Attributable to Shareholders of the Issuer</td>
          <td>12</td><td>-8</td>
        </tr>
      </table>
    </body></html>
    """
    result = parse_financial_result(html, "1320", "https://www.saudiexchange.sa/x")
    assert result is not None
    assert result.net_current == 12
    assert result.net_previous_year == -8
    assert result.earnings_score >= 70


def test_parser_follows_reordered_columns_and_alias_labels():
    html = """
    <html><body>
      <table>
        <tr>
          <th>Metric</th><th>Previous Quarter</th><th>% Change</th>
          <th>Current Period</th><th>Same Period Previous Year</th>
        </tr>
        <tr><td>Total Revenues</td><td>180</td><td>11</td><td>220</td><td>200</td></tr>
        <tr><td>Operating Income</td><td>45</td><td>22</td><td>55</td><td>40</td></tr>
        <tr><td>Net Income</td><td>90</td><td>33</td><td>120</td><td>80</td></tr>
      </table>
    </body></html>
    """
    result = parse_financial_result(html, "2381", "https://english.mubasher.info/x")
    assert result is not None
    assert result.net_current == 120
    assert result.net_previous_year == 80
    assert result.net_previous_quarter == 90
    assert result.revenue_yoy_pct == 10.0
    assert result.operating_yoy_pct == 37.5
    assert result.net_yoy_pct == 50.0
    assert result.net_qoq_pct == 33.333


def test_parser_rejects_non_financial_numeric_table():
    html = """
    <table>
      <tr><th>Metric</th><th>Current Period</th><th>Previous Year</th></tr>
      <tr><td>Share Price</td><td>44</td><td>38</td></tr>
      <tr><td>Volume</td><td>100000</td><td>80000</td></tr>
    </table>
    """
    assert parse_financial_result(html, "9999", "https://example.com") is None
