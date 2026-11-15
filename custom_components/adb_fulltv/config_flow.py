from __future__ import annotations

import asyncio
import ipaddress
import socket
from pathlib import Path

import voluptuous as vol
from androidtvremote2 import AndroidTVRemote
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .adb_lite import ADBLite
from .const import DOMAIN

PORTS_TO_PROBE = (6466, 6467)
SCAN_TIMEOUT = 0.35
SCAN_CONCURRENCY = 80


def _storage_dir(hass) -> Path:
    p = Path(hass.config.path(".storage")) / DOMAIN
    p.mkdir(parents=True, exist_ok=True)
    return p


def _paths_for_host(hass, host: str) -> tuple[str, str]:
    safe = host.replace(":", "_").replace(".", "_")
    base = _storage_dir(hass) / safe
    return str(base.with_suffix(".cert.pem")), str(base.with_suffix(".key.pem"))


def _get_local_ipv4() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


async def _probe_port(ip: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=SCAN_TIMEOUT)
        writer.close()
        if hasattr(writer, "wait_closed"):
            await writer.wait_closed()
        return True
    except Exception:
        return False


async def _looks_like_androidtvremote2(ip: str) -> bool:
    for port in PORTS_TO_PROBE:
        if await _probe_port(ip, port):
            return True
    return False


async def _scan_subnet(local_ip: str) -> list[str]:
    subnet = ".".join(local_ip.split(".")[:3]) + ".0/24"
    network = ipaddress.ip_network(subnet, strict=False)
    sem = asyncio.Semaphore(SCAN_CONCURRENCY)
    found: list[str] = []

    async def _scan_one(ip: str):
        async with sem:
            if await _looks_like_androidtvremote2(ip):
                found.append(ip)

    tasks = []
    for host in network.hosts():
        ip = str(host)
        if ip == local_ip:
            continue
        tasks.append(asyncio.create_task(_scan_one(ip)))
    await asyncio.gather(*tasks)
    found.sort()
    return found


class ADBFullTVFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._scan_results: list[str] = []
        self._host: str | None = None
        self._name: str | None = None
        self._pair_now = True
        self._use_adb_volume = False
        self._remote: AndroidTVRemote | None = None
        self._certfile: str | None = None
        self._keyfile: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return ADBFullTVOptionsFlow()

    async def async_step_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required("method", default="scan"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["scan", "manual"],
                            translation_key="setup_method",
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }),
            )
        return await self.async_step_scan() if user_input["method"] == "scan" else await self.async_step_manual()

    async def async_step_scan(self, user_input=None):
        if user_input is None:
            local_ip = _get_local_ipv4()
            if not local_ip:
                return self.async_abort(reason="local_ip_failed")
            self._scan_results = await _scan_subnet(local_ip)
            if not self._scan_results:
                return self.async_show_form(step_id="scan", errors={"base": "no_devices_found"}, data_schema=vol.Schema({}))
            return self.async_show_form(step_id="scan", data_schema=vol.Schema({
                vol.Required("host"): selector.SelectSelector(selector.SelectSelectorConfig(options=self._scan_results, mode=selector.SelectSelectorMode.DROPDOWN)),
                vol.Optional("name", default="ADB FullTV"): str,
                vol.Optional("pair_now", default=True): bool,
                vol.Optional("use_adb_volume", default=False): bool,
            }))
        return await self._handle_device_selection(user_input)

    async def async_step_manual(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="manual", data_schema=vol.Schema({
                vol.Required("host"): str,
                vol.Optional("name", default="ADB FullTV"): str,
                vol.Optional("pair_now", default=True): bool,
                vol.Optional("use_adb_volume", default=False): bool,
            }))
        return await self._handle_device_selection(user_input)

    async def _handle_device_selection(self, user_input):
        self._host = user_input["host"]
        self._name = user_input.get("name", "ADB FullTV")
        self._pair_now = bool(user_input.get("pair_now", True))
        self._use_adb_volume = bool(user_input.get("use_adb_volume", False))
        await self.async_set_unique_id(self._host)
        self._abort_if_unique_id_configured()
        self._certfile, self._keyfile = _paths_for_host(self.hass, self._host)
        if not self._pair_now:
            return self._create_final_entry()
        return await self._async_start_pairing()

    def _create_final_entry(self):
        return self.async_create_entry(title=self._name or "ADB FullTV", data={"host": self._host, "name": self._name, "certfile": self._certfile, "keyfile": self._keyfile}, options={"use_adb_volume": self._use_adb_volume})

    async def _async_start_pairing(self):
        assert self._host and self._name and self._certfile and self._keyfile
        self._remote = AndroidTVRemote(self._name, self._certfile, self._keyfile, self._host)
        if hasattr(self._remote, "async_generate_cert_if_missing"):
            await self._remote.async_generate_cert_if_missing()
        if hasattr(self._remote, "async_start_pairing"):
            await self._remote.async_start_pairing()
        else:
            await self._remote.start_pairing()
        return await self.async_step_pair()

    async def _async_restart_pairing(self):
        try:
            if self._remote is not None and hasattr(self._remote, "disconnect"):
                result = self._remote.disconnect()
                if asyncio.iscoroutine(result):
                    await result
            return await self._async_start_pairing()
        except Exception:
            return await self.async_step_pair(errors={"base": "pair_start_failed"})

    async def async_step_pair(self, user_input=None, errors: dict | None = None):
        errors = errors or {}
        if user_input is not None:
            action = user_input.get("pair_action", "submit")
            if action == "resend":
                return await self._async_restart_pairing()
            if action == "skip":
                return self._create_final_entry()
            code = (user_input.get("code") or "").strip()
            if not code:
                errors["code"] = "code_required"
            else:
                try:
                    assert self._remote is not None
                    if hasattr(self._remote, "async_finish_pairing"):
                        await self._remote.async_finish_pairing(code)
                    else:
                        await self._remote.finish_pairing(code)
                    return self._create_final_entry()
                except Exception:
                    errors["base"] = "pair_failed"
        return self.async_show_form(step_id="pair", errors=errors, data_schema=vol.Schema({
            vol.Optional("code"): str,
            vol.Required("pair_action", default="submit"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["submit", "resend", "skip"],
                    translation_key="pair_action",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }))


class ADBFullTVOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None):
        use_adb_volume = self.config_entry.options.get("use_adb_volume", False)
        if user_input is not None:
            return self.async_create_entry(data={"use_adb_volume": bool(user_input.get("use_adb_volume", False))})
        host = self.config_entry.data.get("host")
        certfile = self.config_entry.data.get("certfile")
        keyfile = self.config_entry.data.get("keyfile")
        paired = bool(certfile and keyfile and Path(certfile).exists() and Path(keyfile).exists())
        is_turkish = (self.hass.config.language or "").lower().startswith("tr")
        adb_status = "Kapalı" if is_turkish else "Disabled"
        if use_adb_volume and host:
            adb = ADBLite(Path(self.hass.config.path(".storage")) / DOMAIN / "adb", host)
            ok = await adb.probe(stream=3)
            if ok:
                adb_status = "Aktif" if is_turkish else "Active"
            else:
                detail = adb.last_error or ("izin/bağlantı yok" if is_turkish else "permission or connection unavailable")
                adb_status = f"Bağlanamadı ({detail})" if is_turkish else f"Unavailable ({detail})"
            await adb.disconnect()
        if is_turkish:
            status = f"IP Adresi: {host}\nEşleştirme: {'Var' if paired else 'Yok'}\nADB Servisi: {adb_status}"
        else:
            status = f"IP address: {host}\nPairing: {'Available' if paired else 'Unavailable'}\nADB service: {adb_status}"
        return self.async_show_form(step_id="init", description_placeholders={"status": status}, data_schema=vol.Schema({vol.Optional("use_adb_volume", default=use_adb_volume): bool}))
