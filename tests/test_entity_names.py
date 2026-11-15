from __future__ import annotations

import unittest
from pathlib import Path
from runpy import run_path


_MODULE = run_path(
    Path(__file__).parents[1]
    / "custom_components"
    / "adb_fulltv"
    / "entity_names.py"
)
entity_name = _MODULE["entity_name"]


class EntityNameTest(unittest.TestCase):
    def test_turkish_names_are_distinct(self) -> None:
        self.assertEqual(entity_name("tr", "remote"), "Kontrol")
        self.assertEqual(entity_name("tr-TR", "screen"), "Ekran")
        self.assertEqual(entity_name("tr", "notification"), "Bildirim")

    def test_english_names_are_distinct(self) -> None:
        self.assertEqual(entity_name("en", "remote"), "Remote")
        self.assertEqual(entity_name("en-US", "screen"), "Screen")
        self.assertEqual(
            entity_name("en", "notification"), "On-screen notification"
        )

    def test_unknown_languages_fall_back_to_english(self) -> None:
        self.assertEqual(entity_name("de", "screen"), "Screen")
        self.assertEqual(entity_name(None, "notification"), "On-screen notification")


if __name__ == "__main__":
    unittest.main()
