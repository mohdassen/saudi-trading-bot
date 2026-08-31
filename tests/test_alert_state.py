from datetime import datetime
from pathlib import Path

from saudi_trading_bot.models import Signal, SignalState
from saudi_trading_bot.notify.state import AlertState


def _s(state):
    return Signal("1120", state, 70, 70, 70, 70, 50, 100, 95, 110, 2, (), datetime.now())


def test_alert_only_changes(tmp_path: Path):
    a = AlertState(tmp_path / "state.json")
    assert a.changed(_s(SignalState.WATCH))
    assert not a.changed(_s(SignalState.WATCH))
    assert a.changed(_s(SignalState.READY))
