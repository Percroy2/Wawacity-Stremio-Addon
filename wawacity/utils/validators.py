import binascii
import json
from typing import Optional, Dict
from base64 import b64decode
from urllib.parse import urlparse

# --- Wawacity URL normalization ---
def normalize_wawacity_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None

    url = url.strip().rstrip("/")
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    return url

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

    # --- Check required keys ---
    if "alldebrid" not in config_dict or "tmdb" not in config_dict:
        return None

    # --- Check keys not empty ---
    if not config_dict["alldebrid"] or not config_dict["tmdb"]:
        return None

    # --- Validate optional wawacity_url ---
    if "wawacity_url" in config_dict and config_dict["wawacity_url"]:
        normalized = normalize_wawacity_url(config_dict["wawacity_url"])
        if not normalized:
            return None
        config_dict["wawacity_url"] = normalized

    # --- Validate excluded_words ---
    if "excluded_words" in config_dict:
        excluded_words = config_dict["excluded_words"]
        if not isinstance(excluded_words, list):
            return None

        for word in excluded_words:
            if not isinstance(word, str):
                return None
    else:
        config_dict["excluded_words"] = []

    return config_dict

# --- Media info extraction ---
def extract_media_info(content_id: str, content_type: str) -> Dict[str, Optional[str]]:
    content_id_formatted = content_id.replace(".json", "")

    if content_type == "series" and ":" in content_id_formatted:
        parts = content_id_formatted.split(":")
        return {
            "imdb_id": parts[0],
            "season": parts[1] if len(parts) > 1 else "1",
            "episode": parts[2] if len(parts) > 2 else "1",
        }

    return {
        "imdb_id": content_id_formatted,
        "season": None,
        "episode": None,
    }
