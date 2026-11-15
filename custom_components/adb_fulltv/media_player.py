from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers import entity_platform

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

APP_NAMES_EN = {
    "com.google.android.youtube.tv": "YouTube",
    "com.netflix.ninja": "Netflix",
    "com.google.android.apps.tv.launcherx": "Google TV Home",
    "com.amazon.amazonvideo.livingroom": "Prime Video",
    "com.disney.disneyplus": "Disney+",
    "com.nathnetwork.xciptv": "IPTV",
    "com.google.android.tv": "Live TV",
    "com.tcl.tv": "TCL TV",
    "com.spotify.tv.android": "Spotify",
    "com.apple.atve.androidtv.appletv": "Apple TV+",
    "com.google.android.play.games": "Google Play Games",
    "com.google.android.apps.tv.dreamx": "Screensaver",
}

APP_NAMES_TR = {
    **APP_NAMES_EN,
    "com.google.android.apps.tv.launcherx": "Google TV Ana Ekran",
    "com.google.android.tv": "Canlı TV",
    "com.google.android.apps.tv.dreamx": "Ekran Koruyucu",
}

# The native Home Assistant media dialog uses ``media_image_url`` for its
# artwork.  These small, official-style brand marks are only a fallback: a
# real programme thumbnail reported by Google Cast always takes precedence.
APP_ICON_URLS = {
    "com.google.android.youtube.tv": "https://cdn.jsdelivr.net/npm/simple-icons@v15/icons/youtube.svg",
    "com.netflix.ninja": "https://cdn.jsdelivr.net/npm/simple-icons@v15/icons/netflix.svg",
    "com.amazon.amazonvideo.livingroom": "https://cdn.jsdelivr.net/npm/simple-icons@v15/icons/amazonprime.svg",
    "com.disney.disneyplus": "https://cdn.jsdelivr.net/npm/simple-icons@v15/icons/disneyplus.svg",
    "com.spotify.tv.android": "https://cdn.jsdelivr.net/npm/simple-icons@v15/icons/spotify.svg",
    "com.apple.atve.androidtv.appletv": "https://cdn.jsdelivr.net/npm/simple-icons@v15/icons/appletv.svg",
}

def _app_names(language: str | None) -> dict[str, str]:
    """Return human-readable app names in Home Assistant's configured language."""
    return APP_NAMES_TR if (language or "").lower().startswith("tr") else APP_NAMES_EN


def _friendly_app_name(package: str | None, language: str | None) -> str | None:
    if not package:
        return None
    if package in _app_names(language):
        return _app_names(language)[package]
    leaf = package.rsplit(".", 1)[-1].replace("_", " ").replace("-", " ")
    return leaf.title() if leaf else package


async def async_setup_entry(hass, entry, async_add_entities):
    client = hass.data[DOMAIN][entry.entry_id]
    entity = ADBFullTVMediaPlayer(entry, client)
    async_add_entities([entity])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("send_key", {vol.Required("key"): str}, "async_service_send_key")


class ADBFullTVMediaPlayer(MediaPlayerEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
    )

    def __init__(self, entry, client):
        self.entry = entry
        self.client = client
        self._attr_name = None
        self._attr_unique_id = f"{entry.entry_id}_media_player"
        self._unsub = None
        self._state = STATE_OFF
        self._app = None
        self._sources = []
        self._source = None
        self._volume_level = None
        self._is_muted = None

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
        if not self.client.get_is_on_cached():
            self._state = STATE_OFF
        elif self.client.media_state in ("playing", "paused", "idle"):
            self._state = self.client.media_state
        else:
            self._state = STATE_ON
        self._app = self.client.get_current_app_cached()
        inputs = self.client.get_sources_cached() or []
        # Always expose installed/common apps even when Android TV Remote v2
        # does not report its hardware-input list.  This makes HA's "Source"
        # button functional and keeps raw package identifiers out of the UI.
        app_sources = {label: package for package, label in _app_names(self.client.hass.config.language).items()}
        self._sources = list(dict.fromkeys([*app_sources, *inputs]))
        self._source = _friendly_app_name(self._app, self.client.hass.config.language) or self.client.get_current_source_cached()
        vinfo = self.client.get_volume_info_cached()
        self._volume_level = None
        self._is_muted = None
        if isinstance(vinfo, dict):
            cur = vinfo.get("level")
            vmax = vinfo.get("max")
            self._is_muted = vinfo.get("muted")
            if cur is not None and vmax not in (None, 0):
                self._volume_level = float(cur) / float(vmax)

    @property
    def state(self): return self._state
    @property
    def app_name(self): return _friendly_app_name(self._app, self.client.hass.config.language)
    @property
    def app_id(self): return self._app
    @property
    def media_title(self): return self.client.media_title
    @property
    def media_artist(self): return self.client.media_artist
    @property
    def media_album_name(self): return self.client.media_album
    @property
    def media_channel(self): return self.client.media_channel
    @property
    def media_duration(self): return self.client.media_duration
    @property
    def media_position(self): return self.client.media_position
    @property
    def media_position_updated_at(self): return self.client.media_position_updated_at
    @property
    def media_content_type(self): return "video" if self.client.media_title else None
    @property
    def media_image_url(self):
        return self.client.media_image_url or APP_ICON_URLS.get(self._app)
    @property
    def media_image_remotely_accessible(self): return True
    @property
    def source_list(self): return self._sources
    @property
    def source(self): return self._source
    @property
    def volume_level(self): return self._volume_level
    @property
    def is_volume_muted(self): return self._is_muted

    async def async_turn_on(self):
        await self.client.power_on()
        self._state = STATE_ON
        self.async_write_ha_state()

    async def async_turn_off(self):
        await self.client.power_off()
        self._state = STATE_OFF
        self.async_write_ha_state()

    async def async_media_play(self): await self.client.play()
    async def async_media_pause(self): await self.client.pause()
    async def async_media_stop(self): await self.client.stop()
    async def async_media_next_track(self): await self.client.next_track()
    async def async_media_previous_track(self): await self.client.previous_track()
    async def async_play_media(self, media_type, media_id, **kwargs):
        await self.client.play_media(media_id)
    async def async_volume_up(self): await self.client.volume_up()
    async def async_volume_down(self): await self.client.volume_down()

    async def async_mute_volume(self, mute):
        if mute:
            await self.client.mute()

    async def async_set_volume_level(self, volume):
        await self.client.set_volume_level(volume)

    async def async_select_source(self, source):
        try:
            package = {label: package for package, label in _app_names(self.client.hass.config.language).items()}.get(source)
            if package:
                await self.client.launch_app(package)
                self._source = source
                self.async_write_ha_state()
            else:
                await self.client.set_source(source)
        except NotImplementedError:
            _LOGGER.debug("Source selection is not supported by this device/library.")

    async def async_service_send_key(self, key: str):
        await self.client.send_key(key)
