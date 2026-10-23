"""Independent Shelly Gen2 RPC, mDNS, UDP and Shelly Cloud transport.

Experimental compatibility implementation: neither Shelly nor Zendure endorse it.
"""
from __future__ import annotations

import asyncio
import hashlib
import ssl
from urllib.parse import urlsplit
import ipaddress
import json
import logging
import re
from datetime import datetime, timezone
import socket
import time
from contextlib import suppress
from typing import Any

import aiohttp
from aiohttp import web
from homeassistant.components.zeroconf import async_get_instance
from zeroconf import ServiceInfo

from .const import (CONF_CLOUD_ENABLE, CONF_CLOUD_KEY, CONF_CLOUD_SERVER,
                    CONF_TLS_MODE, CONF_TLS_FINGERPRINT, TLS_VERIFY, TLS_PIN, TLS_COMPAT)

_LOGGER = logging.getLogger(__name__)


class RpcFailure(Exception):
    """Shelly Gen2 RPC failure with standard error frame."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _decode_query_params(query) -> dict:
    """Decode JSON-object/array query arguments supported by Shelly Gen2."""
    values = {}
    for name, raw in query.items():
        if raw == "true":
            values[name] = True
        elif raw == "false":
            values[name] = False
        else:
            try:
                values[name] = json.loads(raw) if raw and raw[0] in '[{\"' else raw
            except (json.JSONDecodeError, TypeError):
                values[name] = raw
    return values


_SUPPORTED_METHODS = (
    "Shelly.GetDeviceInfo", "Shelly.GetStatus", "Shelly.GetConfig",
    "Shelly.GetComponents", "Shelly.ListMethods", "Shelly.ListProfiles",
    "Shelly.Reboot", "Shelly.CheckForUpdate", "EM.GetStatus",
    "EM.GetConfig", "EM.GetCTTypes", "EMData.GetStatus",
    "EMData.GetConfig", "EMData.GetData", "Sys.GetStatus",
    "Sys.GetConfig", "Sys.SetConfig", "Cloud.GetStatus",
    "Cloud.GetConfig", "Cloud.SetConfig", "WiFi.GetStatus",
    "WiFi.GetConfig", "Eth.GetStatus", "Eth.GetConfig",
    "MQTT.GetStatus", "MQTT.GetConfig", "Temperature.GetStatus",
    "Schedule.List", "Webhook.List", "Script.List", "KVS.List",
)


class ShellyBridge:
    def __init__(self, hass, meter, settings: dict, identity: dict):
        self.hass = hass
        self.meter = meter
        self.settings = settings
        self.identity = identity
        self.device_id = identity["id"]
        self.ip = settings["lan_ip"]
        self.port = int(settings["http_port"])
        self.cloud_server = settings.get(CONF_CLOUD_SERVER, "")
        self.cloud_key = settings.get(CONF_CLOUD_KEY, "")
        self.cloud_enabled = settings.get(CONF_CLOUD_ENABLE, False)
        self.tls_mode = settings.get(CONF_TLS_MODE, TLS_VERIFY)
        self.tls_fingerprint = (settings.get(CONF_TLS_FINGERPRINT) or "").replace(":", "").replace(" ", "").lower()
        self.cloud_server_certificate_sha256 = None
        self.cloud_certificate_verified = False
        self.cloud_tls_error_detail = None
        self.cloud_connected = False
        self.cloud_phase = "Nicht gestartet"
        self.cloud_last_attempt = None
        self.cloud_last_reply = "Noch keine Serverantwort"
        self.cloud_last_method = None
        self.cloud_last_server_contact = None
        self.cloud_last_error = None
        self.cloud_last_http_status = None
        self.cloud_last_close_code = None
        self.cloud_attempt_count = 0
        self.udp_dst: tuple[str, int] | None = None
        self.peer: str = "*"
        self.boot = time.monotonic()
        self._runner = None
        self._zeroconf = None
        self._records = []
        self._udp = []
        self._udp_push = None
        self._cloud_task = None
        self._ws_clients = set()
        self._ws_sources = {}
        self._last_push = 0.0

    async def start(self):
        app = web.Application(client_max_size=8192)
        app.router.add_get("/shelly", self.handle_shelly)
        app.router.add_get("/rpc", self.handle_ws)
        app.router.add_get("/rpc/{method}", self.handle_get)
        app.router.add_post("/rpc", self.handle_post)
        app.router.add_post("/rpc/{method}", self.handle_post)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.ip, self.port).start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner
        await self.register_mdns()
        loop = asyncio.get_running_loop()
        for port in (1010, 2220, 8006):
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda p=port: UdpHandler(self, p), local_addr=("0.0.0.0", port),
                    allow_broadcast=True)
                self._udp.append((port, transport))
            except OSError as exc:
                _LOGGER.warning("UDP Port %s belegt: %s", port, exc)
        self._udp_push = asyncio.create_task(self.push_loop())
        if self.cloud_enabled:
            await self.set_cloud_enabled(True)
        else:
            self.cloud_phase = "Deaktiviert"

    async def stop(self):
        for attr in ("_udp_push", "_cloud_task"):
            task = getattr(self, attr)
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                setattr(self, attr, None)
        for ws in tuple(self._ws_clients):
            with suppress(Exception):
                await ws.close()
        for _, transport in self._udp:
            transport.close()
        self._udp.clear()
        await self.unregister_mdns()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self.cloud_connected = False
        self.cloud_phase = "Angehalten"

    async def set_cloud_enabled(self, enabled: bool):
        """Actively close/reopen the cloud connection; never expose the JWT."""
        self.cloud_enabled = enabled
        task = self._cloud_task
        if not enabled:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self._cloud_task = None
            self.cloud_connected = False
            self.cloud_phase = "Deaktiviert"
            self.cloud_last_error = None
            return
        if not self.cloud_server or not self.cloud_key:
            self.cloud_phase = "Fehlende Cloud-Zugangsdaten"
            self.cloud_last_error = "Cloud-Server oder Cloud-Schlüssel fehlt"
            return
        if self._runner is None:
            self.cloud_phase = "Wartet auf virtuelle Instanz"
            return
        if task is None or task.done():
            self._cloud_task = asyncio.create_task(self.cloud_loop())

    @staticmethod
    def _now_utc():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _safe_server_text(self, value: object) -> str:
        """Remove known secrets/JWT patterns before persisting diagnostics."""
        output = str(value)
        if self.cloud_key:
            output = output.replace(self.cloud_key, "[GESCHÜTZT]")
        output = re.sub(r"[A-Za-z0-9_-]{14,}\.[A-Za-z0-9_-]{14,}\.[A-Za-z0-9_-]{14,}",
                        "[JWT GESCHÜTZT]", output)
        output = re.sub(r"(?i)(?:token|key|authorization|password)\s*[:=]\s*[^\s,;}]+",
                        "[ZUGANGSDATEN GESCHÜTZT]", output)
        return output[:350]

    def _capture_server_reply(self, payload: object):
        """Store only allowlisted fields, never an entire untrusted RPC body."""
        if not isinstance(payload, dict):
            return
        safe = {"typ": "Serveranfrage" if "method" in payload else "Serverantwort"}
        method = payload.get("method")
        if isinstance(method, str):
            self.cloud_last_method = method[:80]
            safe["methode"] = self.cloud_last_method
        identifier = payload.get("id")
        if type(identifier) in (int, str) and len(str(identifier)) <= 30:
            safe["id"] = identifier
        err = payload.get("error")
        if isinstance(err, dict):
            if type(err.get("code")) is int:
                safe["fehlercode"] = err["code"]
            if isinstance(err.get("message"), str):
                safe["fehlermeldung"] = self._safe_server_text(err["message"])
                self.cloud_last_error = "Cloud-Server: " + safe["fehlermeldung"]
                self.cloud_phase = "Serverfehler"
        elif isinstance(err, str):
            safe["fehlermeldung"] = self._safe_server_text(err)
            self.cloud_last_error = "Cloud-Server: " + safe["fehlermeldung"]
            self.cloud_phase = "Serverfehler"
        result = payload.get("result")
        if isinstance(result, dict):
            safe["ergebnis_felder"] = sorted(str(k)[:40] for k in result.keys())[:20]
            for key in ("connected", "status", "code"):
                if key in result and type(result[key]) in (bool, int, str):
                    safe[key] = self._safe_server_text(result[key])
        if method == "Shelly.GetDeviceInfo":
            params = payload.get("params")
            safe["ident_angefordert"] = isinstance(params, dict) and params.get("ident") is True
        self.cloud_last_reply = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))[:1024]
        self.cloud_last_server_contact = self._now_utc()

    async def register_mdns(self):
        try:
            self._zeroconf = await async_get_instance(self.hass)
            mac = self.identity["mac"].replace(":", "").upper()
            instance = f"{'ShellyPro3EM63' if '63' in str(self.identity.get('model', '')) else 'ShellyPro3EM'}-{mac}"
            address = socket.inet_aton(self.ip)
            for service in ("_shelly._tcp.local.", "_http._tcp.local."):
                record = ServiceInfo(
                    type_=service,
                    name=f"{instance}.{service}",
                    addresses=[address],
                    port=self.port,
                    server=f"{instance}.local.",
                    properties={
                        "gen": "2", "app": "Pro3EM", "id": self.device_id,
                        "ver": str(self.identity.get("ver") or "1.7.1"),
                        "discoverable": "true",
                    },
                )
                try:
                    await self.hass.async_add_executor_job(self._zeroconf.register_service, record)
                    self._records.append(record)
                except Exception as exc:
                    _LOGGER.warning("mDNS-Anmeldung fehlgeschlagen: %s", exc)
        except Exception as exc:
            _LOGGER.warning("mDNS nicht verfügbar: %s", exc)

    async def unregister_mdns(self):
        for record in self._records:
            with suppress(Exception):
                await self.hass.async_add_executor_job(self._zeroconf.unregister_service, record)
        self._records.clear()

    def device_info(self, ident=False):
        data = dict(self.identity)
        data.update({"auth_en": False, "auth_domain": None})
        data.pop("discoverable", None)
        if ident and self.cloud_key:
            data["key"] = self.cloud_key
        return data

    def status(self):
        return {
            "sys": {
                "mac": self.identity["mac"], "restart_required": False,
                "time": time.strftime("%H:%M"), "unixtime": int(time.time()),
                "uptime": int(time.monotonic() - self.boot),
                "ram_size": 262144, "ram_free": 130000,
                "fs_size": 1048576, "fs_free": 550000,
                "cfg_rev": 1, "kvs_rev": 0,
            },
            "wifi": {"sta_ip": self.ip, "status": "got ip", "ssid": "Home Assistant", "rssi": -45},
            "eth": {"ip": self.ip, "ip6": None},
            "cloud": {"connected": self.cloud_connected},
            "mqtt": {"connected": False},
            "em:0": self.meter.em_status(),
            "emdata:0": self.meter.emdata_status(),
            "temperature:0": {"id": 0, "tC": 35.0, "tF": 95.0},
        }

    def config(self):
        return {
            "sys": {"device": {"name": "Shelly Meter Proxy", "mac": self.identity["mac"],
                               "fw_id": self.identity.get("fw_id"), "discoverable": True,
                               "profile": "triphase"},
                    "rpc_udp": {"listen_port": 8006,
                                "dst_addr": f"{self.udp_dst[0]}:{self.udp_dst[1]}" if self.udp_dst else None}},
            "wifi": {"sta": {"enable": True, "ipv4mode": "dhcp"}, "ap": {"enable": False}},
            "eth": {"enable": False},
            "cloud": {"enable": self.cloud_enabled, "server": self.cloud_server},
            "mqtt": {"enable": False},
            "em:0": {"id": 0, "name": None,
                     "blink_mode_selector": "active_energy", "phase_selector": "all",
                     "monitor_phase_sequence": False,
                     "ct_type": "3x63A" if "63" in str(self.identity.get("model", "")) else "3x120A",
                     "reverse": {}},
            "emdata:0": {"id": 0},
        }

    def components(self, params=None):
        """Shelly.GetComponents supports include, keys, dynamic_only and offset."""
        params = params or {}
        s, c = self.status(), self.config()
        all_keys = ("sys", "wifi", "eth", "cloud", "mqtt", "em:0", "emdata:0", "temperature:0")
        include = params.get("include", [])
        keys = params.get("keys", [])
        if isinstance(include, str):
            try:
                include = json.loads(include)
            except json.JSONDecodeError:
                raise RpcFailure(-103, "Invalid include") from None
        if isinstance(keys, str):
            try:
                keys = json.loads(keys)
            except json.JSONDecodeError:
                raise RpcFailure(-103, "Invalid keys") from None
        if not isinstance(include, list) or not isinstance(keys, list):
            raise RpcFailure(-103, "Invalid component filter")
        try:
            offset = int(params.get("offset", 0))
        except (ValueError, TypeError) as exc:
            raise RpcFailure(-103, "Invalid offset") from exc
        if offset < 0:
            raise RpcFailure(-103, "Invalid offset")
        matches = [] if params.get("dynamic_only", False) in (True, "true") else [
            k for k in all_keys if not keys or k in keys
        ]
        output = []
        for k in matches[offset:offset+50]:
            item = {"key": k}
            if "status" in include:
                item["status"] = s.get(k, {})
            if "config" in include:
                item["config"] = c.get(k, {})
            output.append(item)
        return {"components": output, "cfg_rev": 1, "offset": offset, "total": len(matches)}

    @staticmethod
    def _check_component_id(params):
        if "id" not in params:
            raise RpcFailure(-103, "Missing required 'id'")
        if isinstance(params["id"], bool):
            raise RpcFailure(-103, "Invalid id")
        try:
            value = int(params["id"])
        except (TypeError, ValueError) as exc:
            raise RpcFailure(-103, "Invalid id") from exc
        if value != 0:
            raise RpcFailure(-105, f"Bad id={value}")
        return value

    def _em_request(self, params, target):
        self._check_component_id(params)
        return target()

    def set_udp_destination(self, config):
        if not isinstance(config, dict):
            raise RpcFailure(-103, "Invalid config")
        udp = config.get("rpc_udp", {})
        if not isinstance(udp, dict):
            raise RpcFailure(-103, "Invalid rpc_udp")
        dest = udp.get("dst_addr")
        if not dest:
            return {"restart_required": False}
        try:
            host, port_str = dest.rsplit(":", 1)
            host_ip = ipaddress.ip_address(host)
            port = int(port_str)
            if host_ip.version != 4 or not host_ip.is_private or not 1 <= port <= 65535:
                raise ValueError("Invalid local UDP target")
            self.udp_dst = (str(host_ip), port)
            return {"restart_required": True}
        except (ValueError, AttributeError):
            return {"restart_required": False}

    def rpc(self, method: str, params=None):
        params = params if isinstance(params, dict) else {}
        status = self.status()
        config = self.config()
        queries = {
            "Shelly.GetDeviceInfo": lambda: self.device_info(params.get("ident", False) in (True, "true", "1", 1)),
            "Shelly.GetStatus": lambda: status,
            "Shelly.GetConfig": lambda: config,
            "Shelly.GetComponents": lambda: self.components(params),
            "Shelly.ListMethods": lambda: {"methods": list(_SUPPORTED_METHODS)},
            "Shelly.ListProfiles": lambda: {"profiles": ["triphase"]},
            "Shelly.Reboot": lambda: {},
            "Shelly.CheckForUpdate": lambda: {"stable": {"version": self.identity.get("ver", "1.7.1")}},
            "EM.GetStatus": lambda: self._em_request(params, self.meter.em_status),
            "EM.GetConfig": lambda: self._em_request(params, lambda: config["em:0"]),
            "EM.GetCTTypes": lambda: self._em_request(params, lambda: {"supported": [config["em:0"]["ct_type"]]}),
            "EMData.GetStatus": lambda: self._em_request(params, self.meter.emdata_status),
            "EMData.GetConfig": lambda: self._em_request(params, lambda: config["emdata:0"]),
            "EMData.GetData": lambda: self._em_request(params, lambda: {"data": []}),
            "Sys.GetStatus": lambda: status["sys"],
            "Sys.GetConfig": lambda: config["sys"],
            "Sys.SetConfig": lambda: self.set_udp_destination(params.get("config", {})),
            "Cloud.GetStatus": lambda: status["cloud"],
            "Cloud.GetConfig": lambda: config["cloud"],
            "Cloud.SetConfig": lambda: {"restart_required": False},
            "WiFi.GetStatus": lambda: status["wifi"],
            "WiFi.GetConfig": lambda: config["wifi"],
            "Eth.GetStatus": lambda: status["eth"],
            "Eth.GetConfig": lambda: config["eth"],
            "MQTT.GetStatus": lambda: status["mqtt"],
            "MQTT.GetConfig": lambda: config["mqtt"],
            "Temperature.GetStatus": lambda: status["temperature:0"],
            "Schedule.List": lambda: {"jobs": []},
            "Webhook.List": lambda: {"hooks": []},
            "Script.List": lambda: {"scripts": []},
            "KVS.List": lambda: {"keys": {}, "rev": 0},
        }
        if method not in queries:
            raise RpcFailure(-32601, f"Method not found: {method}")
        return queries[method]()

    async def handle_shelly(self, request):
        return web.json_response(self.device_info())

    async def handle_get(self, request):
        try:
            return web.json_response(self.rpc(request.match_info["method"], _decode_query_params(request.query)))
        except RpcFailure as exc:
            return web.json_response({"code": exc.code, "message": exc.message})

    async def handle_post(self, request):
        try:
            payload = await request.json()
        except Exception:
            raise web.HTTPBadRequest()
        method = request.match_info.get("method")
        if method:
            if not isinstance(payload, dict):
                raise web.HTTPBadRequest()
            try:
                return web.json_response(self.rpc(method, payload))
            except RpcFailure as exc:
                return web.json_response({"code": exc.code, "message": exc.message})
        if isinstance(payload, list):
            return web.json_response([self.rpc_response(item) for item in payload if isinstance(item, dict)])
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest()
        return web.json_response(self.rpc_response(payload))

    def rpc_response(self, request):
        frame = {"id": request.get("id"), "src": self.device_id, "dst": request.get("src", "")}
        try:
            frame["result"] = self.rpc(request.get("method", ""), request.get("params", {}))
        except RpcFailure as exc:
            frame["error"] = {"code": exc.code, "message": exc.message}
        return frame

    async def handle_ws(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._ws_clients.add(ws)
        self._ws_sources[ws] = None
        try:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        obj = json.loads(message.data)
                        if isinstance(obj, dict) and obj.get("method"):
                            src = obj.get("src")
                            if isinstance(src, str) and src:
                                self._ws_sources[ws] = src
                            await ws.send_json(self.rpc_response(obj))
                    except (json.JSONDecodeError, ValueError):
                        pass
        finally:
            self._ws_clients.discard(ws)
            self._ws_sources.pop(ws, None)
        return ws

    async def broadcast_status(self):
        if not self._ws_clients:
            return
        msg = {"src": self.device_id, "dst": "*", "method": "NotifyStatus",
               "params": {"ts": time.time(), "em:0": self.meter.em_status()}}
        for ws in tuple(self._ws_clients):
            if ws.closed:
                self._ws_clients.discard(ws)
                continue
            destination = self._ws_sources.get(ws)
            if not destination:
                continue
            with suppress(Exception):
                await ws.send_json({**msg, "dst": destination})

    def udp_message(self, payload: bytes, sender, transport):
        try:
            obj = json.loads(payload)
            if not isinstance(obj, dict) or not obj.get("method"):
                return
            method = obj["method"]
            if method == "EM1.GetStatus":
                result = {"id": 0, "act_power": round(self.meter.total, 3)}
            elif method == "EM.GetStatus":
                result = self.meter.em_udp()
            else:
                try:
                    result = self.rpc(method, obj.get("params"))
                except RpcFailure as exc:
                    result = None
                    failure = {"code": exc.code, "message": exc.message}
            if obj.get("src") and sender[0] == (self.udp_dst[0] if self.udp_dst else sender[0]):
                self.peer = obj["src"]
            if "id" in obj:
                answer = {"id": obj["id"], "src": self.device_id,
                          "dst": obj.get("src", "*")}
                if method not in ("EM1.GetStatus", "EM.GetStatus") and result is None:
                    answer["error"] = failure
                else:
                    answer["result"] = result
                transport.sendto(json.dumps(answer, separators=(",", ":")).encode(), sender)
        except (UnicodeDecodeError, ValueError, TypeError):
            return

    def send_udp_push(self):
        if not self.udp_dst:
            return
        transport = next((transport for port, transport in self._udp if port == 8006), None)
        if not transport:
            return
        msg = {"src": self.device_id, "dst": self.peer, "method": "NotifyStatus",
               "params": {"ts": time.time(), "em:0": self.meter.em_udp()}}
        with suppress(OSError):
            transport.sendto(json.dumps(msg, separators=(",", ":")).encode(), self.udp_dst)

    async def push_loop(self):
        while True:
            await asyncio.sleep(5)
            self.send_udp_push()
            await self.broadcast_status()

    def _cloud_ssl(self):
        """TLS remains encrypted in every mode; authorization differs.

        Defaults to normal trusted-CA validation. SHA-256 pinning allows a
        consciously trusted leaf cert without relying on the CA chain.
        Compatibility mode must be separately approved in the options flow.
        """
        if self.tls_mode == TLS_VERIFY:
            return ssl.create_default_context()
        if self.tls_mode == TLS_PIN:
            if not re.fullmatch(r"[0-9a-f]{64}", self.tls_fingerprint):
                raise ValueError("TLS-Fingerprint fehlt oder hat kein gültiges SHA-256-Format")
            return aiohttp.Fingerprint(bytes.fromhex(self.tls_fingerprint))
        if self.tls_mode == TLS_COMPAT:
            # aiohttp ssl=False still uses an encrypted wss:// TLS channel,
            # but does not authenticate the server certificate. Never enable
            # this mode automatically as a fallback from verification failure.
            return False
        raise ValueError("Unbekannter Cloud-TLS-Modus")

    async def _probe_cloud_certificate(self):
        """Read remote leaf fingerprint, WITHOUT sending any Shelly JWT/RPC.

        The fingerprint is UNVERIFIED until independently checked/approved.
        No fallback is made to less secure mode automatically.
        """
        try:
            parts = urlsplit("wss://" + self.cloud_server.removeprefix("wss://"))
            if parts.scheme != "wss" or not parts.hostname:
                return
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(parts.hostname, parts.port or 443,
                                        ssl=ctx, server_hostname=parts.hostname), 8)
            try:
                obj = writer.get_extra_info("ssl_object")
                cert = obj.getpeercert(binary_form=True) if obj else None
                if cert:
                    self.cloud_server_certificate_sha256 = hashlib.sha256(cert).hexdigest().upper()
            finally:
                writer.close()
                await writer.wait_closed()
        except (OSError, asyncio.TimeoutError, ssl.SSLError, ValueError) as exc:
            # This check is diagnostic-only; never replace the actual error.
            _LOGGER.debug("Cloud TLS certificate probe unavailable: %s", type(exc).__name__)

    async def cloud_loop(self):
        """Connect to the Shelly cloud and retain safe server diagnostics.

        A connected websocket proves transport establishment, not acceptance
        of the virtual identity by Shelly's account/device backend.
        """
        if not self.cloud_server or not self.cloud_key:
            self.cloud_phase = "Fehlende Cloud-Zugangsdaten"
            return
        backoff = 5
        while self.cloud_enabled:
            self.cloud_attempt_count += 1
            self.cloud_last_attempt = self._now_utc()
            self.cloud_phase = "Verbindungsaufbau"
            self.cloud_last_error = None
            self.cloud_last_http_status = None
            self.cloud_certificate_verified = False
            self.cloud_tls_error_detail = None
            try:
                url = "wss://" + self.cloud_server.removeprefix("wss://")
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session:
                    async with session.ws_connect(url, heartbeat=30, ssl=self._cloud_ssl()) as ws:
                        self.cloud_connected = True
                        self.cloud_certificate_verified = self.tls_mode in (TLS_VERIFY, TLS_PIN)
                        self.cloud_phase = "WebSocket verbunden"
                        self.cloud_last_close_code = None
                        backoff = 5
                        await ws.send_json({"src": self.device_id, "dst": "cloud", "method": "NotifyFullStatus",
                                            "params": {**self.status(), "ts": time.time()}})

                        async def push_periodically():
                            while self.cloud_enabled:
                                await asyncio.sleep(10)
                                await ws.send_json({"src": self.device_id, "dst": "cloud", "method": "NotifyStatus",
                                                    "params": {"ts": time.time(), "em:0": self.meter.em_status()}})
                        push_task = asyncio.create_task(push_periodically())
                        try:
                            async for message in ws:
                                if message.type == aiohttp.WSMsgType.TEXT:
                                    try:
                                        incoming = json.loads(message.data)
                                        self._capture_server_reply(incoming)
                                        if isinstance(incoming, dict) and incoming.get("method"):
                                            if "id" in incoming:
                                                result = self.rpc_response(incoming)
                                                result["dst"] = incoming.get("src", "cloud")
                                                await ws.send_json(result)
                                            if incoming["method"] == "Shelly.Reboot":
                                                self.cloud_phase = "Server fordert Neustart"
                                                break
                                            if incoming["method"] == "Sys.SetConfig":
                                                self.send_udp_push()
                                    except (json.JSONDecodeError, ValueError, TypeError) as exc:
                                        self.cloud_last_error = "Ungültige Servernachricht: " + type(exc).__name__
                                elif message.type == aiohttp.WSMsgType.ERROR:
                                    self.cloud_last_error = self._safe_server_text(ws.exception() or "WebSocket-Fehler")
                                    break
                                elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                                                      aiohttp.WSMsgType.CLOSED):
                                    break
                        finally:
                            self.cloud_last_close_code = ws.close_code
                            push_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await push_task
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientConnectorCertificateError,
                    aiohttp.ClientConnectorSSLError,
                    aiohttp.ServerFingerprintMismatch) as exc:
                self.cloud_tls_error_detail = self._safe_server_text(f"{type(exc).__name__}: {exc}")
                self.cloud_last_error = self.cloud_tls_error_detail
                self.cloud_phase = "TLS-Zertifikat abgelehnt"
                _LOGGER.warning("Shelly-Cloud TLS-Prüfung fehlgeschlagen: %s", type(exc).__name__)
                await self._probe_cloud_certificate()
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as exc:
                self.cloud_last_error = self._safe_server_text(f"{type(exc).__name__}: {exc}")
                if isinstance(exc, aiohttp.WSServerHandshakeError):
                    self.cloud_last_http_status = exc.status
                self.cloud_phase = "Verbindungsfehler"
                _LOGGER.warning("Shelly-Cloud-Verbindung fehlgeschlagen: %s", type(exc).__name__)
            except Exception as exc:
                self.cloud_last_error = self._safe_server_text(f"{type(exc).__name__}: {exc}")
                self.cloud_phase = "Verbindungsfehler"
                _LOGGER.exception("Unerwarteter Cloud-Verbindungsfehler")
            finally:
                self.cloud_connected = False
                if self.cloud_enabled and self.cloud_phase not in ("Verbindungsfehler", "Serverfehler", "TLS-Zertifikat abgelehnt"):
                    self.cloud_phase = "Verbindung beendet"
            if not self.cloud_enabled:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)


class UdpHandler(asyncio.DatagramProtocol):
    def __init__(self, bridge, port):
        self.bridge = bridge
        self.port = port
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) <= 8192:
            self.bridge.udp_message(data, addr, self.transport)
