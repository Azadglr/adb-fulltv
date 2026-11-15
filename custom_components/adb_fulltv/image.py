from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    client = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ADBFullTVScreen(entry, client)])


class ADBFullTVScreen(ImageEntity):
    """Live screenshot captured from the TV through ADB."""

    _attr_content_type = "image/png"
    _attr_has_entity_name = True
    _attr_translation_key = "screen"

    def __init__(self, entry, client):
        super().__init__(client.hass)
        self.entry = entry
        self.client = client
        self._attr_name = None
        self._attr_unique_id = f"{entry.entry_id}_screen"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.entry.data["host"])},
            "name": self.entry.data.get("name", "ADB FullTV"),
            "manufacturer": "TCL / Google TV",
        }

    async def async_image(self) -> bytes | None:
        image = await self.client.get_screenshot()
        if image:
            self._attr_image_last_updated = dt_util.utcnow()
        return image
