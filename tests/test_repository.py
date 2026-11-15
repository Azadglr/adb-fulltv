from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "adb_fulltv"


class RepositoryTest(unittest.TestCase):
    def test_translation_keys_match_strings(self) -> None:
        strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
        for language in ("en", "tr"):
            translation = json.loads(
                (INTEGRATION / "translations" / f"{language}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(translation.keys(), strings.keys())

    def test_manifest_uses_semantic_version(self) -> None:
        manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
        parts = manifest["version"].split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(part.isdigit() for part in parts))

    def test_public_assets_do_not_contain_private_material(self) -> None:
        tracked_names = [path.name.lower() for path in ROOT.rglob("*") if path.is_file()]
        self.assertNotIn("adbkey", tracked_names)
        self.assertFalse(any(name.endswith(".pem") for name in tracked_names))


if __name__ == "__main__":
    unittest.main()
