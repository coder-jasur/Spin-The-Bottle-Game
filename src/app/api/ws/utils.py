"""
Protokol utilitalari: AES-256-CBC shifrash/deshifrash + paket formati.
"""
import base64
import json
import os
from typing import Optional

from Crypto.Cipher import AES
from Crypto.Hash import MD5

DECRYPT_KEY = "050000000000"


# ─────────────────────────────────────────────────────────────────────────
# AES-256-CBC (CryptoJS muvofiqligi)
# ─────────────────────────────────────────────────────────────────────────

def _derive_key_iv(password: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """OpenSSL EVP_BytesToKey algoritmi (MD5, 1 iteratsiya)."""
    d, d_i = b"", b""
    while len(d) < 48:
        d_i = MD5.new(d_i + password + salt).digest()
        d += d_i
    return d[:32], d[32:48]


def encrypt_aes(data: dict, key: str = DECRYPT_KEY) -> str:
    """Dict → AES-256-CBC → Base64 string."""
    salt = os.urandom(8)
    k, iv = _derive_key_iv(key.encode(), salt)
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    pad = 16 - len(raw) % 16
    raw += bytes([pad] * pad)
    ct = AES.new(k, AES.MODE_CBC, iv).encrypt(raw)
    return base64.b64encode(b"Salted__" + salt + ct).decode()


def decrypt_aes(b64: str, key: str = DECRYPT_KEY) -> Optional[dict]:
    """Base64 AES → dict. Xato bo'lsa None."""
    try:
        enc = base64.b64decode(b64)
        k, iv = _derive_key_iv(key.encode(), enc[8:16])
        raw = AES.new(k, AES.MODE_CBC, iv).decrypt(enc[16:])
        pad = raw[-1]
        if pad > 16:
            raw_text = raw.decode("utf-8", errors="ignore")
        else:
            raw_text = raw[:-pad].decode("utf-8")
        return json.loads(raw_text)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# Paket: 2-bayt uzunlik + JSON {"data": "<base64>"}
# ─────────────────────────────────────────────────────────────────────────

def prepare_packet(data: dict) -> bytes:
    """Dict → [2B len][JSON {"data": "..."}]"""
    b64 = encrypt_aes(data)
    payload = json.dumps({"data": b64}, separators=(",", ":"), ensure_ascii=False).encode()
    ln = len(payload)
    return bytes([ln >> 8 & 0xFF, ln & 0xFF]) + payload


def parse_packet(raw: bytes) -> dict:
    """[2B len][JSON] → dict. Xato bo'lsa {} qaytaradi."""
    if not raw:
        return {}
    # Header bo'lmagan holat (birinchi bayt '{')
    payload = raw if raw[0] == 123 else raw[2:]
    try:
        obj = json.loads(payload.decode("utf-8", errors="ignore"))
        if isinstance(obj, dict) and "data" in obj:
            return decrypt_aes(obj["data"]) or {}
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}