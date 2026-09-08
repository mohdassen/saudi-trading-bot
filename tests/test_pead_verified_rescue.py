from datetime import datetime
from zoneinfo import ZoneInfo

from saudi_trading_bot.pead_verified_rescue import (
    _detail_published_at,
    _recent_event,
)
from saudi_trading_bot.disclosures.saudi_exchange import Announcement

RIYADH = ZoneInfo("Asia/Riyadh")


def test_detail_published_at_uses_announcement_page_date_not_listing_context():
    html = """
    <html><body>
      <h1>The National Company for Glass Industries "Zoujaj" announces its
      Interim Financial results</h1>
      <div>02 August 2026 08:18 AM</div>
    </body></html>
    """

    published = _detail_published_at(html)

    assert published == datetime(2026, 8, 2, 8, 18, tzinfo=RIYADH)


def test_old_zoujaj_event_is_not_recent_on_september_8():
    event = Announcement(
        symbol="2150",
        title="Zoujaj financial results",
        url="https://english.mubasher.info/news/example",
        fetched_at="2026-09-08T18:00:00+00:00",
        published_at="2026-08-02T08:18:00+03:00",
    )
    now = datetime(2026, 9, 8, 21, 0, tzinfo=RIYADH)

    assert not _recent_event(event, now, 12)


def test_recent_verified_event_is_accepted():
    event = Announcement(
        symbol="9999",
        title="Example financial results",
        url="https://english.mubasher.info/news/example",
        fetched_at="2026-09-08T18:00:00+00:00",
        published_at="2026-09-07T08:18:00+03:00",
    )
    now = datetime(2026, 9, 8, 21, 0, tzinfo=RIYADH)

    assert _recent_event(event, now, 12)
