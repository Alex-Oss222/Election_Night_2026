"""Kalshi API-key request signing (Ed25519 or RSA-PSS), used only for the market-data websocket.

Signature = base64(sign(timestamp_ms + METHOD + path_without_query)).
Headers: KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP, KALSHI-ACCESS-SIGNATURE.
"""
from __future__ import annotations

import base64
import os
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa


def _key_bytes_from_env() -> bytes | None:
    """Return PEM or DER key bytes, None if no key is configured, or raise ValueError if one is set but unreadable.

    KALSHI_PRIVATE_KEY may be the PEM text, the PEM with literal \\n escapes, base64 of the whole PEM file, or
    base64 DER (the PEM body without its BEGIN/END lines, which is what a one-line copy of a key often is).
    KALSHI_PRIVATE_KEY_PATH may point at a PEM or DER file instead.
    """
    path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if path:
        if not os.path.exists(path):
            raise ValueError("KALSHI_PRIVATE_KEY_PATH is set but the file does not exist")
        return open(path, "rb").read()
    raw = os.environ.get("KALSHI_PRIVATE_KEY")
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    if "BEGIN" in raw:
        return raw.replace("\\n", "\n").encode()
    try:
        data = base64.b64decode("".join(raw.replace("\\n", "").split()), validate=True)
    except ValueError:
        raise ValueError("KALSHI_PRIVATE_KEY is neither PEM text nor valid base64") from None
    return data.replace(b"\\n", b"\n") if b"BEGIN" in data else data


def _load_private_key(data: bytes):
    is_pem = b"-----BEGIN" in data
    try:
        if is_pem:
            return serialization.load_pem_private_key(data, password=None)
        return serialization.load_der_private_key(data, password=None)
    except (ValueError, TypeError) as e:
        # cryptography's messages don't echo key material, but keep ours generic regardless
        kind = "PEM" if is_pem else f"DER ({len(data)} bytes after base64 decode)"
        raise ValueError(f"could not parse private key as {kind}: {type(e).__name__}") from None


class KalshiSigner:
    def __init__(self, key_id: str, key_bytes: bytes) -> None:
        self.key_id = key_id
        self.key = _load_private_key(key_bytes)
        if not isinstance(self.key, (ed25519.Ed25519PrivateKey, rsa.RSAPrivateKey)):
            raise ValueError("Kalshi key must be Ed25519 or RSA")

    @classmethod
    def from_env(cls) -> "KalshiSigner | None":
        key_id, key_bytes = os.environ.get("KALSHI_API_KEY_ID"), _key_bytes_from_env()
        return cls(key_id, key_bytes) if key_id and key_bytes else None

    def sign(self, message: bytes) -> bytes:
        if isinstance(self.key, ed25519.Ed25519PrivateKey):
            return self.key.sign(message)
        return self.key.sign(message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                                  salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())

    def headers(self, method: str, path: str, ts_ms: int | None = None) -> dict[str, str]:
        ts = str(ts_ms if ts_ms is not None else int(time.time() * 1000))
        msg = (ts + method.upper() + path.split("?", 1)[0]).encode()
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(self.sign(msg)).decode(),
        }
