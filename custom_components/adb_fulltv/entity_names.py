from __future__ import annotations


_ENTITY_NAMES = {
    "en": {
        "remote": "Remote",
        "screen": "Screen",
        "notification": "On-screen notification",
    },
    "tr": {
        "remote": "Kontrol",
        "screen": "Ekran",
        "notification": "Bildirim",
    },
}


def entity_name(language: str | None, key: str) -> str:
    """Return a stable localized name for a secondary TV entity."""
    locale = "tr" if (language or "").lower().startswith("tr") else "en"
    return _ENTITY_NAMES[locale][key]
