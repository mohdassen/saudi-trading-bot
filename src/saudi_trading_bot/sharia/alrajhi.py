from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import unicodedata

import pandas as pd
import requests
from bs4 import BeautifulSoup


SOURCE_NAME = "Al Rajhi Capital Shariah Group"
DEFAULT_URL = "https://alrajhi-capital.sa/shariah-group/about-us-guidelines"


@dataclass(frozen=True)
class ShariaSyncResult:
    symbols: int
    period: str
    checked_on: date
    url: str


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u200b", "").strip()
    return re.sub(r"\s+", " ", text)


def parse_compliant_main_market(html: str) -> tuple[pd.DataFrame, str]:
    soup = BeautifulSoup(html, "html.parser")
    tokens = [_clean(x) for x in soup.stripped_strings if _clean(x)]
    full_text = " ".join(tokens)
    m = re.search(r"Q([1-4])[-\s]?(20\d{2})", full_text, re.I)
    period = f"Q{m.group(1)}-{m.group(2)}" if m else "unknown"

    compliant_indexes = [i for i, t in enumerate(tokens) if "Main Market (TASI) - Compliant" in t]
    if not compliant_indexes:
        raise ValueError("Could not locate TASI compliant section")
    start = compliant_indexes[-1] + 1
    stop = len(tokens)
    for i in range(start, len(tokens)):
        if "Main Market (TASI) - Non-Compliant" in tokens[i]:
            stop = i
            break

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    i = start
    while i < stop - 1:
        symbol = tokens[i].replace("​", "").strip()
        if re.fullmatch(r"\d{4}", symbol):
            name = tokens[i + 1]
            if symbol not in seen and not re.fullmatch(r"\d{4}", name):
                seen.add(symbol)
                rows.append({"symbol": symbol, "name": name})
                i += 2
                continue
        i += 1

    if len(rows) < 50:
        raise ValueError(f"Parsed only {len(rows)} symbols; refusing suspicious Sharia update")
    return pd.DataFrame(rows), period


def sync_allowlist(
    output: str | Path,
    url: str = DEFAULT_URL,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> ShariaSyncResult:
    client = session or requests.Session()
    r = client.get(url, timeout=timeout, headers={"User-Agent": "SaudiTradingBot/0.2 (+paper-research)"})
    r.raise_for_status()
    df, period = parse_compliant_main_market(r.text)
    checked = date.today()
    df["status"] = "allowed"
    df["source"] = SOURCE_NAME
    df["source_period"] = period
    df["source_checked_at"] = checked.isoformat()
    df["source_url"] = url
    df["notes"] = "Published compliant list; bot does not issue a Sharia ruling."
    cols = ["symbol", "name", "status", "source", "source_period", "source_checked_at", "source_url", "notes"]
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    df[cols].to_csv(tmp, index=False)
    tmp.replace(out)
    return ShariaSyncResult(len(df), period, checked, url)
