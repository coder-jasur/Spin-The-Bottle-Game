import geoip2.database
import pathlib

# Baza fayli yo'li
BASE_DIR = pathlib.Path(__file__).resolve().parents[3]
DB_PATH = BASE_DIR / "GeoLite2-Country.mmdb"

def get_country_by_ip(ip: str) -> str:
    """IP orqali davlat nomini aniqlash"""
    if not ip or ip == "127.0.0.1" or ip == "::1":
        return "Unknown" # Localhost uchun

    try:
        with geoip2.database.Reader(str(DB_PATH)) as reader:
            response = reader.country(ip)
            # Faqat davlat nomini qaytaramiz (masalan: Uzbekistan)
            return response.country.name or "Unknown"
    except Exception:
        return "Unknown"
