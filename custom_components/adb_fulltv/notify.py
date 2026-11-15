from __future__ import annotations

import shlex

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    client = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ADBFullTVNotify(entry, client)])


class ADBFullTVNotify(NotifyEntity):
    """Send on-screen notifications through the Android TV notification app."""

    _attr_supported_features = NotifyEntityFeature.TITLE
    _attr_has_entity_name = True
    _attr_translation_key = "notification"

    def __init__(self, entry, client):
        self.entry = entry
        self.client = client
        self._attr_name = None
        self._attr_unique_id = f"{entry.entry_id}_notify"
        self._notifier = None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.entry.data["host"])},
            "name": self.entry.data.get("name", "ADB FullTV"),
            "manufacturer": "TCL / Google TV",
        }

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Display an overlay notification, with an ADB system fallback."""
        try:
            if self._notifier is None:
                from notifications_android_tv.notifications import Notifications

                self._notifier = await self.hass.async_add_executor_job(
                    Notifications, self.client.host
                )
            sent = await self.hass.async_add_executor_job(
                lambda: self._notifier.send(
                    message=str(message).replace("\n", "<br>"),
                    title=title,
                )
            )
            if sent:
                return
        except Exception:
            self._notifier = None

        # The optional TV overlay app is not always installed/running. ADB's
        # native notification command still provides a reliable local fallback.
        adb = self.client._adb
        if adb is None:
            from .adb_lite import ADBLite

            adb = ADBLite(self.client._adb_dir, self.client.host)
            self.client._adb = adb
        command = "cmd notification post -S bigtext"
        if title:
            command += " -t " + shlex.quote(str(title))
        command += " ADBFullTV " + shlex.quote(str(message))
        result = await adb.shell(command)
        if result is None:
            raise HomeAssistantError("Unable to send the notification to the TV.")
