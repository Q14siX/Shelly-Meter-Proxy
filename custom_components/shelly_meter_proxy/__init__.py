"""Shelly Meter Proxy integration entrypoints."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import DOMAIN, CONF_IDENT, CONF_SHELLY_IP, CONF_HANDOVER_ACK, CONF_CLOUD_ENABLE
from .meter import Meter
from .identity import resolve_ha_ip, is_original_reachable
from .bridge import ShellyBridge

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "switch"]


class Runtime:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.settings = {**entry.data, **entry.options}
        self.meter = Meter()
        self.bridge = ShellyBridge(hass, self.meter, self.settings, entry.data[CONF_IDENT])
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._task = None
        self.listeners = set()
        self._closed = False
        self.original_online = None
        self.bridge_active = False
        self._offline_checks = 0
        self.guard_error = None
        self._guard_lock = asyncio.Lock()
        self._expected_toggle_options = None

    async def start(self):
        saved = await self.store.async_load() or {}
        try:
            self.meter.imports = [float(v) for v in saved["imports"]][:3]
            self.meter.exports = [float(v) for v in saved["exports"]][:3]
            if len(self.meter.imports) != 3 or len(self.meter.exports) != 3:
                raise ValueError("invalid saved counters")
        except (KeyError, ValueError, TypeError):
            pass
        destination = saved.get("udp_destination")
        if isinstance(destination, list) and len(destination) == 2:
            try:
                self.bridge.set_udp_destination({"rpc_udp": {
                    "dst_addr": f"{destination[0]}:{int(destination[1])}"}})
            except (ValueError, TypeError):
                pass
        self.settings["lan_ip"] = await resolve_ha_ip(self.hass, self.settings[CONF_SHELLY_IP])
        if self.settings["lan_ip"] == self.settings[CONF_SHELLY_IP]:
            raise ValueError("Shelly and Home Assistant cannot use the same LAN IP")
        self.bridge.ip = self.settings["lan_ip"]
        self.meter.update(self.hass, self.settings)
        await self._check_original()
        self._task = asyncio.create_task(self.poll())

    async def _check_original(self):
        """Serialise handover checks and protect the physical Shelly identity.

        The same check is used by periodic monitoring and the Cloud switch;
        two callers must never bind the same listener or race to start/stop.
        """
        async with self._guard_lock:
            if self.settings.get("maintenance_mode") is True:
                self.guard_error = "Wartungsmodus – virtuelle Instanz vollständig angehalten"
                self.original_online = None
                self._offline_checks = 0
                if self.bridge_active:
                    await self.bridge.stop()
                    self.bridge_active = False
                return
            if self.settings.get(CONF_HANDOVER_ACK) is not True:
                self.original_online = None
                self._offline_checks = 0
                self.guard_error = "Umstellung nicht bestätigt – virtuelle Instanz gesperrt"
                if self.bridge_active:
                    await self.bridge.stop()
                    self.bridge_active = False
                return

            try:
                online = await is_original_reachable(
                    self.settings[CONF_SHELLY_IP], self.bridge.device_id
                )
            except Exception as exc:
                # Never treat an unexpected probe failure as proof the original
                # is offline. A transport timeout is handled within the probe.
                self.original_online = None
                self._offline_checks = 0
                self.guard_error = f"Original-Prüfung fehlgeschlagen: {type(exc).__name__}"
                _LOGGER.warning("Shelly-Originalprüfung fehlgeschlagen: %s", type(exc).__name__)
                if self.bridge_active:
                    await self.bridge.stop()
                    self.bridge_active = False
                return

            self.original_online = online
            if online:
                self._offline_checks = 0
                self.guard_error = "Original-Shelly erreichbar – virtuelle Instanz angehalten"
                if self.bridge_active:
                    await self.bridge.stop()
                    self.bridge_active = False
                return

            self._offline_checks = min(3, self._offline_checks + 1)
            if not self.bridge_active:
                if self._offline_checks < 3:
                    self.guard_error = (
                        f"Original-Abschaltprüfung {self._offline_checks}/3 – "
                        "virtuelle Instanz noch nicht gestartet"
                    )
                    return
                try:
                    await self.bridge.start()
                except Exception as exc:
                    # Explain common startup failures in the HA state itself.
                    # No secret identity material is placed into diagnostics.
                    await self.bridge.stop()
                    detail = (
                        f"Port {self.bridge.port} an {self.bridge.ip} belegt oder nicht nutzbar"
                        if isinstance(exc, OSError)
                        else type(exc).__name__
                    )
                    self.guard_error = f"Bridge-Start fehlgeschlagen: {detail}"
                    _LOGGER.warning("Shelly-Bridge startet nicht: %s", type(exc).__name__)
                    return
                self.bridge_active = True
            self.guard_error = None

    async def begin_maintenance(self):
        """Prevent duplicate local and Cloud identities while physical Shelly is on."""
        async with self._guard_lock:
            self.settings["maintenance_mode"] = True
            self.settings[CONF_CLOUD_ENABLE] = False
            self.bridge.cloud_enabled = False
            await self.bridge.stop()
            self.bridge_active = False
            self.guard_error = "Wartungsmodus – virtuelle Instanz vollständig angehalten"
            self._offline_checks = 0
        updated_options = {**self.entry.options, "maintenance_mode": True,
                           CONF_CLOUD_ENABLE: False}
        self._expected_toggle_options = dict(updated_options)
        self.hass.config_entries.async_update_entry(self.entry, options=updated_options)
        for fn in tuple(self.listeners):
            fn()

    async def set_cloud_enabled(self, enabled: bool):
        """Set Cloud transport without unloading/restarting the bridge.

        A switch-on immediately performs any remaining original-offline checks
        instead of raising a race-induced 'bridge not active' error after a
        settings reload. Settings persistence must not reload the integration.
        """
        if enabled:
            if self.settings.get("maintenance_mode") is True:
                raise HomeAssistantError("Wartungsmodus aktiv – zuerst Wartung abschließen")
            if self.settings.get(CONF_HANDOVER_ACK) is not True:
                raise HomeAssistantError("Original-Shelly-Übergabe ist nicht bestätigt")
            if not self.bridge_active:
                for _ in range(max(1, 3 - self._offline_checks)):
                    await self._check_original()
                    if self.bridge_active or self.original_online is not False:
                        break
            else:
                # Re-check that the original did not return immediately
                # before a deliberate Cloud enable operation.
                await self._check_original()
            if not self.bridge_active:
                raise HomeAssistantError(
                    self.guard_error or "Virtuelle Instanz ist noch nicht aktiv"
                )

        await self.bridge.set_cloud_enabled(enabled)
        self.settings[CONF_CLOUD_ENABLE] = enabled
        updated_options = {**self.entry.options, CONF_CLOUD_ENABLE: enabled}
        # HA calls the config-entry update listener for option changes. The
        # bridge switch has already applied this particular change in-place;
        # a full reload would tear it down and reset the handover guard.
        self._expected_toggle_options = dict(updated_options)
        self.hass.config_entries.async_update_entry(
            self.entry, options=updated_options
        )
        for fn in tuple(self.listeners):
            fn()

    async def poll(self):
        n = 0
        while not self._closed:
            await asyncio.sleep(2)
            self.meter.update(self.hass, self.settings)
            n += 1
            if n % 3 == 0:
                await self._check_original()
            for fn in tuple(self.listeners):
                fn()
            if n % 30 == 0:
                await self.save()

    async def save(self):
        await self.store.async_save({
            "imports": self.meter.imports, "exports": self.meter.exports,
            "udp_destination": list(self.bridge.udp_dst) if self.bridge.udp_dst else None,
        })

    async def stop(self):
        self._closed = True
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        await self.bridge.stop()
        self.bridge_active = False
        await self.save()

    def subscribe(self, callback):
        self.listeners.add(callback)
        return lambda: self.listeners.discard(callback)


async def async_setup(hass: HomeAssistant, config: dict):
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry):
    hass.data.setdefault(DOMAIN, {})
    runtime = Runtime(hass, entry)
    try:
        await runtime.start()
    except Exception:
        with suppress(Exception):
            await runtime.stop()
        _LOGGER.exception("Virtueller Shelly konnte nicht starten")
        return False
    hass.data[DOMAIN][entry.entry_id] = runtime
    entry.async_on_unload(entry.add_update_listener(_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _update_listener(hass: HomeAssistant, entry):
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None and runtime._expected_toggle_options == entry.options:
        runtime._expected_toggle_options = None
        return
    if runtime is not None:
        runtime._expected_toggle_options = None
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry):
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not ok:
        return False
    runtime = hass.data[DOMAIN].pop(entry.entry_id, None)
    if runtime:
        await runtime.stop()
    return True
