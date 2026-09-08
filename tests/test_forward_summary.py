from saudi_trading_bot.forward_summary import _queued_event_audit


def test_queued_event_audit_selects_latest_event_per_queued_symbol():
    health = {
        "queued": ["2150"],
        "source": "https://english.mubasher.info/news/sa/now/announcements",
    }
    event_cache = {
        "events": [
            {
                "symbol": "2150",
                "title": "Old financial results",
                "published_at": "2026-08-01T08:00:00+03:00",
                "url": "https://example.test/old",
            },
            {
                "symbol": "2150",
                "title": "Latest financial results",
                "published_at": "2026-09-07T08:00:00+03:00",
                "url": "https://example.test/latest",
            },
            {
                "symbol": "2380",
                "title": "Other company",
                "published_at": "2026-09-08T08:00:00+03:00",
                "url": "https://example.test/other",
            },
        ]
    }

    audit = _queued_event_audit(health, event_cache)

    assert len(audit) == 1
    assert audit[0]["symbol"] == "2150"
    assert audit[0]["title"] == "Latest financial results"
    assert audit[0]["published_at"] == "2026-09-07T08:00:00+03:00"
    assert audit[0]["discovery_source"].startswith("https://english.mubasher.info")
    assert audit[0]["mode"] == ""


def test_queued_event_audit_prefers_verified_health_provenance():
    health = {
        "source": "verified-source",
        "queued_events": [
            {
                "symbol": "9999",
                "mode": "fundamental+price",
                "title": "Verified results",
                "published_at": "2026-09-07T08:00:00+03:00",
                "url": "https://example.test/verified",
            }
        ],
    }

    audit = _queued_event_audit(health, {"events": []})

    assert audit[0]["symbol"] == "9999"
    assert audit[0]["mode"] == "fundamental+price"
    assert audit[0]["discovery_source"] == "verified-source"


def test_queued_event_audit_keeps_symbol_even_if_event_cache_is_missing():
    audit = _queued_event_audit(
        {"queued": ["2150"], "source": "cache"},
        {"events": []},
    )

    assert audit == [
        {
            "symbol": "2150",
            "title": "",
            "published_at": "",
            "url": "",
            "discovery_source": "cache",
            "mode": "",
        }
    ]
