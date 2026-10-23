"""Home Assistant setup flow: import an owned Shelly identity, select HA entities."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import selector

from .const import (
    DOMAIN, CONF_MODE, CONF_TOTAL, CONF_PHASE_A, CONF_PHASE_B, CONF_PHASE_C,
    CONF_INVERT, CONF_TIMEOUT, CONF_SPLIT, CONF_SHELLY_IP, CONF_LAN_IP,
    CONF_HTTP_PORT, CONF_CLOUD_ENABLE, CONF_CLOUD_KEY, CONF_CLOUD_SERVER,
    CONF_IDENT, CONF_ORIGINAL_INFO, CONF_HANDOVER_ACK, DEFAULT_HTTP_PORT, DEFAULT_TIMEOUT,
    CONF_TLS_MODE, CONF_TLS_FINGERPRINT, TLS_VERIFY, TLS_PIN, TLS_COMPAT,
)
from .identity import (
    IdentityImportError, fetch_original_identity, is_original_reachable, resolve_ha_ip, valid_local_ip,
)
from .tls_probe import inspect_cloud_certificate
from .isolation import IsolationError, prepare_ap, isolate_original

_LOGGER = logging.getLogger(__name__)
CONF_ORIGINAL_PASSWORD = "original_password"


def ui_lang(hass) -> str:
    lang = getattr(getattr(hass, "config", None), "language", None) or "en"
    return "de" if str(lang).lower().startswith("de") else "en"


def label_for(hass, *, de: str, en: str) -> str:
    return de if ui_lang(hass) == "de" else en


def entity_input():
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["sensor", "input_number", "number"])
    )


def _marker(key: str, current: dict):
    """Avoid UNDEFINED defaults; HA frontend treats those as invalid options."""
    if key in current and current[key] not in (None, ""):
        return vol.Required(key, default=current[key])
    return vol.Required(key)


def source_schema(mode: str, settings: dict | None = None, *, advanced: bool = False, hass=None):
    """Use native HA selectors and never inject undefined selector defaults."""
    current = settings or {}
    fields = {}
    if advanced:
        fields[vol.Required(CONF_INVERT, default=bool(current.get(CONF_INVERT, False)))] = (
            selector.BooleanSelector()
        )
        fields[vol.Required(CONF_TIMEOUT, default=int(current.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)))] = (
            selector.NumberSelector(selector.NumberSelectorConfig(
                min=5, max=3600, step=1, mode=selector.NumberSelectorMode.BOX))
        )
    if mode == "total":
        fields[_marker(CONF_TOTAL, current)] = entity_input()
        fields[vol.Required(CONF_SPLIT, default=current.get(CONF_SPLIT, "equal"))] = (
            selector.SelectSelector(selector.SelectSelectorConfig(options=[
                selector.SelectOptionDict(value="equal", label=label_for(hass, de="Gleichmäßig auf L1, L2, L3 verteilen", en="Distribute evenly across L1, L2, L3")),
                selector.SelectOptionDict(value="phase_a", label=label_for(hass, de="Gesamtleistung nur L1 zuordnen", en="Assign total power to L1 only")),
            ]))
        )
    elif mode == "phases":
        for key in (CONF_PHASE_A, CONF_PHASE_B, CONF_PHASE_C):
            fields[_marker(key, current)] = entity_input()
    else:
        raise ValueError("Unknown source mode")
    return vol.Schema(fields)


def mode_selector(hass=None):
    return selector.SelectSelector(selector.SelectSelectorConfig(options=[
        selector.SelectOptionDict(value="total", label=label_for(hass, de="Ein Gesamtleistungssensor", en="One total power sensor")),
        selector.SelectOptionDict(value="phases", label=label_for(hass, de="Drei Phasensensoren", en="Three phase sensors")),
    ], mode=selector.SelectSelectorMode.DROPDOWN))


class ShellyMeterProxyFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self):
        self._settings: dict = {}

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = valid_local_ip(user_input[CONF_SHELLY_IP])
                # A configured original is managed through its Options flow.
                # Do not access the physical device or request its secret JWT
                # when the user accidentally starts a second setup.
                if any(
                    entry.data.get(CONF_SHELLY_IP) == host
                    for entry in self.hass.config_entries.async_entries(DOMAIN)
                ):
                    return self.async_abort(reason="already_configured")
                # Identify the real device first. Network source-IP discovery
                # must not mask a successful Shelly API response.
                self._original_password = user_input.get(CONF_ORIGINAL_PASSWORD, "")
                identity, key, server, original_info = await fetch_original_identity(
                    host, self._original_password
                )
                my_ip = await resolve_ha_ip(self.hass, host)
                if host == my_ip:
                    raise IdentityImportError("same_ip")
                await self.async_set_unique_id(identity["mac"])
                self._abort_if_unique_id_configured()
                self._settings = {
                    CONF_SHELLY_IP: host,
                    CONF_LAN_IP: my_ip,
                    CONF_HTTP_PORT: int(user_input.get(CONF_HTTP_PORT, DEFAULT_HTTP_PORT)),
                    CONF_IDENT: identity,
                    CONF_ORIGINAL_INFO: original_info,
                    CONF_CLOUD_KEY: key,
                    CONF_CLOUD_SERVER: server,
                    CONF_CLOUD_ENABLE: False,
                    CONF_TLS_MODE: TLS_VERIFY,
                    CONF_INVERT: False,
                    CONF_TIMEOUT: DEFAULT_TIMEOUT,
                    CONF_SPLIT: "equal",
                }
                return await self.async_step_mode()
            except AbortFlow:
                # HA uses this deliberately for duplicate unique IDs or flows
                # in progress. Never log or mislabel it as an import failure.
                raise
            except IdentityImportError as exc:
                errors["base"] = exc.reason
            except (ValueError, KeyError, TypeError):
                errors["base"] = "invalid_input"
            except Exception as exc:
                _LOGGER.error("Shelly-Einrichtung: Fehler %s (keine Zugangsdaten protokolliert)",
                              type(exc).__name__)
                errors["base"] = "unexpected_import_error"
        old = user_input or {}
        fields = {
            vol.Required(CONF_SHELLY_IP, default=old[CONF_SHELLY_IP]) if old.get(CONF_SHELLY_IP)
            else vol.Required(CONF_SHELLY_IP): str,
            vol.Optional(CONF_ORIGINAL_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }
        return self.async_show_form(step_id="user", data_schema=vol.Schema(fields), errors=errors)

    async def async_step_mode(self, user_input: dict | None = None):
        if user_input is not None:
            self._settings[CONF_MODE] = user_input[CONF_MODE]
            return await self.async_step_sources()
        return self.async_show_form(step_id="mode", data_schema=vol.Schema({
            vol.Required(CONF_MODE, default=self._settings.get(CONF_MODE, "total")): mode_selector(self.hass),
        }))

    async def async_step_sources(self, user_input: dict | None = None):
        if user_input is not None:
            self._settings.update(user_input)
            return await self.async_step_cloud_certificate()
        return self.async_show_form(
            step_id="sources", data_schema=source_schema(self._settings[CONF_MODE], self._settings, hass=self.hass)
        )

    async def async_step_cloud_certificate(self, user_input: dict | None = None):
        """Automatically use normal CA; request one-time approval only if needed."""
        if not hasattr(self, "_tls_preflight"):
            try:
                self._tls_preflight = await inspect_cloud_certificate(
                    self._settings[CONF_CLOUD_SERVER]
                )
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                # A temporarily offline server cannot block local installation;
                # leave secure CA verification enabled until a later approval.
                _LOGGER.warning("TLS-Vorprüfung vorübergehend nicht möglich: %s", type(exc).__name__)
                self._tls_preflight = {"verified": True, "fingerprint": None,
                                       "host": "", "probe_failed": True}
        result = self._tls_preflight
        if result["verified"]:
            self._settings[CONF_TLS_MODE] = TLS_VERIFY
            return await self.async_step_isolation_choice()
        errors = {}
        if user_input is not None:
            if user_input.get("certificate_confirmed") is True:
                self._settings[CONF_TLS_MODE] = TLS_PIN
                self._settings[CONF_TLS_FINGERPRINT] = result["fingerprint"]
                return await self.async_step_isolation_choice()
            errors["base"] = "certificate_confirmation_required"
        return self.async_show_form(
            step_id="cloud_certificate",
            data_schema=vol.Schema({
                vol.Required("certificate_confirmed", default=False):
                    selector.BooleanSelector(),
            }),
            description_placeholders={"server": result["host"],
                                      "fingerprint": result["fingerprint"]},
            errors=errors,
        )

    async def async_step_isolation_choice(self, user_input: dict | None = None):
        if user_input is not None:
            if user_input["method"] == "auto":
                return await self.async_step_isolation_prepare()
            return await self.async_step_handover()
        return self.async_show_form(step_id="isolation_choice", data_schema=vol.Schema({
            vol.Required("method", default="auto"):
                selector.SelectSelector(selector.SelectSelectorConfig(options=[
                    selector.SelectOptionDict(value="auto", label=label_for(self.hass, de="Access Point vorbereiten und automatisch isolieren", en="Prepare access point and isolate automatically")),
                    selector.SelectOptionDict(value="manual", label=label_for(self.hass, de="Original-Shelly selbst ausschalten", en="Switch off original Shelly manually")),
                ])),
        }))

    async def async_step_isolation_prepare(self, user_input: dict | None = None):
        errors = {}
        if user_input is not None:
            try:
                self._ap_info = await prepare_ap(
                    self._settings[CONF_SHELLY_IP], self._settings[CONF_IDENT]["id"],
                    user_input["ap_password"],
                    device_password=getattr(self, "_original_password", ""),
                )
                return await self.async_step_isolation_verify()
            except IsolationError as exc:
                errors["base"] = exc.reason
        return self.async_show_form(step_id="isolation_prepare", data_schema=vol.Schema({
            vol.Required("ap_password"): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }), errors=errors)

    async def async_step_isolation_verify(self, user_input: dict | None = None):
        errors = {}
        if user_input is not None:
            if user_input.get("ap_reachable") is not True:
                errors["base"] = "ap_test_required"
            else:
                try:
                    await isolate_original(
                        self._settings[CONF_SHELLY_IP], self._settings[CONF_IDENT]["id"],
                        device_password=getattr(self, "_original_password", ""),
                    )
                    self._settings[CONF_HANDOVER_ACK] = True
                    self._settings[CONF_CLOUD_ENABLE] = True
                    return self.async_create_entry(
                        title="Shelly Pro 3EM – Virtual Bridge", data=self._settings)
                except IsolationError as exc:
                    errors["base"] = exc.reason
        return self.async_show_form(step_id="isolation_verify", data_schema=vol.Schema({
            vol.Required("ap_reachable", default=False): selector.BooleanSelector(),
        }), errors=errors, description_placeholders={
            "ssid": self._ap_info["ssid"], "ap_ip": self._ap_info["ip"],
        })

    async def async_step_handover(self, user_input: dict | None = None):
        """Last wizard step: require offline original before the bridge is armed."""
        errors = {}
        if user_input is not None:
            if user_input.get("original_offline_confirmed") is not True:
                errors["base"] = "handover_confirmation_required"
            elif await is_original_reachable(
                self._settings[CONF_SHELLY_IP], self._settings[CONF_IDENT]["id"]
            ):
                errors["base"] = "original_still_online"
            else:
                # Physical has been deliberately disconnected and its known LAN
                # endpoint does not reply. Cloud can now be activated by runtime.
                self._settings[CONF_HANDOVER_ACK] = True
                self._settings[CONF_CLOUD_ENABLE] = True
                return self.async_create_entry(
                    title="Shelly Pro 3EM – Virtual Bridge", data=self._settings
                )
        return self.async_show_form(
            step_id="handover",
            data_schema=vol.Schema({
                vol.Required("original_offline_confirmed", default=False):
                    selector.BooleanSelector(),
            }),
            errors=errors,
            description_placeholders={"device": self._settings[CONF_IDENT]["id"]},
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return ShellyMeterProxyOptionsFlow()


class ShellyMeterProxyOptionsFlow(OptionsFlow):
    """Independent Cloud/TLS and source configuration flows.

    Old versions required saving the entire source wizard for a TLS change.
    Stopping at that page silently left the old TLS mode active. A dedicated
    cloud step stores only its own settings in one submission.
    """

    async def async_step_init(self, user_input: dict | None = None):
        return self.async_show_menu(
            step_id="init", menu_options=["trust_certificate", "sources_mode", "cloud", "original_isolation", "finish_maintenance"]
        )

    async def async_step_original_isolation(self, user_input: dict | None = None):
        """Enter maintenance safely before instructing to power the real device."""
        errors = {}
        if user_input is not None:
            if user_input.get("maintenance_confirmed") is True:
                runtime = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
                if runtime is None:
                    errors["base"] = "ap_runtime_missing"
                else:
                    await runtime.begin_maintenance()
                    return await self.async_step_original_isolation_prepare()
            else:
                errors["base"] = "ap_maintenance_required"
        return self.async_show_form(step_id="original_isolation", data_schema=vol.Schema({
            vol.Required("maintenance_confirmed", default=False): selector.BooleanSelector(),
        }), errors=errors)

    async def async_step_original_isolation_prepare(self, user_input: dict | None = None):
        errors = {}
        if user_input is not None:
            try:
                self._ap_password = user_input["ap_password"]
                self._ap_device_password = user_input.get("original_password", "")
                self._ap_info = await prepare_ap(
                    self.config_entry.data[CONF_SHELLY_IP],
                    self.config_entry.data[CONF_IDENT]["id"], self._ap_password,
                    device_password=self._ap_device_password,
                )
                return await self.async_step_original_isolation_verify()
            except IsolationError as exc:
                errors["base"] = exc.reason
        return self.async_show_form(step_id="original_isolation_prepare", data_schema=vol.Schema({
            vol.Required("ap_password"): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional("original_password"): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }), errors=errors)

    async def async_step_original_isolation_verify(self, user_input: dict | None = None):
        errors = {}
        if user_input is not None:
            if user_input.get("ap_reachable") is not True:
                errors["base"] = "ap_test_required"
            else:
                try:
                    await isolate_original(
                        self.config_entry.data[CONF_SHELLY_IP],
                        self.config_entry.data[CONF_IDENT]["id"],
                        device_password=getattr(self, "_ap_device_password", ""),
                    )
                    opts = {**self.config_entry.options,
                            CONF_CLOUD_ENABLE: True, "maintenance_mode": False,
                            CONF_HANDOVER_ACK: True}
                    return self.async_create_entry(title="", data=opts)
                except IsolationError as exc:
                    errors["base"] = exc.reason
        return self.async_show_form(step_id="original_isolation_verify", data_schema=vol.Schema({
            vol.Required("ap_reachable", default=False): selector.BooleanSelector(),
        }), errors=errors, description_placeholders={
            "ssid": self._ap_info["ssid"], "ap_ip": self._ap_info["ip"],
        })

    async def async_step_finish_maintenance(self, user_input: dict | None = None):
        errors = {}
        if user_input is not None:
            if user_input.get("offline_confirmed") is not True:
                errors["base"] = "handover_confirmation_required"
            elif await is_original_reachable(
                self.config_entry.data[CONF_SHELLY_IP],
                self.config_entry.data[CONF_IDENT]["id"]):
                errors["base"] = "original_still_online"
            else:
                return self.async_create_entry(title="", data={
                    **self.config_entry.options,
                    "maintenance_mode": False,
                    CONF_CLOUD_ENABLE: True,
                })
        return self.async_show_form(step_id="finish_maintenance", data_schema=vol.Schema({
            vol.Required("offline_confirmed", default=False): selector.BooleanSelector(),
        }), errors=errors)

    async def async_step_trust_certificate(self, user_input: dict | None = None):
        """One-click fingerprint trust approval for an existing installation."""
        errors = {}
        if not hasattr(self, "_tls_preflight"):
            try:
                self._tls_preflight = await inspect_cloud_certificate(
                    self.config_entry.data[CONF_CLOUD_SERVER]
                )
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                _LOGGER.warning("Cloud-Zertifikatsprüfung fehlgeschlagen: %s", type(exc).__name__)
                return self.async_abort(reason="cloud_certificate_unreachable")
        result = self._tls_preflight
        if result["verified"]:
            return self.async_create_entry(title="", data={
                **self.config_entry.options,
                CONF_TLS_MODE: TLS_VERIFY,
                CONF_TLS_FINGERPRINT: "",
            })
        if user_input is not None:
            if user_input.get("certificate_confirmed") is True:
                return self.async_create_entry(title="", data={
                    **self.config_entry.options,
                    CONF_TLS_MODE: TLS_PIN,
                    CONF_TLS_FINGERPRINT: result["fingerprint"],
                })
            errors["base"] = "certificate_confirmation_required"
        return self.async_show_form(
            step_id="trust_certificate",
            data_schema=vol.Schema({
                vol.Required("certificate_confirmed", default=False):
                    selector.BooleanSelector(),
            }),
            description_placeholders={"server": result["host"],
                                      "fingerprint": result["fingerprint"]},
            errors=errors,
        )

    async def async_step_cloud(self, user_input: dict | None = None):
        errors = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            self._cloud = bool(user_input[CONF_CLOUD_ENABLE])
            self._tls_mode = user_input[CONF_TLS_MODE]
            self._tls_fingerprint = str(
                user_input.get(CONF_TLS_FINGERPRINT) or ""
            ).replace(":", "").replace(" ", "").lower()
            if self._tls_mode == TLS_PIN and (
                len(self._tls_fingerprint) != 64 or
                any(c not in "0123456789abcdef" for c in self._tls_fingerprint)
            ):
                errors["base"] = "invalid_tls_fingerprint"
            else:
                self._pending_options = {
                    **self.config_entry.options,
                    CONF_CLOUD_ENABLE: self._cloud,
                    CONF_TLS_MODE: self._tls_mode,
                    CONF_TLS_FINGERPRINT: self._tls_fingerprint,
                }
                if self._cloud and not current.get(CONF_HANDOVER_ACK, False):
                    return await self.async_step_handover()
                if self._tls_mode == TLS_COMPAT:
                    return await self.async_step_tls_warning()
                # IMPORTANT: this creates the options entry directly.
                # There is NO follow-on source form that the user might abandon.
                return self.async_create_entry(title="", data=self._pending_options)
        values = {**current, **(user_input or {})}
        return self.async_show_form(step_id="cloud", data_schema=vol.Schema({
            vol.Required(CONF_CLOUD_ENABLE, default=bool(values.get(CONF_CLOUD_ENABLE, False))):
                selector.BooleanSelector(),
            vol.Required(CONF_TLS_MODE, default=values.get(CONF_TLS_MODE, TLS_VERIFY)):
                selector.SelectSelector(selector.SelectSelectorConfig(options=[
                    selector.SelectOptionDict(value=TLS_VERIFY, label=label_for(self.hass, de="Zertifikat prüfen", en="Verify certificate")),
                    selector.SelectOptionDict(value=TLS_PIN, label=label_for(self.hass, de="SHA-256-Zertifikatfingerprint prüfen", en="Verify SHA-256 certificate fingerprint")),
                    selector.SelectOptionDict(value=TLS_COMPAT, label=label_for(self.hass, de="Kompatibilitätsmodus (ohne Zertifikatsprüfung)", en="Compatibility mode (without certificate verification)")),
                ])),
            vol.Optional(CONF_TLS_FINGERPRINT, default=values.get(CONF_TLS_FINGERPRINT, "")):
                selector.TextSelector(),
        }), errors=errors)

    async def async_step_sources_mode(self, user_input: dict | None = None):
        values = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            self._mode = user_input[CONF_MODE]
            return await self.async_step_sources()
        return self.async_show_form(step_id="sources_mode", data_schema=vol.Schema({
            vol.Required(CONF_MODE, default=values.get(CONF_MODE, "total")): mode_selector(self.hass),
        }))

    async def async_step_sources(self, user_input: dict | None = None):
        values = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            source_keys = {
                CONF_TOTAL, CONF_PHASE_A, CONF_PHASE_B, CONF_PHASE_C,
                CONF_INVERT, CONF_TIMEOUT, CONF_SPLIT,
            }
            # When the source mode is changed, remove obsolete keys from the
            # opposite mode rather than retaining and confusing the user.
            source_options = {k: v for k, v in self.config_entry.options.items()
                              if k not in (source_keys - {CONF_INVERT, CONF_TIMEOUT})}
            source_options.update({CONF_MODE: self._mode})
            source_options.update({k: v for k, v in user_input.items() if k in source_keys})
            return self.async_create_entry(title="", data=source_options)
        return self.async_show_form(
            step_id="sources", data_schema=source_schema(self._mode, values, advanced=True, hass=self.hass)
        )

    async def async_step_handover(self, user_input: dict | None = None):
        errors = {}
        if user_input is not None:
            if user_input.get("original_offline_confirmed") is not True:
                errors["base"] = "handover_confirmation_required"
            elif await is_original_reachable(
                self.config_entry.data[CONF_SHELLY_IP],
                self.config_entry.data[CONF_IDENT]["id"],
            ):
                errors["base"] = "original_still_online"
            else:
                self._pending_options[CONF_HANDOVER_ACK] = True
                if self._tls_mode == TLS_COMPAT:
                    return await self.async_step_tls_warning()
                return self.async_create_entry(title="", data=self._pending_options)
        return self.async_show_form(
            step_id="handover",
            data_schema=vol.Schema({
                vol.Required("original_offline_confirmed", default=False):
                    selector.BooleanSelector(),
            }),
            errors=errors,
            description_placeholders={"device": self.config_entry.data[CONF_IDENT]["id"]},
        )

    async def async_step_tls_warning(self, user_input: dict | None = None):
        """Explicit opt-in; TLS encryption continues, peer auth is disabled."""
        errors = {}
        if user_input is not None:
            if user_input.get("tls_risk_accepted") is True:
                return self.async_create_entry(title="", data=self._pending_options)
            errors["base"] = "tls_ack_required"
        return self.async_show_form(
            step_id="tls_warning",
            data_schema=vol.Schema({
                vol.Required("tls_risk_accepted", default=False): selector.BooleanSelector()
            }),
            errors=errors,
        )
