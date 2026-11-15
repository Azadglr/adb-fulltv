from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

try:
    from adb_shell.adb_device_async import AdbDeviceTcpAsync
except Exception:  # pragma: no cover
    AdbDeviceTcpAsync = None

from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

_LOGGER = logging.getLogger(__name__)
_RE_RANGE = re.compile(r"range\s*\[0\.\.(\d+)\]", re.IGNORECASE)


class ADBLite:
    """Small ADB client used only for absolute volume control."""

    def __init__(self, storage_dir: Path, host: str, port: int = 5555):
        self._storage_dir = storage_dir
        self.host = host
        self.port = port
        self._dev = None
        self._signer: Optional[PythonRSASigner] = None
        self._max_index = 100
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    @property
    def max_index(self) -> int:
        return self._max_index

    def _ensure_keys(self) -> PythonRSASigner:
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        priv = self._storage_dir / "adbkey"
        pub = self._storage_dir / "adbkey.pub"
        if not priv.exists() or not pub.exists():
            keygen(str(priv))
        return PythonRSASigner(pub.read_text(encoding="utf-8"), priv.read_text(encoding="utf-8"))

    def _new_device(self):
        if AdbDeviceTcpAsync is None:
            raise RuntimeError("adb-shell[async] is not installed or AdbDeviceTcpAsync is unavailable")
        candidates = [
            ((self.host, self.port), {"default_transport_timeout_s": 15.0}),
            ((self.host, self.port, 2.0), {}),
            ((self.host, self.port), {}),
        ]
        last_err: Exception | None = None
        for args, kwargs in candidates:
            try:
                return AdbDeviceTcpAsync(*args, **kwargs)
            except TypeError as err:
                last_err = err
        raise last_err or TypeError("Could not create AdbDeviceTcpAsync")

    async def _ensure_connected(self) -> bool:
        if self._dev is not None:
            return True
        try:
            self._signer = await asyncio.to_thread(self._ensure_keys)
            self._dev = self._new_device()
            await self._dev.connect(rsa_keys=[self._signer], auth_timeout_s=2.0)
            self.last_error = None
            return True
        except Exception as err:
            self.last_error = str(err)
            self._dev = None
            _LOGGER.debug("ADB connect failed for %s:%s: %s", self.host, self.port, err)
            return False

    async def disconnect(self) -> None:
        if self._dev is not None:
            try:
                await self._dev.close()
            except Exception:
                pass
        self._dev = None

    async def probe(self, stream: int = 3) -> bool:
        async with self._lock:
            if not await self._ensure_connected() or self._dev is None:
                return False
            try:
                await self._dev.shell(f"cmd media_session volume --stream {stream} --get")
                self.last_error = None
                return True
            except Exception as err:
                self.last_error = str(err)
                return False

    async def shell(self, command: str) -> str | None:
        """Run a read-only or control shell command on the TV."""
        async with self._lock:
            if not await self._ensure_connected() or self._dev is None:
                return None
            try:
                result = await self._dev.shell(command)
                self.last_error = None
                if isinstance(result, bytes):
                    return result.decode("utf-8", errors="replace")
                return str(result) if result is not None else ""
            except Exception as err:
                self.last_error = str(err)
                await self.disconnect()
                return None

    async def screenshot(self) -> bytes | None:
        """Capture the current TV screen as PNG through ADB."""
        async with self._lock:
            if not await self._ensure_connected() or self._dev is None:
                return None
            try:
                result = await self._dev.exec_out(
                    "screencap -p",
                    transport_timeout_s=15.0,
                    read_timeout_s=15.0,
                    timeout_s=30.0,
                    decode=False,
                )
                self.last_error = None
                return bytes(result) if result else None
            except Exception as err:
                self.last_error = str(err)
                await self.disconnect()
                return None

    async def refresh_volume_range(self, stream: int = 3) -> None:
        async with self._lock:
            if not await self._ensure_connected() or self._dev is None:
                return
            try:
                out = await self._dev.shell(f"cmd media_session volume --stream {stream} --get")
                if out:
                    match = _RE_RANGE.search(out)
                    if match:
                        self._max_index = int(match.group(1))
                self.last_error = None
            except Exception as err:
                self.last_error = str(err)

    async def set_volume(self, level_0_1: float, stream: int = 3, show_ui: bool = True) -> bool:
        value = max(0.0, min(1.0, float(level_0_1)))
        async with self._lock:
            if not await self._ensure_connected() or self._dev is None:
                return False
            idx = int(round(value * self._max_index))
            idx = max(0, min(self._max_index, idx))
            cmd = f"cmd media_session volume {'--show ' if show_ui else ''}--stream {stream} --set {idx}"
            try:
                await self._dev.shell(cmd)
                self.last_error = None
                return True
            except Exception as err:
                self.last_error = str(err)
                return False
