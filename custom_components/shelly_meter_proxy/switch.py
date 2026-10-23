"""User-controlled Shelly Cloud transport enable/disable."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CloudSwitch(runtime)])


class CloudSwitch(SwitchEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "cloud_switch"

    def __init__(self, runtime):
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_cloud_switch"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry.entry_id)},
            name="Shelly Meter Proxy",
            manufacturer="Community",
            model="Shelly Pro 3EM Proxy",
        )
        self._unsubscribe = None

    @property
    def is_on(self) -> bool:
        return bool(self.runtime.bridge.cloud_enabled)

    @property
    def extra_state_attributes(self):
        return {
            "websocket_verbunden": self.runtime.bridge.cloud_connected,
            "cloud_serverstatus": self.runtime.bridge.cloud_phase,
        }

    async def async_turn_on(self, **kwargs):
        await self.runtime.set_cloud_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self.runtime.set_cloud_enabled(False)
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        self._unsubscribe = self.runtime.subscribe(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
