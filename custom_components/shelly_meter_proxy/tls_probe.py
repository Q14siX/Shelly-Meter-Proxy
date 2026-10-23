"""Shelly-Cloud TLS preflight without transmitting credentials.

Normal CA validation is automatic. An untrusted certificate can be pinned
only after explicit user confirmation; no silent downgrade to insecure TLS.
"""
from __future__ import annotations

import asyncio
import hashlib
import ssl
from urllib.parse import urlsplit


async def inspect_cloud_certificate(endpoint: str) -> dict[str, object]:
    """Probe a Shelly WSS server with no RPC / JWT payload.

    Return verified, fingerprint (SHA256 leaf DER), and server. Failure to
    connect returns an error, never automatically trusts the certificate.
    """
    raw = endpoint.removeprefix("wss://")
    parsed = urlsplit("wss://" + raw)
    if (not parsed.hostname or parsed.path != "/jrpc" or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Invalid Shelly Cloud endpoint")
    host = parsed.hostname
    port = parsed.port or 443

    async def connect(ctx):
        _, writer = await asyncio.wait_for(asyncio.open_connection(
            host, port, ssl=ctx, server_hostname=host), timeout=8)
        try:
            ssl_obj = writer.get_extra_info("ssl_object")
            cert = ssl_obj.getpeercert(binary_form=True) if ssl_obj else None
            return hashlib.sha256(cert).hexdigest().upper() if cert else None
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        fingerprint = await connect(ssl.create_default_context())
        return {"verified": True, "fingerprint": fingerprint, "host": host}
    except (ssl.SSLCertVerificationError, ssl.CertificateError):
        # Verification failure: obtain fingerprint only, without sending a key.
        pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    fingerprint = await connect(context)
    if not fingerprint:
        raise RuntimeError("No TLS certificate presented")
    return {"verified": False, "fingerprint": fingerprint, "host": host}
