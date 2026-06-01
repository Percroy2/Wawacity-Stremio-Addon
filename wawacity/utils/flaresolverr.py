import asyncio
from typing import Optional

import httpx

from wawacity.core.config import FLARESOLVERR_URL
from wawacity.utils.logger import logger

FLARESOLVERR_CLIENT_TIMEOUT = 120.0
FLARESOLVERR_MAX_TIMEOUT_MS = 60000


async def fetch_html(url: str) -> Optional[str]:
    if not FLARESOLVERR_URL:
        return None

    api_url = f"{FLARESOLVERR_URL.rstrip('/')}/v1"
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": FLARESOLVERR_MAX_TIMEOUT_MS,
    }

    try:
        async with httpx.AsyncClient(timeout=FLARESOLVERR_CLIENT_TIMEOUT) as client:
            response = await asyncio.wait_for(
                client.post(api_url, json=payload),
                timeout=FLARESOLVERR_CLIENT_TIMEOUT,
            )
    except asyncio.TimeoutError:
        logger.error(f"FlareSolverr timed out: {url}")
        return None
    except Exception as e:
        logger.error(f"FlareSolverr request failed: {e}")
        return None

    if response.status_code != 200:
        logger.error(f"FlareSolverr API HTTP {response.status_code}")
        return None

    try:
        data = response.json()
    except ValueError:
        logger.error("FlareSolverr returned invalid JSON")
        return None

    if data.get("status") != "ok":
        logger.error(f"FlareSolverr error: {data.get('message', 'unknown')}")
        return None

    solution = data.get("solution") or {}
    status = solution.get("status")
    if status != 200:
        logger.error(f"FlareSolverr page status {status}: {url}")
        return None

    html = solution.get("response") or ""
    if not html:
        logger.error(f"FlareSolverr empty response: {url}")
        return None

    if "Just a moment" in html or "cf-challenge" in html.lower():
        logger.error(f"FlareSolverr still blocked by Cloudflare: {url}")
        return None

    logger.log("SCRAPER", f"FlareSolverr OK: {url}")
    return html
