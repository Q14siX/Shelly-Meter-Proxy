"""Opt-in isolation of a physical Shelly Pro 3EM using official Gen2 RPC.

AP reachability cannot be verified from Home Assistant: user must check it
independently before internet/LAN uplinks are disabled. Changes are not
performed merely by opening a settings page.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re

import aiohttp

_LOGGER = logging.getLogger(__name__)

class IsolationError(Exception):
    """A non-secret, localized error code."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _rpc_session(password):
    kwargs = {"timeout": aiohttp.ClientTimeout(total=9, connect=4), "trust_env": False}
    if password:
        digest = getattr(aiohttp, "DigestAuthMiddleware", None)
        if digest is None:
            raise IsolationError("auth_unavailable")
        kwargs["middlewares"] = (digest("admin", password),)
    return aiohttp.ClientSession(**kwargs)


async def _rpc(session, ip, method, params=None):
    try:
        async with session.post(f"http://{ip}/rpc", json={
            "id": 1, "method": method, "params": params or {},
        }, allow_redirects=False) as resp:
            if resp.status in (401, 403):
                raise IsolationError("ap_auth")
            if resp.status != 200:
                raise IsolationError("ap_rpc_failed")
            result = await resp.json(content_type=None)
    except IsolationError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise IsolationError("ap_network_failed") from exc
    if not isinstance(result, dict) or "error" in result:
        raise IsolationError("ap_rpc_failed")
    value = result.get("result", result)
    if not isinstance(value, dict):
        raise IsolationError("ap_rpc_failed")
    return value


async def _check_id(session, ip, expected_id):
    info = await _rpc(session, ip, "Shelly.GetDeviceInfo")
    if info.get("id") != expected_id:
        raise IsolationError("ap_wrong_device")


async def prepare_ap(ip, expected_id, password, *, device_password=""):
    """Enable a secured AP and disable its routing, without dropping LAN/WLAN."""
    if not isinstance(password, str) or not 8 <= len(password) <= 63:
        raise IsolationError("ap_invalid_password")
    async with _rpc_session(device_password) as session:
        await _check_id(session, ip, expected_id)
        before = await _rpc(session, ip, "WiFi.GetConfig")
        ap = before.get("ap") or {}
        params = {"ap": {"enable": True, "is_open": False, "pass": password}}
        if isinstance(before.get("range_extender"), dict):
            params["range_extender"] = {"enable": False}
        await _rpc(session, ip, "WiFi.SetConfig", {"config": params})
        current = await _rpc(session, ip, "WiFi.GetConfig")
        ap = current.get("ap") or {}
        if not ap.get("enable") or ap.get("is_open") is not False:
            raise IsolationError("ap_not_secure")
        if (isinstance(current.get("range_extender"), dict)
                and current["range_extender"].get("enable") is True):
            raise IsolationError("ap_range_extender")
        return {"ssid": ap.get("ssid") or expected_id, "ip": "192.168.33.1"}


async def isolate_original(ip, expected_id, *, device_password=""):
    """After user verifies AP: disable uplinks, but preserve Cloud config.

    This disconnects the original from the Internet without changing its
    provisioned Cloud account or credentials. A physical recovery is needed
    if network deactivation was partially applied. Configuration
    is applied over the active network with the active transport shut down last.
    A loss of HTTP while applying is reported as ambiguous, not success.
    """
    async with _rpc_session(device_password) as session:
        await _check_id(session, ip, expected_id)
        wifi = await _rpc(session, ip, "WiFi.GetConfig")
        if not (wifi.get("ap") or {}).get("enable") or (wifi.get("ap") or {}).get("is_open") is not False:
            raise IsolationError("ap_not_secure")
        eth = await _rpc(session, ip, "Eth.GetConfig")
        wifi_status = await _rpc(session, ip, "WiFi.GetStatus")
        # Do not call Cloud.SetConfig. The existing cloud identity on the
        # physical device stays provisioned for future restoration; only the
        # WLAN station profiles and Ethernet uplink are disconnected.
        # If current HTTP endpoint is WiFi STA, disconnect it last.
        wifi_is_primary = str(wifi_status.get("sta_ip") or "") == str(ip)
        async def stop_wifi():
            cfg = {}
            for station in ("sta", "sta1"):
                if isinstance(wifi.get(station), dict):
                    cfg[station] = {"enable": False}
            if cfg:
                await _rpc(session, ip, "WiFi.SetConfig", {"config": cfg})
        async def stop_eth():
            if eth.get("enable") is True:
                return await _rpc(session, ip, "Eth.SetConfig", {"config": {"enable": False}})
            return {"restart_required": False}
        if wifi_is_primary:
            eth_result = await stop_eth()
            await stop_wifi()
        else:
            await stop_wifi()
            eth_result = await stop_eth()
        # Ethernet requires reboot on Gen2; if we are still connected,
        # request one. Losing connectivity after the final call is expected.
        if eth_result.get("restart_required"):
            try:
                await _rpc(session, ip, "Shelly.Reboot")
            except IsolationError:
                pass
    return True
