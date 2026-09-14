from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from saudi_trading_bot.disclosures.saudi_exchange import Announcement
from saudi_trading_bot.pead_verified_rescue import (
    _fetch_verified_events,
    _save_verified_events,
)

RIYADH = ZoneInfo("Asia/Riyadh")


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_cached_verified_event_rehydrates_financial_snapshot(monkeypatch, tmp_path):
    now = datetime(2026, 9, 14, 21, 0, tzinfo=RIYADH)
    detail_url = "https://english.mubasher.info/news/123/result"
    cache_path = tmp_path / "verified.json"
    event = Announcement(
        symbol="2381",
        title="Company announces financial results",
        url=detail_url,
        fetched_at=now.isoformat(),
        published_at=datetime(2026, 9, 14, 8, 1, tzinfo=RIYADH).isoformat(),
    )
    _save_verified_events(cache_path, [event], now)

    detail_html = """
    <html><body>
      <div>14 September 2026 08:01 AM</div>
      <table>
        <tr>
          <th>Metric</th><th>Previous Quarter</th>
          <th>Current Period</th><th>Same Period Previous Year</th>
        </tr>
        <tr><td>Total Revenues</td><td>180</td><td>220</td><td>200</td></tr>
        <tr><td>Operating Income</td><td>45</td><td>55</td><td>40</td></tr>
        <tr><td>Net Income</td><td>90</td><td>120</td><td>80</td></tr>
      </table>
    </body></html>
    """

    def fake_get(url, **kwargs):
        if url == "https://listing.invalid":
            raise requests.ConnectionError("listing unavailable")
        assert url == detail_url
        return _Response(detail_html)

    monkeypatch.setattr(requests, "get", fake_get)

    events, snapshots, errors, source = _fetch_verified_events(
        "https://listing.invalid",
        cache_path,
        now,
        [("2381", "Test Company")],
        5,
    )

    assert source == "verified-cache-rehydrated"
    assert [item.symbol for item in events] == ["2381"]
    assert detail_url in snapshots
    assert snapshots[detail_url].net_current == 120
    assert snapshots[detail_url].net_previous_year == 80
    assert snapshots[detail_url].net_previous_quarter == 90
    assert errors and "listing refresh failed" in errors[0]
