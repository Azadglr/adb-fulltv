from __future__ import annotations

import unittest
from pathlib import Path
from runpy import run_path


MODULE = run_path(
    Path(__file__).parents[1]
    / "custom_components"
    / "adb_fulltv"
    / "power_state.py"
)
parse_power = MODULE["parse_android_tv_power_state"]


class PowerStateTest(unittest.TestCase):
    def test_tcl_standby_with_adb_still_reachable(self) -> None:
        text = "mWakefulness=Asleep\nmIsPowered=true\nmWakefulnessChanging=false"
        self.assertIs(parse_power(text), False)

    def test_screen_off_is_not_active_media(self) -> None:
        text = "Display State=OFF\nmScreenState=OFF\nmActualState=OFF"
        self.assertIs(parse_power(text), False)

    def test_awake(self) -> None:
        self.assertIs(parse_power("mWakefulness=Awake"), True)

    def test_dozing(self) -> None:
        self.assertIs(parse_power("mWakefulness=Dozing"), False)

    def test_dreaming(self) -> None:
        self.assertIs(parse_power("mWakefulness=Dreaming"), True)

    def test_panel_overrides_unrelated_power_flag(self) -> None:
        self.assertIs(parse_power("mIsPowered=true\nmActualState=OFF"), False)

    def test_unknown_is_not_assumed_on(self) -> None:
        self.assertIsNone(parse_power("mIsPowered=true"))
        self.assertIsNone(parse_power(None))


if __name__ == "__main__":
    unittest.main()
