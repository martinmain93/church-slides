"""The power state that explains a slow engine on a fast computer."""

from auto_presenter.pipeline import power
from auto_presenter.pipeline.power import PowerState, parse_pmset

SETTINGS = """System-wide power settings:
Currently in use:
 standby              1
 lowpowermode         {lpm}
 sleep                1
"""


def test_parse_pmset_reads_low_power_mode_and_battery():
    state = parse_pmset(
        SETTINGS.format(lpm=1),
        "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t98%; discharging;",
    )
    assert state == PowerState(low_power_mode=True, battery_percent=98, on_battery=True)


def test_parse_pmset_critical_battery_on_the_charger():
    state = parse_pmset(
        SETTINGS.format(lpm=0),
        "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t3%; charging;",
    )
    assert not state.low_power_mode
    assert not state.on_battery
    assert state.battery_low


def test_parse_pmset_desktop_without_a_battery():
    state = parse_pmset(SETTINGS.format(lpm=0), "Now drawing from 'AC Power'\n")
    assert state.battery_percent is None
    assert not state.battery_low


def test_read_power_state_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(power, "_cached", None)
    monkeypatch.setattr(power.sys, "platform", "darwin")
    monkeypatch.setattr(power, "_read_macos", lambda: calls.append(1) or PowerState())
    power.read_power_state()
    power.read_power_state()
    assert len(calls) == 1
