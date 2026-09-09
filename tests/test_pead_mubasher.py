from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from saudi_trading_bot.pead_mubasher import parse_mubasher_financial_events

RIYADH = ZoneInfo("Asia/Riyadh")


def test_discovers_financial_event_without_publisher_literal():
    now = datetime(2026, 9, 9, 12, 0, tzinfo=RIYADH)
    html = """
    <article class="news-card">
      <span>2 hours ago</span>
      <a href="/news/4652451/srmg-financial-results/news">
        Saudi Research and Media Group announces its Interim Financial results
        for the Period Ending on 2026-06-30 (Six Months)
      </a>
    </article>
    """
    events = parse_mubasher_financial_events(
        html,
        now,
        [("4210", "Saudi Research and Media Group")],
    )
    assert len(events) == 1
    assert events[0].symbol == "4210"
    assert datetime.fromisoformat(events[0].published_at) == now - timedelta(hours=2)


def test_rejects_financial_title_when_company_cannot_be_resolved():
    now = datetime(2026, 9, 9, 12, 0, tzinfo=RIYADH)
    html = """
    <article><span>today 09:00 AM</span>
      <a href="/news/123/unknown/news">Unknown Co. announces Interim Financial results</a>
    </article>
    """
    assert not parse_mubasher_financial_events(html, now, [("4210", "SRMG")])
