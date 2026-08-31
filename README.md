# Saudi Trading Bot 🇸🇦 — Zero-Cost, Paper-First

A completely independent Saudi-market-only research bot. It does **not** reuse code, settings, secrets, state, positions, or strategy history from any US-market bot.

## Non-negotiable zero-cost policy

This project is designed to run with **0 SAR paid data/services now and later**.

- Market history: free delayed Yahoo Finance adapter (`*.SR`, plus `^TASI.SR`).
- Outage fallback: local CSV last-known-good cache.
- Sharia list: public Al Rajhi Capital Shariah Group compliant list; fail-closed.
- Issuer disclosures: public Saudi Exchange announcements; cached best-effort reader.
- Indicators/backtest/paper engine: local Python only.
- Telegram Bot API: free.
- CI/scheduled execution: GitHub Actions only on a **public** repository using standard hosted runners. Both workflows contain a hard guard and refuse to run on a private repository.
- No paid API key is accepted by configuration.
- No broker/execution adapter exists.

If a free external source becomes unavailable, the bot either uses its local cache or blocks that component. It never silently upgrades to a paid service. If the repository is private, hosted GitHub workflows are skipped by design to prevent possible metered usage.

## Strategy

Daily/Swing rather than scalping, because free Saudi market sources are delayed and are not appropriate for latency-sensitive execution.

Signal score combines:
- 35% Trend — EMA20/50/200 alignment.
- 30% Momentum — 20-day ROC, RSI, volume expansion.
- 20% Swing structure — proximity to 20/55-day highs.
- 15% disclosure impact — rule-based positive/neutral/negative classification.

A separate TASI regime gate blocks new Paper entries when TASI is below EMA200 or index data is unavailable.

## Sharia safety

The bot does not issue a fatwa. It only consumes an externally published compliant list. Unknown symbols or a source that has not been re-checked within the configured window are blocked.

Current seed symbols were verified against the Al Rajhi Capital page that currently publishes **Main Market (TASI) - Compliant Q1-2026**. `saudi-bot sync-sharia` refreshes the full compliant Main Market list automatically, rebuilds the scan universe from that list, and refuses suspicious parses.

## Paper lifecycle

1. Free data fetch → local cache.
2. Sharia PASS required.
3. TASI market regime evaluated.
4. Trend/Momentum/Swing/Disclosure score.
5. `WATCH` or `READY` state.
6. Paper position sizing with fixed risk and max-position caps.
7. Daily stop/target/max-hold exit simulation.
8. Telegram only on relevant state changes.
9. Backtest reports written to `artifacts/`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
cp .env.example .env
pytest -q
ruff check src tests
```

## Commands

```bash
# Refresh the public Sharia list; keeps last-known-good copy if best-effort mode fails
saudi-bot sync-sharia --best-effort

# Verify zero-cost safeguards and local readiness
saudi-bot doctor

# Paper scan without Telegram
saudi-bot scan

# Paper scan with Telegram alerts
saudi-bot scan --send

# Five-year strategy backtest over enabled universe
saudi-bot backtest
```

## Safety states

- `BLOCKED`: Sharia/data/risk gate failed.
- `IGNORE`: no edge.
- `WATCH`: setup forming or TASI regime blocks a would-be entry.
- `READY`: Paper candidate only; never a real order.

## Current implementation phase

`v0.2` is intentionally **Backtest + Shadow/Paper only**. Promotion to any real-money workflow is outside the codebase and is not planned until sufficient paper evidence exists.
