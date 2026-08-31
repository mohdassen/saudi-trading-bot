from saudi_trading_bot.sharia.alrajhi import parse_compliant_main_market


def test_parse_alrajhi_compliant_section():
    rows = "".join(f"<div>{1000+i}</div><div>COMPANY {i}</div>" for i in range(60))
    html = f"""
    <html><body>
      <p>To view the list of Sharia Compliant and Non-Compliant companies Q1-2026</p>
      <a>Main Market (TASI) - Compliant</a><a>Main Market (TASI) - Non-Compliant</a>
      <h3>Main Market (TASI) - Compliant</h3>
      {rows}
      <h3>Main Market (TASI) - Non-Compliant</h3>
      <div>9999</div><div>NOPE</div>
    </body></html>
    """
    df, period = parse_compliant_main_market(html)
    assert period == "Q1-2026"
    assert len(df) == 60
    assert "9999" not in set(df["symbol"])
