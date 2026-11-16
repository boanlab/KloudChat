"""Web Push message encryption (RFC 8291, aes128gcm) and VAPID (RFC 8292).

Built on `cryptography` only. pywebpush / py-vapid are MPL-2.0 and stay out.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: record size; a push service must accept at least 4096 bytes per message
RECORD_SIZE = 4096
#: header: salt(16) rs(4) idlen(1) keyid(65)
_HEADER_LEN = 16 + 4 + 1 + 65
#: largest plaintext that fits one record (tag 16, delimiter 1)
MAX_PLAINTEXT = RECORD_SIZE - _HEADER_LEN - 16 - 1


def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def private_key_from_raw(raw: bytes) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def private_key_raw(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_numbers().private_value.to_bytes(32, "big")


def public_key_from_raw(raw: bytes) -> ec.EllipticCurvePublicKey:
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)


def generate_keypair() -> tuple[str, str]:
    """A fresh P-256 pair as (private 32 bytes, public 65 bytes), base64url."""
    key = ec.generate_private_key(ec.SECP256R1())
    return b64u_encode(private_key_raw(key)), b64u_encode(_public_bytes(key.public_key()))


def public_from_private(private_b64: str) -> str:
    key = private_key_from_raw(b64u_decode(private_b64))
    return b64u_encode(_public_bytes(key.public_key()))


def _derive(
    auth_secret: bytes, ecdh_secret: bytes, ua_public: bytes, as_public: bytes, salt: bytes
) -> tuple[bytes, bytes]:
    # RFC 8291 §3.3-3.4; every HKDF-Expand here needs one block, so T(1) = HMAC(PRK, info||1)
    prk_key = _hmac(auth_secret, ecdh_secret)
    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = _hmac(prk_key, key_info + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    return cek, nonce


def encrypt(
    plaintext: bytes,
    ua_public_b64: str,
    auth_secret_b64: str,
    *,
    _as_private: ec.EllipticCurvePrivateKey | None = None,
    _salt: bytes | None = None,
) -> bytes:
    """One aes128gcm record for a subscription's `p256dh` and `auth` keys.

    `_as_private` and `_salt` exist for the RFC test vector only; real sends use fresh ones.
    """
    if len(plaintext) > MAX_PLAINTEXT:
        raise ValueError("push payload too large")
    ua_public = b64u_decode(ua_public_b64)
    auth_secret = b64u_decode(auth_secret_b64)
    if len(ua_public) != 65 or len(auth_secret) < 16:
        raise ValueError("bad subscription keys")
    ua_key = public_key_from_raw(ua_public)
    as_key = _as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(as_key.public_key())
    salt = _salt or os.urandom(16)
    ecdh_secret = as_key.exchange(ec.ECDH(), ua_key)
    cek, nonce = _derive(auth_secret, ecdh_secret, ua_public, as_public, salt)
    # last (only) record: delimiter 0x02, no padding
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + struct.pack("!L", RECORD_SIZE) + bytes([len(as_public)]) + as_public
    return header + ciphertext


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth_secret_b64: str) -> bytes:
    """The receiving side, for tests and the local push-service mock."""
    salt = body[:16]
    idlen = body[20]
    as_public = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]
    ua_public = _public_bytes(ua_private.public_key())
    ecdh_secret = ua_private.exchange(ec.ECDH(), public_key_from_raw(as_public))
    cek, nonce = _derive(b64u_decode(auth_secret_b64), ecdh_secret, ua_public, as_public, salt)
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    end = padded.rstrip(b"\x00")
    if not end.endswith(b"\x02"):
        raise ValueError("not a last record")
    return end[:-1]


def audience(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


def vapid_jwt(
    endpoint: str, private_b64: str, subject: str, *, ttl: int = 12 * 3600, now: int | None = None
) -> str:
    """ES256 JWT for RFC 8292; `exp` must stay within 24 hours."""
    key = private_key_from_raw(b64u_decode(private_b64))
    header = {"typ": "JWT", "alg": "ES256"}
    claims = {"aud": audience(endpoint), "exp": int(now or time.time()) + ttl, "sub": subject}
    signing_input = (
        b64u_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64u_encode(json.dumps(claims, separators=(",", ":")).encode())
    )
    der = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return signing_input + "." + b64u_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def vapid_header(endpoint: str, private_b64: str, public_b64: str, subject: str) -> str:
    return f"vapid t={vapid_jwt(endpoint, private_b64, subject)}, k={public_b64}"


__all__ = [
    "MAX_PLAINTEXT",
    "audience",
    "b64u_decode",
    "b64u_encode",
    "decrypt",
    "encrypt",
    "generate_keypair",
    "private_key_from_raw",
    "public_from_private",
    "public_key_from_raw",
    "vapid_header",
    "vapid_jwt",
]
