from datetime import date
from pathlib import Path

from saudi_trading_bot.sharia.filter import StrictShariaFilter


def test_unknown_is_blocked(tmp_path: Path):
    p = tmp_path / "allow.csv"
    p.write_text("symbol,status,source,source_period,source_checked_at\n1120,allowed,test,Q1-2026,2026-08-30\n", encoding="utf-8")
    f = StrictShariaFilter(p, max_source_check_age_days=14, block_unknown=True)
    assert not f.check("9999", today=date(2026, 9, 1)).allowed


def test_stale_source_check_is_blocked(tmp_path: Path):
    p = tmp_path / "allow.csv"
    p.write_text("symbol,status,source,source_period,source_checked_at\n1120,allowed,test,Q1-2026,2026-07-01\n", encoding="utf-8")
    f = StrictShariaFilter(p, max_source_check_age_days=14, block_unknown=True)
    d = f.check("1120", today=date(2026, 9, 1))
    assert d.stale and not d.allowed
