from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

RIYADH = ZoneInfo("Asia/Riyadh")


@dataclass(frozen=True)
class EarningsSnapshot:
    symbol: str
    url: str
    published_at: str
    revenue_yoy_pct: float | None
    operating_yoy_pct: float | None
    net_yoy_pct: float | None
    net_qoq_pct: float | None
    net_current: float | None
    net_previous_year: float | None
    net_previous_quarter: float | None
    earnings_score: float


def _number(text: str) -> float | None:
    value = " ".join(str(text).split()).strip()
    if not value or value in {"-", "—", "–", "N/A", "n/a"}:
        return None
    negative = value.startswith("(") and value.endswith(")")
    value = value.strip("()").replace(",", "").replace("%", "")
    value = re.sub(r"[^0-9.+-]", "", value)
    if not value or value in {"+", "-", "."}:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return -parsed if negative else parsed


def _change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    if previous == 0:
        return 100.0 if current > 0 else (-100.0 if current < 0 else 0.0)
    if previous < 0 < current:
        return 100.0
    if previous > 0 > current:
        return -100.0
    return (current / abs(previous) - (1.0 if previous > 0 else -1.0)) * 100.0


def _published_at(text: str) -> str:
    match = re.search(
        r"(?<!\d)(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2}):(\d{2})(?!\d)",
        text,
    )
    if match:
        day, month, year, hour, minute, second = map(int, match.groups())
        return datetime(year, month, day, hour, minute, second, tzinfo=RIYADH).isoformat()

    match = re.search(r"(?<!\d)(\d{2})/(\d{2})/(\d{4})(?!\d)", text)
    if match:
        day, month, year = map(int, match.groups())
        return datetime(year, month, day, tzinfo=RIYADH).isoformat()
    return ""


def _rows(html: str) -> dict[str, list[float | None]]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, list[float | None]] = {}
    for tr in soup.find_all("tr"):
        cells = [" ".join(cell.stripped_strings) for cell in tr.find_all(["th", "td"])]
        if len(cells) < 3:
            continue
        label = cells[0].lower()
        values = [
            _number(cells[1]),
            _number(cells[2]),
            _number(cells[4]) if len(cells) > 4 else None,
        ]
        if "sales/revenue" in label or "sales / revenue" in label:
            found["revenue"] = values
        elif "operational profit" in label or "operating profit" in label:
            found["operating"] = values
        elif "net profit" in label and "shareholders" in label:
            found["net"] = values
    return found


def parse_financial_result(html: str, symbol: str, url: str) -> EarningsSnapshot | None:
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.stripped_strings)
    rows = _rows(html)
    net = rows.get("net")
    if not net or net[0] is None:
        return None

    revenue = rows.get("revenue", [None, None, None])
    operating = rows.get("operating", [None, None, None])
    current_net, previous_year_net, previous_quarter_net = (net + [None, None, None])[:3]

    revenue_yoy = _change(revenue[0], revenue[1])
    operating_yoy = _change(operating[0], operating[1])
    net_yoy = _change(current_net, previous_year_net)
    net_qoq = _change(current_net, previous_quarter_net)

    score = 0.0
    if current_net is not None and current_net > 0:
        score += 15.0
    else:
        score -= 30.0

    if previous_year_net is not None and previous_year_net <= 0 < float(current_net or 0):
        score += 35.0
    elif net_yoy is not None:
        if net_yoy >= 25:
            score += 35.0
        elif net_yoy >= 10:
            score += 25.0
        elif net_yoy >= 0:
            score += 10.0
        else:
            score -= 25.0

    if revenue_yoy is not None:
        score += 20.0 if revenue_yoy >= 10 else (10.0 if revenue_yoy >= 0 else -10.0)
    if operating_yoy is not None:
        score += 20.0 if operating_yoy >= 10 else (10.0 if operating_yoy >= 0 else -15.0)
    if net_qoq is not None:
        score += 10.0 if net_qoq >= 0 else (-10.0 if net_qoq <= -20 else 0.0)

    return EarningsSnapshot(
        symbol=str(symbol),
        url=url,
        published_at=_published_at(text),
        revenue_yoy_pct=None if revenue_yoy is None else round(revenue_yoy, 3),
        operating_yoy_pct=None if operating_yoy is None else round(operating_yoy, 3),
        net_yoy_pct=None if net_yoy is None else round(net_yoy, 3),
        net_qoq_pct=None if net_qoq is None else round(net_qoq, 3),
        net_current=current_net,
        net_previous_year=previous_year_net,
        net_previous_quarter=previous_quarter_net,
        earnings_score=round(max(0.0, min(100.0, score)), 2),
    )


class SaudiFinancialResultReader:
    """Official Saudi Exchange financial-result reader with persistent free cache."""

    def __init__(self, cache_file: str | Path, timeout: int = 20):
        self.cache_file = Path(cache_file)
        self.timeout = int(timeout)
        self._cache = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.cache_file.exists():
            return {}
        try:
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
            return dict(payload.get("results", {}))
        except (OSError, TypeError, ValueError):
            return {}

    def _save(self) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "results": self._cache,
        }
        self.cache_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def read(self, symbol: str, url: str) -> EarningsSnapshot | None:
        if "saudiexchange.sa" not in str(url):
            return None
        cached = self._cache.get(url)
        if cached:
            try:
                return EarningsSnapshot(**cached)
            except TypeError:
                pass

        try:
            response = requests.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": "SaudiTradingBot/0.3 (+paper-research)"},
            )
            response.raise_for_status()
            result = parse_financial_result(response.text, symbol, url)
        except (requests.RequestException, ValueError):
            return None

        if result is not None:
            self._cache[url] = asdict(result)
            self._save()
        return result
