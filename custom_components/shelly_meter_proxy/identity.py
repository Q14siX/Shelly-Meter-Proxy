"""Import a user-owned Shelly Pro 3EM identity through its documented local RPC API.

No modifications are made to the physical meter. Never write cloud credentials to logs.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)


class IdentityImportError(Exception):
    """Localized Home Assistant form error; reason must be in strings.json."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def valid_local_ip(value: str) -> str:
    try:
        ip = ipaddress.ip_address(str(value).strip())
    except ValueError as exc:
        raise IdentityImportError("invalid_ip") from exc
    if (ip.version != 4 or not ip.is_private or ip.is_loopback
            or ip.is_unspecified or ip.is_multicast or ip.is_link_local):
        raise IdentityImportError("invalid_ip")
    return str(ip)


async def resolve_ha_ip(hass, target_ip: str) -> str:
    """Find the IPv4 address routed towards the physical Shelly device.

    Use the kernel's route selection first: this does not send packets and it
    works even when the Home Assistant network component is unavailable,
    misconfigured or raising HomeAssistantError. Fall back to the HA network
    integration if the socket-based lookup is unavailable.
    """
    host = valid_local_ip(target_ip)

    def route_ip() -> str:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((host, 80))
            return sock.getsockname()[0]

    try:
        ip = await hass.async_add_executor_job(route_ip)
        result = valid_local_ip(ip)
        if result != host:
            return result
    except (OSError, ValueError, TypeError, IdentityImportError) as exc:
        _LOGGER.debug("Kernel-Routenauswahl nicht verfügbar: %s", type(exc).__name__)

    try:
        from homeassistant.components.network import async_get_source_ip
        result = valid_local_ip(await async_get_source_ip(hass, target_ip=host))
        if result != host:
            return result
    except Exception as exc:
        # HomeAssistantError does not inherit from OSError/RuntimeError on all
        # HA versions. Never turn a failure of automatic IP discovery into a
        # bogus 'Shelly unreachable' error.
        _LOGGER.warning("Home-Assistant-IP konnte nicht ermittelt werden (%s)",
                        type(exc).__name__)
    raise IdentityImportError("ha_ip_unavailable")


