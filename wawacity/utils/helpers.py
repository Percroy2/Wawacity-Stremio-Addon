import json
from typing import Optional, Dict, Any, List
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
from base64 import b64encode, b64decode

# --- Base64 encoding ---
def encode_config_to_base64(config: Dict[str, Any]) -> str:
    return b64encode(json.dumps(config).encode()).decode()

# --- Wawacity URL from user config or environment ---
def get_wawacity_url(config: Dict[str, Any]) -> str:
    from wawacity.core.config import WAWACITY_URL

    url = (config.get("wawacity_url") or WAWACITY_URL).strip().rstrip("/")
    return url


def is_bookys_enabled(config: Dict[str, Any]) -> bool:
    if not config.get("enable_bookys"):
        return False

    return bool(get_bookys_url(config))


def pick_audiobook_stream_link(results: List[Dict[str, Any]]) -> Optional[str]:
    for result in results:
        hoster = (result.get("hoster") or "").lower()
        if "1fichier" in hoster and result.get("dl_protect"):
            return result["dl_protect"]
    for result in results:
        if result.get("dl_protect"):
            return result["dl_protect"]
    return None


def get_bookys_url(config: Dict[str, Any]) -> Optional[str]:
    from wawacity.core.config import BOOKYS_URL

    if not config.get("enable_bookys"):
        return None

    url = (config.get("bookys_url") or BOOKYS_URL or "").strip().rstrip("/")
    return url or None

# --- Cache key creation ---
def create_cache_key(
    cache_type: str,
    title: str,
    year: Optional[str] = None,
    wawacity_url: Optional[str] = None,
) -> str:
    cache_key = f"{cache_type}:{quote_plus(title.lower())}"
    if year:
        cache_key += f":{year}"
    if wawacity_url:
        host = urlparse(wawacity_url).netloc
        if host:
            cache_key += f":{host}"
    return cache_key

# --- Filename extraction from dl-protect links ---
def extract_filename_from_link(url: str, link_text: str) -> str:
    # --- First try from link text ---
    original_filename = link_text.split(":")[-1].strip() if ":" in link_text else link_text.strip()
    
    # --- Then try decoding from URL ---
    try:
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        fn_encoded = query_params.get('fn', [None])[0]
        
        if fn_encoded:
            fn_unquoted = unquote(fn_encoded)
            decoded_fn = b64decode(fn_unquoted).decode('utf-8')
            return decoded_fn if decoded_fn else original_filename
    except Exception:
        pass
    
    return original_filename

# --- URL formatting ---
def format_url(url: str, base_url: str) -> str:
    if not url:
        return ""
    
    if url.startswith("http://") or url.startswith("https://"):
        return url
    
    if url.startswith("/"):
        return f"{base_url}{url}"
    
    return url

# --- URL parameter encoding ---
def quote_url_param(param: str) -> str:
    return quote_plus(param)