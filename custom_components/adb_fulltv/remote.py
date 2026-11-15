from __future__ import annotations

from homeassistant.components.remote import RemoteEntity
from .const import DOMAIN
from .entity_names import entity_name


async def async_setup_entry(hass, entry, async_add_entities):
    client = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ADBFullTVRemote(entry, client)])


class ADBFullTVRemote(RemoteEntity):
    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(self, entry, client):
        self.entry = entry
        self.client = client
        self._attr_name = entity_name(client.hass.config.language, "remote")
        self._attr_unique_id = f"{entry.entry_id}_remote"
        self._unsub = None
        self._is_on = None

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.entry.data["host"])}, "name": self.entry.data.get("name", "ADB FullTV"), "manufacturer": "TCL / Google TV"}

    async def async_added_to_hass(self):
        self._unsub = self.client.add_listener(self._on_client_update)
        self._apply_from_client()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None

    def _on_client_update(self):
        self._apply_from_client()
        self.async_write_ha_state()

    def _apply_from_client(self):
        self._is_on = self.client.get_is_on_cached()

    @property
    def is_on(self): return self._is_on

    async def async_turn_on(self, **kwargs):
        if self._is_on is False:
            await self.client.power_on()

    async def async_turn_off(self, **kwargs):
        if self._is_on is True:
            await self.client.power_off()

    async def async_toggle(self, **kwargs):
        await self.client.power_on()

    async def async_send_command(self, command, **kwargs):
        if not isinstance(command, list):
            command = [command]
        for raw in command:
            if raw is None:
                continue
            cmd = str(raw).strip()
            aliases = {
                "PLAYPAUSE": self.client.play_pause,
                "PLAY_PAUSE": self.client.play_pause,
                "NEXT": self.client.next_track,
                "MEDIA_NEXT": self.client.next_track,
                "PREV": self.client.previous_track,
                "PREVIOUS": self.client.previous_track,
                "MEDIA_PREVIOUS": self.client.previous_track,
                "STOP": self.client.stop,
                "MEDIA_STOP": self.client.stop,
                "FF": self.client.fast_forward,
                "FAST_FORWARD": self.client.fast_forward,
                "MEDIA_FAST_FORWARD": self.client.fast_forward,
                "REW": self.client.rewind,
                "REWIND": self.client.rewind,
                "MEDIA_REWIND": self.client.rewind,
                "UP": self.client.up,
                "DOWN": self.client.down,
                "LEFT": self.client.left,
                "RIGHT": self.client.right,
                "OK": self.client.enter,
                "ENTER": self.client.enter,
                "BACK": self.client.back,
                "HOME": self.client.home,
            }
            if cmd in aliases:
                await aliases[cmd]()
            elif cmd == "NETFLIX":
                await self.client.launch_app("com.netflix.ninja")
            elif cmd == "YOUTUBE":
                await self.client.launch_app("com.google.android.youtube.tv")
            else:
                await self.client.send_key(cmd)
