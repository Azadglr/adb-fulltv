from __future__ import annotations

import asyncio
import logging
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from androidtvremote2 import AndroidTVRemote
from homeassistant.core import HomeAssistant

from .adb_lite import ADBLite
from .const import DOMAIN
from .power_state import parse_android_tv_power_state

_LOGGER = logging.getLogger(__name__)
Listener = Callable[[], None]


async def _maybe_await(result: Any):
    if asyncio.iscoroutine(result):
        return await result
    return result


class ADBFullTVClient:
    """Remote v2 control plus optional ADB Lite for instant volume."""

    _ADB_PORT = 5555
    _ADB_STREAM = 3

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        client_name: str,
        certfile: str,
        keyfile: str,
        use_adb_volume: bool = False,
    ):
        self.hass = hass
        self.host = host
        self.client_name = client_name
        self.certfile = certfile
        self.keyfile = keyfile
        self.remote = AndroidTVRemote(self.client_name, self.certfile, self.keyfile, self.host)

        self.available: bool | None = None
        self.is_on: bool | None = None
        self._adb_is_on: bool | None = None
        self.current_app: str | None = None
        self.volume_info: dict | None = None
        self.current_input: str | None = None
        self.inputs: list[str] = []
        self.media_title: str | None = None
        self.media_artist: str | None = None
        self.media_album: str | None = None
        self.media_channel: str | None = None
        self.media_image_url: str | None = None
        self.media_duration: float | None = None
        self.media_position: float | None = None
        self.media_position_updated_at: datetime | None = None
        self.media_state: str | None = None
        self._telemetry_task: asyncio.Task | None = None
        self._remote_reconnect_task: asyncio.Task | None = None
        self._screenshot: bytes | None = None
        self._screenshot_at = 0.0
        self._adb_failures = 0

        self._listeners: set[Listener] = set()
        self._callbacks_registered = False
        self._connect_lock = asyncio.Lock()
        self._volume_lock = asyncio.Lock()

        self.use_adb_volume = use_adb_volume
        self._adb: Optional[ADBLite] = None
        self._adb_dir = Path(self.hass.config.path(".storage")) / DOMAIN / "adb"

    def add_listener(self, listener: Listener) -> Callable[[], None]:
        self._listeners.add(listener)

        def _remove():
            self._listeners.discard(listener)

        return _remove

    def _notify(self):
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                _LOGGER.debug("Listener callback failed", exc_info=True)

    async def connect(self):
        async with self._connect_lock:
            # ADB provides metadata and screenshots independently from Remote v2.
            # Start it first so a slow Remote pairing/reconnect cannot block the
            # unified media entity.
            if self._adb is None:
                self._adb = ADBLite(self._adb_dir, self.host, self._ADB_PORT)
            if self._telemetry_task is None or self._telemetry_task.done():
                self._telemetry_task = self.hass.async_create_background_task(
                    self._telemetry_loop(),
                    "adb_fulltv_telemetry",
                    eager_start=True,
                )
            if self.use_adb_volume:
                self.hass.async_create_task(
                    self._adb.refresh_volume_range(stream=self._ADB_STREAM)
                )

            if hasattr(self.remote, "async_generate_cert_if_missing"):
                await _maybe_await(self.remote.async_generate_cert_if_missing())
            await _maybe_await(self.remote.async_connect())
            if not self._callbacks_registered:
                self._register_callbacks()
                self._callbacks_registered = True
            if hasattr(self.remote, "keep_reconnecting"):
                if self._remote_reconnect_task is None or self._remote_reconnect_task.done():
                    self._remote_reconnect_task = self.hass.async_create_background_task(
                        _maybe_await(self.remote.keep_reconnecting()),
                        "adb_fulltv_remote_reconnect",
                        eager_start=True,
                    )
            await self.refresh_inputs()

    async def disconnect(self):
        if self._telemetry_task:
            self._telemetry_task.cancel()
            self._telemetry_task = None
        if self._remote_reconnect_task:
            self._remote_reconnect_task.cancel()
            self._remote_reconnect_task = None
        if hasattr(self.remote, "disconnect"):
            try:
                await asyncio.wait_for(
                    _maybe_await(self.remote.disconnect()), timeout=3.0
                )
            except (TimeoutError, asyncio.CancelledError):
                _LOGGER.debug("Remote disconnect timed out")
        if self._adb:
            try:
                await asyncio.wait_for(self._adb.disconnect(), timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                _LOGGER.debug("ADB disconnect timed out")

    def _register_callbacks(self):
        if hasattr(self.remote, "add_is_available_updated_callback"):
            self.remote.add_is_available_updated_callback(self._on_available)
        if hasattr(self.remote, "add_is_on_updated_callback"):
            self.remote.add_is_on_updated_callback(self._on_is_on)
        if hasattr(self.remote, "add_current_app_updated_callback"):
            self.remote.add_current_app_updated_callback(self._on_current_app)
        if hasattr(self.remote, "add_volume_info_updated_callback"):
            self.remote.add_volume_info_updated_callback(self._on_volume_info)

    def _on_available(self, value: bool):
        self.available = value
        self._notify()

    def _on_is_on(self, value: bool):
        # A fresh Remote v2 event invalidates the last ADB power snapshot.
        # The next telemetry cycle will confirm the physical screen state.
        self.is_on = value
        self._adb_is_on = None
        self._notify()

    def _on_current_app(self, value: str):
        self.current_app = value
        self._notify()

    def _on_volume_info(self, value: dict):
        self.volume_info = value
        self._notify()

    def get_is_on_cached(self):
        return self._adb_is_on if self._adb_is_on is not None else self.is_on
    def get_current_app_cached(self): return self.current_app
    def get_volume_info_cached(self): return self.volume_info
    def get_sources_cached(self): return self.inputs
    def get_current_source_cached(self): return self.current_input

    async def _telemetry_loop(self):
        """Collect media metadata that Android TV Remote v2 does not expose."""
        while True:
            try:
                await self.refresh_media_metadata()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.debug("ADB media refresh failed", exc_info=True)
            await asyncio.sleep(5)

    @staticmethod
    def _metadata_value(text: str, key: str) -> str | None:
        patterns = (
            rf"android\.media\.metadata\.{key}\s*[=:]\s*([^\n\r]+)",
            rf"(?:^|[\s,{{]){key.lower()}\s*[=:]\s*([^\n\r,}}]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(1).strip().strip("'\"")
                if value and value.lower() not in ("null", "none"):
                    return value
        return None

    async def refresh_media_metadata(self):
        if self._adb is None:
            self._adb = ADBLite(self._adb_dir, self.host, self._ADB_PORT)
        previous_is_on = self.get_is_on_cached()
        previous_available = self.available
        # ADB stays reachable on some TCL / Google TV sets during standby.
        # Media-session success does NOT imply that the screen is on.
        power_dump = await self._adb.shell("dumpsys power")
        screen_on = parse_android_tv_power_state(power_dump)
        if power_dump is not None and screen_on is None:
            screen_on = parse_android_tv_power_state(
                await self._adb.shell("dumpsys display")
            )
        if screen_on is not None:
            self._adb_is_on = screen_on
            self.available = True

        if screen_on is False:
            old_metadata = (
                self.current_app, self.media_title, self.media_artist,
                self.media_album, self.media_channel, self.media_image_url,
                self.media_duration, self.media_position, self.media_state,
            )
            self.current_app = None
            self.media_title = None
            self.media_artist = None
            self.media_album = None
            self.media_channel = None
            self.media_image_url = None
            self.media_duration = None
            self.media_position = None
            self.media_position_updated_at = None
            self.media_state = None
            self._screenshot = None
            self._adb_failures = 0
            if (
                previous_is_on is not False
                or previous_available != self.available
                or any(value is not None for value in old_metadata)
            ):
                self._notify()
            return

        dump = await self._adb.shell("dumpsys media_session")
        if dump is None:
            self._adb_failures += 1
            if self._adb_failures >= 3:
                self.available = False
            if (
                previous_is_on != self.get_is_on_cached()
                or previous_available != self.available
            ):
                self._notify()
            return
        self._adb_failures = 0
        self.available = True

        focus = await self._adb.shell(
            "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'"
        )
        if focus:
            package = re.search(r"\s([A-Za-z0-9_.]+)/[A-Za-z0-9_.$]+", focus)
            if package:
                self.current_app = package.group(1)
        media_package = re.search(
            r"Media button session is ([A-Za-z0-9_.]+)/", dump, re.IGNORECASE
        )
        if media_package:
            self.current_app = media_package.group(1)

        volume = await self._adb.shell(
            f"cmd media_session volume --stream {self._ADB_STREAM} --get"
        )
        if volume:
            volume_match = re.search(
                r"volume is\s+(\d+).*?range\s*\[0\.\.(\d+)\]",
                volume,
                re.IGNORECASE,
            )
            if volume_match:
                level = int(volume_match.group(1))
                maximum = int(volume_match.group(2))
                self.volume_info = {
                    "level": level,
                    "max": maximum,
                    "muted": level == 0,
                }

        title = self._metadata_value(dump, "TITLE")
        artist = self._metadata_value(dump, "ARTIST")
        album = self._metadata_value(dump, "ALBUM")
        channel = self._metadata_value(dump, "DISPLAY_TITLE") or self._metadata_value(dump, "AUTHOR")
        duration_raw = self._metadata_value(dump, "DURATION")

        description = re.search(r"description=([^\n\r]+)", dump, re.IGNORECASE)
        if description and not title:
            parts = [part.strip() for part in description.group(1).split(",")]
            title = parts[0] if parts and parts[0].lower() != "null" else None
            artist = artist or (parts[1] if len(parts) > 1 and parts[1].lower() != "null" else None)
            album = album or (parts[2] if len(parts) > 2 and parts[2].lower() != "null" else None)

        state_match = re.search(r"state=(\d+).*?position=(\d+)", dump, re.IGNORECASE | re.DOTALL)
        state_map = {0: "idle", 1: "stopped", 2: "paused", 3: "playing", 6: "buffering", 7: "error"}
        new_state = state_map.get(int(state_match.group(1))) if state_match else None
        position = float(state_match.group(2)) / 1000 if state_match else None
        try:
            duration = float(duration_raw) / 1000 if duration_raw else None
        except ValueError:
            duration = None

        changed = (
            title, artist, album, channel, duration, position, new_state
        ) != (
            self.media_title, self.media_artist, self.media_album, self.media_channel,
            self.media_duration, self.media_position, self.media_state
        )
        self.media_title = title
        self.media_artist = artist
        self.media_album = album
        # YouTube commonly reports its creator in ARTIST but leaves the
        # CHANNEL field empty.  Surface it in both standard HA metadata
        # fields so dashboards can choose either "channel" or "artist".
        self.media_channel = channel or (
            artist if self.current_app == "com.google.android.youtube.tv" else None
        )
        self.media_duration = duration
        self.media_position = position
        self.media_state = new_state
        if position is not None:
            self.media_position_updated_at = datetime.now(timezone.utc)
        if (
            changed
            or previous_is_on != self.get_is_on_cached()
            or previous_available != self.available
        ):
            self._notify()

    async def get_screenshot(self, max_age: float = 5.0) -> bytes | None:
        now = asyncio.get_running_loop().time()
        if self._screenshot and now - self._screenshot_at < max_age:
            return self._screenshot
        if self._adb is None:
            self._adb = ADBLite(self._adb_dir, self.host, self._ADB_PORT)
        image = await self._adb.screenshot()
        if image and image.startswith(b"\x89PNG"):
            self._screenshot = image
            self._screenshot_at = now
        return self._screenshot

    async def play_media(self, media_id: str):
        """Open a YouTube/video URL or Android package on the TV."""
        value = str(media_id).strip()
        if not value:
            return
        if value.startswith(("http://", "https://")):
            if self._adb is None:
                self._adb = ADBLite(self._adb_dir, self.host, self._ADB_PORT)
            result = await self._adb.shell(
                "am start -a android.intent.action.VIEW -d " + shlex.quote(value)
            )
            if result is not None:
                return
        await self.launch_app(value)

    async def refresh_inputs(self):
        if hasattr(self.remote, "get_inputs"):
            try:
                result = await _maybe_await(self.remote.get_inputs())
                if isinstance(result, list):
                    self.inputs = result
                    self._notify()
            except Exception:
                _LOGGER.debug("get_inputs failed", exc_info=True)
        self.current_input = getattr(self.remote, "current_input", None)

    async def set_source(self, source: str):
        if hasattr(self.remote, "set_input"):
            await _maybe_await(self.remote.set_input(source))
            self.current_input = source
            self._notify()
        else:
            raise NotImplementedError

    async def launch_app(self, app_id: str):
        # Remote v2's launch command is not implemented on every Google TV
        # build.  ADB's launcher intent is deterministic and also reports
        # completion to us, so use it first whenever ADB is available.
        if re.fullmatch(r"[A-Za-z0-9._]+", app_id):
            if self._adb is None:
                self._adb = ADBLite(self._adb_dir, self.host, self._ADB_PORT)
            result = await self._adb.shell(
                "monkey -p " + shlex.quote(app_id) + " -c android.intent.category.LAUNCHER 1"
            )
            if result is not None:
                self.current_app = app_id
                self.current_input = None
                self._notify()
                return
        if hasattr(self.remote, "send_launch_app_command"):
            await _maybe_await(self.remote.send_launch_app_command(app_id))
            return
        if hasattr(self.remote, "launch_app"):
            await _maybe_await(self.remote.launch_app(app_id))
            return
        raise NotImplementedError

    async def send_key(self, key: str, direction: str = "SHORT"):
        await _maybe_await(self.remote.send_key_command(key, direction))

    async def power_on(self):
        self._adb_is_on = None
        await self.send_key("POWER")

    async def power_off(self):
        """Power off through ADB first; some TCL Remote v2 builds ignore POWER."""
        if self._adb is None:
            self._adb = ADBLite(self._adb_dir, self.host, self._ADB_PORT)
        result = await self._adb.shell("input keyevent 26")
        if result is not None:
            self.is_on = False
            self._adb_is_on = False
            self._notify()
            return
        await self.send_key("POWER")
    async def up(self): await self.send_key("DPAD_UP")
    async def down(self): await self.send_key("DPAD_DOWN")
    async def left(self): await self.send_key("DPAD_LEFT")
    async def right(self): await self.send_key("DPAD_RIGHT")
    async def enter(self): await self.send_key("DPAD_CENTER")
    async def back(self): await self.send_key("BACK")
    async def home(self): await self.send_key("HOME")
    async def volume_up(self): await self.send_key("VOLUME_UP")
    async def volume_down(self): await self.send_key("VOLUME_DOWN")
    async def mute(self): await self.send_key("MUTE")
    async def play(self): await self.send_key("MEDIA_PLAY")
    async def pause(self): await self.send_key("MEDIA_PAUSE")
    async def play_pause(self): await self.send_key("MEDIA_PLAY_PAUSE")
    async def next_track(self): await self.send_key("MEDIA_NEXT")
    async def previous_track(self): await self.send_key("MEDIA_PREVIOUS")
    async def stop(self): await self.send_key("MEDIA_STOP")
    async def fast_forward(self): await self.send_key("MEDIA_FAST_FORWARD")
    async def rewind(self): await self.send_key("MEDIA_REWIND")

    async def set_volume_level(self, volume_0_1: float):
        value = max(0.0, min(1.0, float(volume_0_1)))
        async with self._volume_lock:
            if self.use_adb_volume:
                try:
                    if self._adb is None:
                        self._adb = ADBLite(self._adb_dir, self.host, self._ADB_PORT)
                    ok = await self._adb.set_volume(value, stream=self._ADB_STREAM, show_ui=True)
                    if ok:
                        if isinstance(self.volume_info, dict):
                            vmax = self.volume_info.get("max") or self.volume_info.get("max_volume") or self._adb.max_index
                            if vmax:
                                self.volume_info = {"level": int(round(value * int(vmax))), "max": int(vmax), "muted": self.volume_info.get("muted", False)}
                                self._notify()
                        return
                except Exception as err:
                    _LOGGER.debug("ADB volume failed, fallback to step: %s", err)
            await self._step_to_target(value)

    async def _step_to_target(self, value: float):
        if not isinstance(self.volume_info, dict):
            return
        cur = self.volume_info.get("level")
        vmax = self.volume_info.get("max")
        if cur is None or vmax in (None, 0):
            return
        target = int(round(value * int(vmax)))
        cur = int(cur)
        if target == cur:
            return
        key = "VOLUME_UP" if target > cur else "VOLUME_DOWN"
        max_steps = min(120, abs(target - cur) + 10)
        deadline = asyncio.get_event_loop().time() + 2.0
        for _ in range(max_steps):
            if asyncio.get_event_loop().time() > deadline:
                break
            await self.send_key(key)
            await asyncio.sleep(0.01)
