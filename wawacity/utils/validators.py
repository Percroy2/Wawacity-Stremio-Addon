import binascii
import json
from typing import Optional, Dict
from base64 import b64decode, urlsafe_b64decode
from urllib.parse import urlparse, unquote

from wawacity.core.categories import normalize_enabled_categories

# --- Wawacity URL normalization ---
def normalize_wawacity_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None

    url = url.strip().rstrip("/")
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    return url


def decode_wa_title(content_id: str) -> Optional[str]:
    if not content_id.startswith("wa:"):
        return None

    encoded = content_id[3:]
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        return urlsafe_b64decode(padded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return unquote(encoded)


def resolve_content_category(content_id: str, content_type: str) -> str:
    content_id_formatted = content_id.replace(".json", "")

    if content_id_formatted.startswith(("wa:", "ol:")) or (
        content_id_formatted.startswith("OL") and content_id_formatted.endswith("W")
    ):
        return "audiobook"

    if content_type == "movie":
        return "movie"

    return "series"


# --- Configuration decoding (lenient, for UI prefill) ---
def decode_config(config_base64: Optional[str]) -> Optional[Dict]:
    if not config_base64:
        return None

    try:
        decoded_bytes = b64decode(config_base64, validate=True)
        decoded_str = decoded_bytes.decode("utf-8")
        config_dict = json.loads(decoded_str)

        if not isinstance(config_dict, dict):
            return None

        return config_dict
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return None


# --- Configuration validation ---
def validate_config(config_base64: Optional[str]) -> Optional[Dict[str, str]]:
    config_dict = decode_config(config_base64)
    if not config_dict:
        return None

    if "alldebrid" not in config_dict or "tmdb" not in config_dict:
        return None

    if not config_dict["alldebrid"] or not config_dict["tmdb"]:
        return None

    if "wawacity_url" in config_dict and config_dict["wawacity_url"]:
        normalized = normalize_wawacity_url(config_dict["wawacity_url"])
        if not normalized:
            return None
        config_dict["wawacity_url"] = normalized

    if "excluded_words" in config_dict:
        excluded_words = config_dict["excluded_words"]
        if not isinstance(excluded_words, list):
            return None

        for word in excluded_words:
            if not isinstance(word, str):
                return None
    else:
        config_dict["excluded_words"] = []

    config_dict["enabled_categories"] = normalize_enabled_categories(
        config_dict.get("enabled_categories")
    )

    return config_dict


# --- Media info extraction ---
def extract_media_info(content_id: str, content_type: str) -> Dict[str, Optional[str]]:
    content_id_formatted = content_id.replace(".json", "")
    category = resolve_content_category(content_id_formatted, content_type)

    if category == "audiobook":
        if content_id_formatted.startswith("wa:"):
            return {
                "category": "audiobook",
                "content_id": content_id_formatted,
                "search_title": decode_wa_title(content_id_formatted),
                "imdb_id": None,
                "season": None,
                "episode": None,
            }

        work_id = content_id_formatted
        if work_id.startswith("ol:"):
            work_id = work_id[3:]

        return {
            "category": "audiobook",
            "content_id": content_id_formatted,
            "search_title": None,
            "openlibrary_id": work_id,
            "imdb_id": None,
            "season": None,
            "episode": None,
        }

    if content_type == "series" and ":" in content_id_formatted:
        parts = content_id_formatted.split(":")
        return {
            "category": "series",
            "content_id": content_id_formatted,
            "imdb_id": parts[0],
            "season": parts[1] if len(parts) > 1 else "1",
            "episode": parts[2] if len(parts) > 2 else "1",
            "search_title": None,
        }

    return {
        "category": "movie" if content_type == "movie" else "series",
        "content_id": content_id_formatted,
        "imdb_id": content_id_formatted,
        "season": None,
        "episode": None,
        "search_title": None,
    }
