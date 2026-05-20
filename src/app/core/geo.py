"""Foydalanuvchi mamlakatini klient IP orqali (GeoLite2) aniqlash."""
from __future__ import annotations

import ipaddress
import logging
import os
import pathlib
import threading
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.websockets import WebSocket

log = logging.getLogger("spinbottle.geo")

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
_DEFAULT_DB = _PROJECT_ROOT / "GeoLite2-Country.mmdb"

# ISO → o'yin ichidagi mamlakat kodi (stollar / room_policy)
ISO_TO_COUNTRY: dict[str, str] = {
    "UZ": "UZBEKISTAN",
    "KZ": "KAZAKHSTAN",
    "RU": "RUSSIA",
    "US": "UNITED STATES",
    "TR": "TURKEY",
    "AZ": "AZERBAIJAN",
    "KG": "KYRGYZSTAN",
    "TJ": "TAJIKISTAN",
    "NL": "NETHERLANDS",
    "DE": "GERMANY",
    "GB": "UNITED KINGDOM",
    "UA": "UKRAINE",
    "BY": "BELARUS",
    "GE": "GEORGIA",
    "TM": "TURKMENISTAN",
    "MD": "MOLDOVA",
    "LV": "LATVIA",
    "LT": "LITHUANIA",
    "EE": "ESTONIA",
    "PL": "POLAND",
    "FR": "FRANCE",
    "IT": "ITALY",
    "ES": "SPAIN",
    "IN": "INDIA",
    "CN": "CHINA",
    "AE": "UNITED ARAB EMIRATES",
    "SA": "SAUDI ARABIA",
    "IL": "ISRAEL",
    "CA": "CANADA",
    "AU": "AUSTRALIA",
    "BR": "BRAZIL",
}

_reader = None
_reader_lock = threading.Lock()
_db_warned = False

# Eski default — IP aniqlanmaganida ham shu yozilgan bo'lgan
_STALE_COUNTRY_VALUES = frozenset(
    {
        "",
        "unknown",
        "uzbekistan",
        "uz",
        "uzbekiston",
        "uzbekistan",
    }
)


def geoip_db_path() -> pathlib.Path:
    raw = (os.environ.get("GEOIP_DB_PATH") or "").strip()
    if raw:
        return pathlib.Path(raw)
    return _DEFAULT_DB


def geoip_status() -> dict:
    path = geoip_db_path()
    size = path.stat().st_size if path.is_file() else 0
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size": size,
        "valid": path.is_file() and size > 1024,
    }


def _get_reader():
    global _reader, _db_warned
    with _reader_lock:
        if _reader is not None:
            return _reader
        path = geoip_db_path()
        if not path.is_file() or path.stat().st_size < 1024:
            if not _db_warned:
                log.warning(
                    "GeoLite2-Country.mmdb yo'q yoki bo'sh (%s) — ip-api.com zaxirasi ishlatiladi",
                    path,
                )
                _db_warned = True
            return None
        try:
            import geoip2.database

            _reader = geoip2.database.Reader(str(path))
            log.info("GeoIP DB yuklandi: %s", path)
            return _reader
        except Exception as e:
            if not _db_warned:
                log.error("GeoIP DB ochilmadi: %s", e)
                _db_warned = True
            return None


def is_public_ip(ip: str) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_reserved
        or addr.is_link_local
        or addr.is_multicast
    )


def _first_public_ip(raw: str) -> str:
    for part in (raw or "").split(","):
        ip = part.strip()
        if ip and is_public_ip(ip):
            return ip[:64]
    return ""


def client_ip_from_headers(headers: dict[str, str], *, fallback_host: str = "") -> str:
    """
    Proxy / Cloudflare orqasidagi haqiqiy klient IP.
    Tartib: CF-Connecting-IP → True-Client-IP → X-Real-IP → X-Forwarded-For → host.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    for key in (
        "cf-connecting-ip",
        "true-client-ip",
        "x-real-ip",
        "x-forwarded-for",
        "forwarded",
    ):
        raw = lower.get(key) or ""
        if not raw:
            continue
        if key == "forwarded" and "for=" in raw.lower():
            for segment in raw.split(";"):
                seg = segment.strip().lower()
                if seg.startswith("for="):
                    cand = seg[4:].strip().strip('"[]')
                    if is_public_ip(cand):
                        return cand[:64]
            continue
        ip = _first_public_ip(raw)
        if ip:
            return ip
    if fallback_host and is_public_ip(fallback_host):
        return fallback_host[:64]
    return ""


def client_ip(request: Request) -> str:
    host = request.client.host if request.client else ""
    ip = client_ip_from_headers(dict(request.headers), fallback_host=host)
    return ip or (host[:64] if host else "unknown")


def ws_client_ip(ws: WebSocket) -> str:
    headers = {
        k.decode("latin-1"): v.decode("latin-1")
        for k, v in (ws.scope.get("headers") or [])
    }
    client = ws.scope.get("client")
    host = str(client[0]) if client and client[0] else ""
    ip = client_ip_from_headers(headers, fallback_host=host)
    return ip or (host[:64] if host else "unknown")


def _normalize_country_name(name: str | None, iso: str | None) -> str | None:
    if iso:
        iso_u = iso.strip().upper()
        if iso_u in ISO_TO_COUNTRY:
            return ISO_TO_COUNTRY[iso_u]
    if name:
        n = name.strip().upper()
        return n if n else None
    return None


def _country_from_ip_api(ip: str) -> str | None:
    """MaxMind yo'q/buzuk bo'lsa — ip-api.com (sync, registratsiya uchun)."""
    import json
    import urllib.error
    import urllib.request

    url = f"http://ip-api.com/json/{ip}?fields=status,country,countryCode"
    try:
        with urllib.request.urlopen(url, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") == "success":
            return _normalize_country_name(
                data.get("country"),
                data.get("countryCode"),
            )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log.debug("ip-api.com lookup failed ip=%s: %s", ip, e)
    return None


def country_code_from_ip(ip: str) -> str | None:
    """IP → UZBEKISTAN / RUSSIA / … yoki None (aniqlab bo'lmadi)."""
    if not ip or ip == "unknown":
        return None
    if not is_public_ip(ip):
        return None

    reader = _get_reader()
    if reader:
        try:
            response = reader.country(ip)
            iso = response.country.iso_code
            name = response.country.name
            result = _normalize_country_name(name, iso)
            if result:
                return result
        except Exception as e:
            log.debug("GeoIP lookup failed ip=%s: %s", ip, e)

    return _country_from_ip_api(ip)


def country_from_request(request: Request) -> str | None:
    return country_code_from_ip(client_ip(request))


def country_needs_geo_refresh(country: str | None) -> bool:
    """Eski default yoki bo'sh — IP bo'yicha qayta yozish mumkin."""
    return (country or "").strip().lower() in _STALE_COUNTRY_VALUES


def get_country_by_ip(ip: str) -> str:
    """Eski API: aniqlanmasa Unknown (Uzbekistan default emas)."""
    return country_code_from_ip(ip) or "Unknown"
