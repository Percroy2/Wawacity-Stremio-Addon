from typing import Dict, Optional, Tuple
from urllib.parse import urlencode

import httpx

from wawacity.core.config import MEDIAFLOW_URL, MEDIAFLOW_INTERNAL_URL, MEDIAFLOW_PASSWORD
from wawacity.utils.http_client import http_client
from wawacity.utils.logger import logger


def get_mediaflow_settings(config: Optional[Dict] = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    config = config or {}

    config_url = (config.get("mediaflow_url") or "").strip().rstrip("/")
    config_password = (config.get("mediaflow_password") or "").strip()

    # Priorité à la config utilisateur (/configure) — instance MediaFlow externe
    if config_url and config_password:
        return config_url, config_url, config_password

    public_url = (MEDIAFLOW_URL or "").strip().rstrip("/")
    internal_url = (MEDIAFLOW_INTERNAL_URL or public_url).strip().rstrip("/")
    password = (MEDIAFLOW_PASSWORD or "").strip()

    if not public_url or not password:
        return None, None, None

    return public_url, internal_url, password


def is_mediaflow_enabled(config: Optional[Dict] = None) -> bool:
    public_url, _, password = get_mediaflow_settings(config)
    return bool(public_url and password)


def build_stream_proxy_url(
    public_url: str,
    password: str,
    destination_url: str,
    filename: Optional[str] = None,
) -> str:
    params = {
        "d": destination_url,
        "api_password": password,
    }
    if filename:
        params["filename"] = filename

    return f"{public_url.rstrip('/')}/proxy/stream?{urlencode(params)}"


class MediaFlowService:
    async def forward_get(
        self,
        internal_url: str,
        password: str,
        destination_url: str,
    ) -> httpx.Response:
        forward_endpoint = f"{internal_url.rstrip('/')}/proxy/forward"
        return await http_client.get(
            forward_endpoint,
            params={
                "d": destination_url,
                "api_password": password,
            },
        )

    async def get_public_ip(self, internal_url: str, password: str) -> Optional[str]:
        try:
            response = await http_client.get(
                f"{internal_url.rstrip('/')}/proxy/ip",
                params={"api_password": password},
                timeout=10.0,
            )
            if response.status_code != 200:
                return None
            data = response.json()
            ip = data.get("ip")
            return ip if isinstance(ip, str) and ip else None
        except Exception as e:
            logger.error(f"MediaFlow IP lookup failed: {e}")
            return None

    def wrap_playback_url(
        self,
        direct_url: str,
        config: Optional[Dict] = None,
        filename: Optional[str] = None,
    ) -> Optional[str]:
        public_url, _, password = get_mediaflow_settings(config)
        if not public_url or not password:
            return None

        return build_stream_proxy_url(public_url, password, direct_url, filename)


mediaflow_service = MediaFlowService()
