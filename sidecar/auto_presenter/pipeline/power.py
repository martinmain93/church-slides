"""Is the computer itself holding the engine back?

Observed live (2026-10-14, M3 Pro): a Mac that had run its battery down to
critical was plugged in and the engine still took ~3 s per speech pass, four
times its normal cost, until it had charged for a while. Low Power Mode does
the same. From the operator's chair that read as "this new laptop is too slow
for the AI" — the warning blamed the computer and suggested cloud speech,
when the fix was a setting or a few minutes on the charger.

So when the pipeline falls behind, the health report says what the power
state was. Read only then (and cached), since it costs a subprocess on macOS.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: At or below this charge the OS throttles, charger or not.
LOW_BATTERY_PERCENT = 10
#: How long one reading stands.
CACHE_S = 30.0


@dataclass(frozen=True)
class PowerState:
    low_power_mode: bool = False
    #: None when there is no battery (a desktop) or it couldn't be read.
    battery_percent: int | None = None
    on_battery: bool = False

    @property
    def battery_low(self) -> bool:
        return self.battery_percent is not None and self.battery_percent <= LOW_BATTERY_PERCENT


_cached: tuple[float, PowerState] | None = None


def read_power_state() -> PowerState:
    """The current power state, at most `CACHE_S` old. Never raises."""
    global _cached
    now = time.monotonic()
    if _cached is not None and now - _cached[0] < CACHE_S:
        return _cached[1]
    try:
        if sys.platform == "darwin":
            state = _read_macos()
        elif sys.platform == "win32":
            state = _read_windows()
        else:
            state = PowerState()
    except Exception:  # pragma: no cover - platform-dependent
        log.debug("could not read the power state", exc_info=True)
        state = PowerState()
    _cached = (now, state)
    return state


def _pmset(*args: str) -> str:
    return subprocess.run(
        ["/usr/bin/pmset", "-g", *args], capture_output=True, text=True, timeout=2
    ).stdout


def _read_macos() -> PowerState:
    return parse_pmset(_pmset(), _pmset("batt"))


def parse_pmset(settings: str, batt: str) -> PowerState:
    """`pmset -g` and `pmset -g batt` output -> PowerState.

    `pmset -g` lists the settings in use for the current power source, so its
    `lowpowermode` line is what applies right now."""
    lpm = re.search(r"^\s*lowpowermode\s+(\d)", settings, re.MULTILINE)
    pct = re.search(r"(\d+)%", batt)
    return PowerState(
        low_power_mode=bool(lpm and lpm.group(1) == "1"),
        battery_percent=int(pct.group(1)) if pct else None,
        on_battery="'Battery Power'" in batt,
    )


def _read_windows() -> PowerState:
    import ctypes

    class _SystemPowerStatus(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        ]

    s = _SystemPowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return PowerState()
    # 128 = no system battery, 255 = unknown; percent 255 = unknown.
    has_battery = s.BatteryFlag not in (128, 255) and s.BatteryLifePercent != 255
    return PowerState(
        # SystemStatusFlag 1 = battery saver on.
        low_power_mode=s.SystemStatusFlag == 1,
        battery_percent=int(s.BatteryLifePercent) if has_battery else None,
        on_battery=s.ACLineStatus == 0,
    )
