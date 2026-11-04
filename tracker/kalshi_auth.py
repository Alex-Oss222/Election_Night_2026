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


def _pem_from_env() -> bytes | None:
    """Accept the PEM text itself, the PEM with literal \\n escapes, base64 of the PEM, or a file path."""
    path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if path and os.path.exists(path):
        return open(path, "rb").read()
    raw = os.environ.get("KALSHI_PRIVATE_KEY")
    if not raw:
        return None
    raw = raw.strip()
    if "BEGIN" not in raw:
        try:
            raw = base64.b64decode(raw).decode()
        except ValueError:
            return None
    return raw.replace("\\n", "\n").encode()


class KalshiSigner:
    def __init__(self, key_id: str, pem: bytes) -> None:
        self.key_id = key_id
        self.key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(self.key, (ed25519.Ed25519PrivateKey, rsa.RSAPrivateKey)):
            raise ValueError("Kalshi key must be Ed25519 or RSA")

    @classmethod
    def from_env(cls) -> "KalshiSigner | None":
        key_id, pem = os.environ.get("KALSHI_API_KEY_ID"), _pem_from_env()
        return cls(key_id, pem) if key_id and pem else None

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
