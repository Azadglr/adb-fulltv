from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
    Platform.IMAGE,
    Platform.NOTIFY,
]


async def async_setup_entry(hass: HomeAssistant, entry):
    hass.data.setdefault(DOMAIN, {})

    from .tv_client import ADBFullTVClient

    host = entry.data["host"]
    name = entry.data.get("name", "ADB FullTV")
    certfile = entry.data.get("certfile")
    keyfile = entry.data.get("keyfile")
    use_adb_volume = entry.options.get("use_adb_volume", False)

    if not certfile or not keyfile:
        safe = host.replace(":", "_").replace(".", "_")
        base = Path(hass.config.path(".storage")) / DOMAIN / safe
        base.parent.mkdir(parents=True, exist_ok=True)
        certfile = str(base.with_suffix(".cert.pem"))
        keyfile = str(base.with_suffix(".key.pem"))

    client = ADBFullTVClient(hass, host, name, certfile, keyfile, use_adb_volume=use_adb_volume)
    hass.data[DOMAIN][entry.entry_id] = client
    await _async_register_services(hass)

    async def _connect_task():
        try:
            await client.connect()
        except Exception as err:
            _LOGGER.debug("Initial connect failed: %s", err)

    # Connection/reconnect work is intentionally a background task.  A task
    # created with async_create_task is tracked as startup work by Home
    # Assistant; the long-lived telemetry loop then leaves HA in
    # NOT_RUNNING forever even though the UI is already reachable.
    hass.async_create_background_task(
        _connect_task(), "adb_fulltv_initial_connect", eager_start=True
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        client = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
    return unload_ok


async def _async_register_services(hass: HomeAssistant):
    if hass.data[DOMAIN].get("_services_registered"):
        return

    async def adb_test(call: ServiceCall):
        from .adb_lite import ADBLite
        requested_host = call.data.get("host")
        results = {}
        for key, client in hass.data.get(DOMAIN, {}).items():
            if key == "_services_registered":
                continue
            host = getattr(client, "host", None)
            if not host or (requested_host and requested_host != host):
                continue
            adb = ADBLite(Path(hass.config.path(".storage")) / DOMAIN / "adb", host)
            ok = await adb.probe(stream=3)
            results[host] = {
                "adb_connected": ok,
                "last_error": adb.last_error,
                "message": "ADB connection successful." if ok else "ADB connection failed. Check Network Debugging permission on the TV.",
            }
            await adb.disconnect()
        return results

    async def adb_set_volume_test(call: ServiceCall):
        from .adb_lite import ADBLite
        requested_host = call.data.get("host")
        level = float(call.data.get("level", 0.8))
        results = {}
        for key, client in hass.data.get(DOMAIN, {}).items():
            if key == "_services_registered":
                continue
            host = getattr(client, "host", None)
            if not host or (requested_host and requested_host != host):
                continue
            adb = ADBLite(Path(hass.config.path(".storage")) / DOMAIN / "adb", host)
            connected = await adb.probe(stream=3)
            sent = False
            if connected:
                await adb.refresh_volume_range(stream=3)
                sent = await adb.set_volume(level, stream=3, show_ui=True)
            results[host] = {"adb_connected": connected, "set_volume_sent": sent, "target_level": level, "max_index": adb.max_index, "last_error": adb.last_error}
            await adb.disconnect()
        return results

    hass.services.async_register(DOMAIN, "adb_test", adb_test, schema=vol.Schema({vol.Optional("host"): str}), supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "adb_set_volume_test", adb_set_volume_test, schema=vol.Schema({vol.Optional("host"): str, vol.Optional("level", default=0.8): vol.Coerce(float)}), supports_response=SupportsResponse.ONLY)
    hass.data[DOMAIN]["_services_registered"] = True
