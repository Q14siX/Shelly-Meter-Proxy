"""Measurement and non-secret diagnostic entities."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, CONF_ORIGINAL_INFO, CONF_IDENT, CONF_SHELLY_IP, CONF_CLOUD_KEY, CONF_CLOUD_SERVER


ENTITY_KEYS = [
    ("total", "W"),
    ("phase_a", "W"),
    ("phase_b", "W"),
    ("phase_c", "W"),
    ("available", None),
    ("cloud", None),
    ("bridge", None),
    ("original", None),
    ("error", None),
    ("original_info", None),
    ("cloud_server", None),
]


def _lang(entity) -> str:
    lang = getattr(getattr(entity, "hass", None), "config", None)
    language = getattr(lang, "language", None) or "en"
    return "de" if str(language).lower().startswith("de") else "en"


def _text(entity, de: str, en: str) -> str:
    return de if _lang(entity) == "de" else en


def _yes_no(entity, value: bool) -> str:
    return _text(entity, de="Ja", en="Yes") if value else _text(entity, de="Nein", en="No")


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN][entry.entry_id]
    entities = [ProxySensor(runtime, key, unit) for key, unit in ENTITY_KEYS]
    async_add_entities(entities)


class ProxySensor(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, runtime, key, unit):
        self.runtime = runtime
        self.key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{runtime.entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        if unit == "W":
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry.entry_id)},
            name="Shelly Meter Proxy",
            manufacturer="Community",
            model="Shelly Pro 3EM Proxy",
        )
        self._unsubscribe = None

    @property
    def native_value(self):
        m, b, r = self.runtime.meter, self.runtime.bridge, self.runtime
        if self.key == "original_info":
            return _text(self, de="Importiert", en="Imported")
        if self.key == "cloud_server":
            return b.cloud_phase
        return {
            "total": round(m.total, 2),
            "phase_a": round(m.phases[0], 2),
            "phase_b": round(m.phases[1], 2),
            "phase_c": round(m.phases[2], 2),
            "available": _yes_no(self, m.available),
            "cloud": _text(self, de="Verbunden", en="Connected") if b.cloud_connected else _text(self, de="Getrennt", en="Disconnected"),
            "bridge": _yes_no(self, r.bridge_active),
            "original": _text(self, de="Ja", en="Yes") if r.original_online else _text(self, de="Nein", en="No") if r.original_online is False else _text(self, de="Unbekannt", en="Unknown"),
            "error": r.guard_error or m.error or "OK",
        }[self.key]

    @property
    def extra_state_attributes(self):
        b, r = self.runtime.bridge, self.runtime
        if self.key == "original_info":
            existing = r.entry.data.get(CONF_ORIGINAL_INFO)
            if isinstance(existing, dict):
                return {k: v for k, v in existing.items()
                        if k in {"name", "id", "mac", "slot", "model", "gen", "fw_id",
                                 "ver", "app", "profile", "auth_en", "auth_domain",
                                 "provision", "enhanced_security", "cloud_server",
                                 "cloud_aktiviert_beim_import", "cloud_verbunden_beim_import",
                                 "cloud_key_vorhanden", "importzeitpunkt", "original_ip"}}
            ident = r.entry.data.get(CONF_IDENT, {})
            return {**{k: ident.get(k) for k in (
                "id", "mac", "model", "gen", "app", "ver", "fw_id", "profile")},
                "original_ip": r.entry.data.get(CONF_SHELLY_IP),
                "cloud_server": r.entry.data.get(CONF_CLOUD_SERVER),
                "cloud_key_vorhanden": bool(r.entry.data.get(CONF_CLOUD_KEY)),
                "hinweis": _text(self,
                                  de="Älterer Import: weitere Metadaten sind nicht gespeichert. Den echten Shelly nicht eigens dafür wieder einschalten.",
                                  en="Older import: additional metadata was not stored. Do not power on the real Shelly just for this.")}
        if self.key == "bridge":
            return {
                "original_ip": r.settings.get(CONF_SHELLY_IP),
                "virtuelle_ip": b.ip,
                "http_port": b.port,
                "abschaltpruefung_erfolgreich": r._offline_checks,
                "original_erreichbar": r.original_online,
                "sperrgrund": r.guard_error,
            }
        if self.key == "cloud_server":
            return {
                "cloud_server": b.cloud_server,
                "tls_modus": b.tls_mode,
                "tls_zertifikat_verifiziert": b.cloud_certificate_verified,
                "server_zertifikat_sha256_ungeprueft": b.cloud_server_certificate_sha256,
                "tls_fehler": b.cloud_tls_error_detail,
                "cloud_aktiviert": b.cloud_enabled,
                "websocket_verbunden": b.cloud_connected,
                "letzter_verbindungsversuch": b.cloud_last_attempt,
                "letzte_serverantwort": b.cloud_last_reply,
                "letzte_servermethode": b.cloud_last_method,
                "letzter_serverkontakt": b.cloud_last_server_contact,
                "letzter_fehler": b.cloud_last_error,
                "letzter_http_status": b.cloud_last_http_status,
                "websocket_close_code": b.cloud_last_close_code,
                "verbindungsversuche": b.cloud_attempt_count,
                "cloud_key_vorhanden": bool(b.cloud_key),
                "virtuelle_instanz_aktiv": r.bridge_active,
                "sperrgrund": r.guard_error,
                "abschaltpruefung_erfolgreich": r._offline_checks,
            }
        return None

    async def async_added_to_hass(self):
        self._unsubscribe = self.runtime.subscribe(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
