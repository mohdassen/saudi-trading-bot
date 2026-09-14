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


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _label_key(label: str) -> str | None:
    normalized = _norm(label)
    if any(
        token in normalized
        for token in (
            "sales revenue",
            "revenue sales",
            "total revenue",
            "total revenues",
            "revenues",
        )
    ):
        return "revenue"
    if any(
        token in normalized
        for token in (
            "operational profit",
            "operating profit",
            "operating income",
            "profit from operations",
            "income from operations",
        )
    ):
        return "operating"
    if (
        ("net profit" in normalized or "net income" in normalized)
        and "per share" not in normalized
        and "margin" not in normalized
    ):
        return "net"
    return None


def _header_role(text: str) -> str | None:
    normalized = _norm(text)
    if not normalized:
        return None
    if any(
        token in normalized
        for token in (
            "current quarter",
            "current period",
            "current year",
            "current",
        )
    ):
        return "current"
    if any(
        token in normalized
        for token in (
            "similar quarter for previous year",
            "similar quarter previous year",
            "similar period for previous year",
            "similar period previous year",
            "corresponding period previous year",
            "same quarter previous year",
            "same period previous year",
            "previous year",
        )
    ):
        return "previous_year"
    if any(
        token in normalized
        for token in (
            "previous quarter",
            "previous period",
            "preceding quarter",
        )
    ):
        return "previous_quarter"
    return None


def _table_column_map(table) -> dict[str, int]:
    best: dict[str, int] = {}
    for tr in table.find_all("tr"):
        cells = [" ".join(cell.stripped_strings) for cell in tr.find_all(["th", "td"])]
        if len(cells) < 2:
            continue
        candidate: dict[str, int] = {}
        for index, cell in enumerate(cells):
            role = _header_role(cell)
            if role is not None and role not in candidate:
                candidate[role] = index
        if "current" in candidate and "previous_year" in candidate:
            best = candidate
            break
    return best


def _values_from_cells(
    cells: list[str],
    columns: dict[str, int],
) -> list[float | None]:
    def value_for(role: str, fallback: int | None) -> float | None:
        index = columns.get(role, fallback)
        if index is None or index >= len(cells):
            return None
        return _number(cells[index])

    return [
        value_for("current", 1),
        value_for("previous_year", 2),
        value_for("previous_quarter", 4 if len(cells) > 4 else None),
    ]


def _rows(html: str) -> dict[str, list[float | None]]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, list[float | None]] = {}
    for table in soup.find_all("table"):
        columns = _table_column_map(table)
        for tr in table.find_all("tr"):
            cells = [
                " ".join(cell.stripped_strings)
                for cell in tr.find_all(["th", "td"])
            ]
            if len(cells) < 3:
                continue
            key = _label_key(cells[0])
            if key is None:
                continue
            values = _values_from_cells(cells, columns)
            if values[0] is not None:
                found[key] = values
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
