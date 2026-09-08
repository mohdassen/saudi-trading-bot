# Durable forward-state design

GitHub Actions cache is treated as a performance cache, not the source of truth for forward evidence.

The bot now has a portable JSON snapshot format (`state_persistence.py`) covering paper and shadow portfolios plus decision artifacts. The workflow integration should restore this durable snapshot before scans and refresh it after successful scans.

This prevents a cache-version change or cache miss from resetting closed-trade evidence.