def _normalize_cloud_server(value: object) -> str:
    """Retain supported endpoint information; never invent a Cloud URL."""
    if not isinstance(value, str) or not value.strip():
        raise IdentityImportError("cloud_server_missing")
    raw = value.strip()
    if raw.startswith("wss://"):
        raw = raw[6:]
    elif raw.startswith("ws://"):
        raise IdentityImportError("cloud_server_missing")
    parsed = urlsplit("wss://" + raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise IdentityImportError("cloud_server_missing") from exc
    if (not parsed.hostname or port is None or port < 1 or port > 65535
            or parsed.path != "/jrpc" or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise IdentityImportError("cloud_server_missing")
    return raw


async def is_original_reachable(ip: str, expected_id: str) -> bool:
    """Fail closed for any HTTP response on the physical device's address.

    We cannot establish Cloud disconnection by LAN probe alone; user
    confirmation is also mandatory. An HTTP response (even 401/403 or a
    different identity) means the configured IP is NOT safely released.
    """
    host = valid_local_ip(ip)
    timeout = aiohttp.ClientTimeout(total=3, connect=2)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.get(
                f"http://{host}/rpc/Shelly.GetDeviceInfo",
                allow_redirects=False,
            ) as response:
                # Any HTTP response means there is still a device using this IP.
                # A matching id confirms it's the original; a different response
                # must NOT allow hijacking a network address.
                return True
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return False


async def fetch_original_identity(ip: str, password: str = "") -> tuple[dict, str, str, dict]:
    """Return (identity, cloud JWT, server, safe original diagnostics).

    Distinct errors are provided for TCP reachability, authentication,
    individual RPC endpoints, provisioning and Cloud settings.
    Cloud.GetStatus is diagnostic-only and is not required for export.
    """
    host = valid_local_ip(ip)
    kwargs = {
        "timeout": aiohttp.ClientTimeout(total=12, connect=5, sock_read=7),
        "trust_env": False,
    }
    if password:
        middleware = getattr(aiohttp, "DigestAuthMiddleware", None)
        if middleware is None:
            raise IdentityImportError("auth_unavailable")
        kwargs["middlewares"] = (middleware("admin", password),)

    async def rpc(session: aiohttp.ClientSession, method: str, params=None) -> dict:
        """Use direct GET RPC, which returns the result object, not an RPC frame."""
        url = f"http://{host}/rpc/{method}"
        try:
            async with session.get(url, params=params, allow_redirects=False) as response:
                if response.status == 401:
                    raise IdentityImportError("auth_required")
                if response.status == 403:
                    raise IdentityImportError("auth_denied")
                if response.status in (301, 302, 303, 307, 308):
                    raise IdentityImportError("unexpected_redirect")
                if response.status == 404:
                    raise IdentityImportError("rpc_unavailable")
                if response.status >= 400:
                    _LOGGER.warning("Shelly RPC %s antwortete mit HTTP %d", method, response.status)
                    raise IdentityImportError("rpc_http_error")
                try:
                    value = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError, UnicodeError) as exc:
                    raise IdentityImportError("invalid_rpc_response") from exc
        except IdentityImportError:
            raise
        except asyncio.TimeoutError as exc:
            _LOGGER.info("Zeitüberschreitung beim Shelly RPC %s (IP %s)", method, host)
            raise IdentityImportError("shelly_timeout") from exc
        except aiohttp.ClientConnectorError as exc:
            _LOGGER.info("Shelly RPC %s TCP-Verbindung fehlgeschlagen an %s", method, host)
            raise IdentityImportError("shelly_connection_failed") from exc
        except (aiohttp.ClientError, OSError) as exc:
            _LOGGER.warning("Shelly RPC %s: %s", method, type(exc).__name__)
            raise IdentityImportError("shelly_connection_failed") from exc
        if not isinstance(value, dict):
            raise IdentityImportError("invalid_rpc_response")
        # Some gateways return the RPC frame even at the GET endpoint.
        if isinstance(value.get("result"), dict):
            value = value["result"]
        elif isinstance(value.get("params"), dict) and "src" in value:
            value = value["params"]
        if "error" in value:
            _LOGGER.warning("Shelly RPC %s lehnte die Abfrage ab", method)
            raise IdentityImportError("rpc_method_rejected")
        return value

    status_at_import = None
    try:
        async with aiohttp.ClientSession(**kwargs) as session:
            # GetDeviceInfo is intentionally not dependent on Cloud.GetStatus.
            info = await rpc(session, "Shelly.GetDeviceInfo", {"ident": "true"})
            try:
                generation = int(info.get("gen", 0))
            except (ValueError, TypeError):
                generation = 0
            model = str(info.get("model") or "")
            app = str(info.get("app") or "")
            if generation != 2 or (app.lower() != "pro3em" and not model.startswith("SPEM-003")):
                raise IdentityImportError("unsupported_device")
            if not info.get("id") or not info.get("mac") or not model:
                raise IdentityImportError("device_info_incomplete")
            cloud_key = info.get("key")
            if not isinstance(cloud_key, str) or not cloud_key.strip():
                raise IdentityImportError("cloud_key_missing")
            cloud_cfg = await rpc(session, "Cloud.GetConfig")
            if cloud_cfg.get("enable") is not True:
                raise IdentityImportError("cloud_disabled")
            server = _normalize_cloud_server(cloud_cfg.get("server"))
            # Cloud.GetStatus is not required: a short-lived Cloud disconnect
            # must not be confused with an inaccessible original device.
            try:
                status = await rpc(session, "Cloud.GetStatus")
                if isinstance(status.get("connected"), bool):
                    status_at_import = status["connected"]
                if status.get("connected") is False:
                    _LOGGER.info("Shelly Cloud ist derzeit nicht verbunden, Import läuft dennoch weiter")
            except IdentityImportError as exc:
                _LOGGER.info("Shelly Cloud-Status nicht verfügbar (%s), Import läuft weiter", exc.reason)
    except IdentityImportError:
        raise
    except Exception as exc:
        _LOGGER.error("Shelly-Import: unerwarteter Fehler vom Typ %s (keine Zugangsdaten geloggt)",
                      type(exc).__name__)
        raise IdentityImportError("unexpected_import_error") from exc

    identity = {key: info.get(key) for key in (
        "id", "mac", "model", "gen", "app", "ver", "fw_id", "profile"
    )}
    identity["mac"] = str(identity["mac"]).replace(":", "").replace("-", "").upper()
    identity["gen"] = generation
    # Persist only the expected, non-secret fields. The original is now
    # disconnected; these values are an import-time snapshot, NOT live values.
    safe = {k: info.get(k) for k in (
        "name", "id", "mac", "slot", "model", "gen", "fw_id", "ver",
        "app", "profile", "auth_en", "auth_domain", "provision",
        "enhanced_security",
    ) if k in info}
    safe.update({
        "cloud_server": server,
        "cloud_aktiviert_beim_import": True,
        "cloud_verbunden_beim_import": status_at_import,
        "cloud_key_vorhanden": True,
        "importzeitpunkt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "original_ip": host,
    })
    return identity, cloud_key, server, safe
