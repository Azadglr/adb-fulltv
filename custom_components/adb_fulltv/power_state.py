"""Parse Android TV screen power independently of ADB connectivity."""

from __future__ import annotations

import re


def parse_android_tv_power_state(output: str | None) -> bool | None:
    """Return True (on), False (standby/off), or None (no reliable signal).

    Do not use mIsPowered: that indicates external power/charging on many
    builds, and can be true while the physical TV panel is off.
    """
    if not output:
        return None

    # The concrete panel state takes priority if supplied by dumpsys display.
    for pattern in (
        r"\bmActualState\s*=\s*(ON|OFF)\b",
        r"\bmScreenState\s*=\s*(ON|OFF)\b",
        r"\bDisplay State\s*=\s*(ON|OFF)\b",
    ):
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            return match.group(1).upper() == "ON"

    match = re.search(
        r"\bmWakefulness\s*=\s*(Awake|Asleep|Dozing|Dreaming)\b",
        output,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).lower() in ("awake", "dreaming")

    return None
