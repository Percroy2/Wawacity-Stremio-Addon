from base64 import urlsafe_b64encode, urlsafe_b64decode
from typing import Optional
from urllib.parse import urlparse

BOOKYS_POSTER_HOST = "bookys-ebooks.com"
OPENLIBRARY_COVER_HOST = "covers.openlibrary.org"


def encode_poster_source(url: str) -> str:
    return urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def decode_poster_source(token: str) -> Optional[str]:
    if not token:
        return None

    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = urlsafe_b64decode(padded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None

    parsed = urlparse(decoded)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    return decoded


def is_bookys_poster_url(url: str) -> bool:
    if not url:
        return False
    return BOOKYS_POSTER_HOST in urlparse(url).netloc.lower()


def should_proxy_poster(url: str) -> bool:
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    if OPENLIBRARY_COVER_HOST in host:
        return False
    if BOOKYS_POSTER_HOST in host:
        return False
    return True


def poster_proxy_url(addon_base_url: str, original_url: str) -> str:
    if not original_url or not addon_base_url:
        return original_url or ""

    if "/poster/" in original_url:
        return original_url

    if not should_proxy_poster(original_url):
        return original_url

    token = encode_poster_source(original_url)
    return f"{addon_base_url.rstrip('/')}/poster/{token}"
