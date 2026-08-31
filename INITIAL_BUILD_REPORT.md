# Saudi Trading Bot — Initial Build Report

Build: v0.2.0
Mode: Backtest + Shadow/Paper only
Market: Saudi Main Market (TASI) only
Cost policy: 0 SAR paid data/services

## Implemented

- Strict Saudi-only universe and `.SR` market adapter.
- Strict fail-closed Sharia allowlist sourced from the public Al Rajhi Capital Shariah Group page.
- Automatic full compliant-list refresh and universe rebuild.
- Free delayed daily market data adapter with last-known-good local cache fallback.
- TASI regime gate using `^TASI.SR` and EMA200.
- Trend/Momentum/Swing scoring engine.
- Saudi Exchange disclosure reader with official-page fallback, local cache, and rule-based impact classifier.
- Backtest engine with next-day-open entries, ATR stop, target, max-hold and friction assumptions.
- Paper portfolio with risk sizing, position caps, daily entry limits, stop/target/max-hold exits, slippage and commission assumptions.
- Telegram Arabic alerts with state-change deduplication.
- GitHub Actions CI and scheduled paper scans.
- Hard GitHub zero-cost guard: hosted workflows run only when the repository is public.
- No broker execution adapter and no real-money order path.

## Quality gates

- Unit/smoke tests: 11 passed.
- Python bytecode compilation: passed.
- `doctor`: zero-paid guard PASS; provider PASS; Sharia list PASS.
- Telegram: WARN until independent Telegram secrets are configured.
- Static forbidden-provider scan: no US-bot/broker/paid-provider dependency found.

## Known environment limitation during build

The build container has no outbound package/network access, so live market/download smoke tests could not be executed from this container. The code is intentionally resilient: external-source failures fall back to local cache where safe, and Sharia failures block rather than guess. GitHub Actions installs runtime dependencies on a normal internet-connected runner once the project is published in its own public repository.

## Promotion rule

Do not connect this codebase to real-money execution. First collect sufficient backtest and Shadow/Paper evidence and review drawdown, win rate, profit factor, sample size, and behavior across different TASI regimes.
