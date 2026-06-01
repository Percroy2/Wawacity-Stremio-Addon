import os
from typing import Optional, Dict, List, Any, Tuple
from urllib.parse import urlencode
from asyncio import sleep

from wawacity.services.mediaflow import get_mediaflow_settings, mediaflow_service
from wawacity.utils.http_client import http_client
from wawacity.core.config import ALLDEBRID_API_URL, ALLDEBRID_MAX_RETRIES, RETRY_DELAY_SECONDS
from wawacity.utils.logger import logger

AUDIO_EXTENSIONS = (".mp3", ".m4b", ".m4a", ".opus", ".flac", ".aac", ".wav", ".ogg")


def _is_audio_filename(filename: str) -> bool:
    lower = (filename or "").lower()
    return any(lower.endswith(ext) for ext in AUDIO_EXTENSIONS)


def _format_chapter_title(filename: str) -> str:
    name = os.path.splitext(filename or "")[0]
    name = name.replace("_", " ").replace(".", " ")
    return name.strip() or filename or "Chapitre"


class AllDebridService:

    def _base_params(self, apikey: str) -> Dict[str, str]:
        return {"agent": "Wawacity", "apikey": apikey}

    async def _api_get(
        self,
        path: str,
        params: Dict[str, str],
        config: Optional[Dict] = None,
        list_params: Optional[Dict[str, List[str]]] = None,
    ):
        pairs: List[Tuple[str, str]] = list(params.items())
        if list_params:
            for key, values in list_params.items():
                for value in values:
                    pairs.append((f"{key}[]", value))

        query = urlencode(pairs)
        destination = f"{ALLDEBRID_API_URL}{path}?{query}"

        _, internal_url, password = get_mediaflow_settings(config)
        if internal_url and password:
            logger.log("ALLDEBRID", "Routing API call via MediaFlow forward proxy")
            return await mediaflow_service.forward_get(internal_url, password, destination)

        return await http_client.get(f"{ALLDEBRID_API_URL}{path}", params=dict(pairs))

    async def _extract_redirector_links(
        self,
        dl_protect_link: str,
        apikey: str,
        config: Optional[Dict] = None,
    ) -> Optional[List[str]]:
        response = await self._api_get(
            "/link/redirector",
            {**self._base_params(apikey), "link": dl_protect_link},
            config,
        )

        if response.status_code != 200:
            return None

        data = response.json()
        if data.get("status") != "success":
            return None

        links = data.get("data", {}).get("links", [])
        return links if isinstance(links, list) and links else None

    async def _fetch_link_infos(
        self,
        links: List[str],
        apikey: str,
        config: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        if not links:
            return []

        response = await self._api_get(
            "/link/infos",
            self._base_params(apikey),
            config,
            list_params={"link": links},
        )

        if response.status_code != 200:
            return []

        data = response.json()
        if data.get("status") != "success":
            return []

        infos = data.get("data", {}).get("infos", [])
        return infos if isinstance(infos, list) else []

    async def list_audio_chapters(
        self,
        dl_protect_link: str,
        apikey: str,
        config: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        redirected_links = await self._extract_redirector_links(
            dl_protect_link, apikey, config
        )
        if not redirected_links:
            return []

        infos = await self._fetch_link_infos(redirected_links, apikey, config)
        if not infos:
            return []

        if len(redirected_links) == 1:
            filename = ""
            if not infos[0].get("error"):
                filename = infos[0].get("filename") or ""
            if not _is_audio_filename(filename):
                return []

        chapters: List[Dict[str, Any]] = []

        for index, info in enumerate(infos):
            if info.get("error"):
                continue

            filename = info.get("filename") or f"Fichier {index + 1}"
            if len(redirected_links) > 1 and not _is_audio_filename(filename):
                continue

            chapters.append(
                {
                    "index": index,
                    "episode": len(chapters) + 1,
                    "filename": filename,
                    "title": _format_chapter_title(filename),
                    "size": info.get("size"),
                }
            )

        if len(chapters) <= 1:
            return []

        chapters.sort(key=lambda chapter: chapter["filename"].lower())
        for episode_number, chapter in enumerate(chapters, start=1):
            chapter["episode"] = episode_number

        logger.log(
            "ALLDEBRID",
            f"Found {len(chapters)} audio chapter(s) in protector link",
        )
        return chapters

    # --- Link conversion ---
    async def convert_link(
        self,
        dl_protect_link: str,
        apikey: str,
        config: Optional[Dict] = None,
        file_index: int = 0,
    ) -> Optional[str]:
        if not apikey:
            logger.error("No AllDebrid API key provided")
            return None

        logger.log("ALLDEBRID", f"Converting: {dl_protect_link}")

        for attempt in range(ALLDEBRID_MAX_RETRIES):
            try:
                redirected_links = await self._extract_redirector_links(
                    dl_protect_link, apikey, config
                )
                if not redirected_links:
                    logger.error(f"No redirected links (attempt {attempt + 1}/{ALLDEBRID_MAX_RETRIES}, retry in {RETRY_DELAY_SECONDS}s)")
                    await sleep(RETRY_DELAY_SECONDS)
                    continue

                if file_index < 0 or file_index >= len(redirected_links):
                    logger.error(
                        f"Chapter index {file_index} out of range ({len(redirected_links)} files)"
                    )
                    return None

                target_link = redirected_links[file_index]
                response2 = await self._api_get(
                    "/link/unlock",
                    {**self._base_params(apikey), "link": target_link},
                    config,
                )
                
                if response2.status_code != 200:
                    logger.error(f"Unlock failed: {response2.status_code} (attempt {attempt + 1}/{ALLDEBRID_MAX_RETRIES}, retry in {RETRY_DELAY_SECONDS}s)")
                    await sleep(RETRY_DELAY_SECONDS)
                    continue
                
                data2 = response2.json()
                if data2.get("status") != "success":
                    error = data2.get("error", {})
                    
                    if error.get("code") == "LINK_DOWN":
                        logger.error(f"Unlock error: {error.get('code', 'UNKNOWN')} - {error.get('message', 'Unknown')}")
                        return "LINK_DOWN"
                    
                    logger.error(f"Unlock error: {error.get('code', 'UNKNOWN')} - {error.get('message', 'Unknown')} (attempt {attempt + 1}/{ALLDEBRID_MAX_RETRIES}, retry in {RETRY_DELAY_SECONDS}s)")
                    await sleep(RETRY_DELAY_SECONDS)
                    continue
                
                direct_link = data2.get("data", {}).get("link")
                if direct_link:
                    logger.log("ALLDEBRID", "Link converted successfully")
                    return direct_link
                
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e} (attempt {attempt + 1}/{ALLDEBRID_MAX_RETRIES}, retry in {RETRY_DELAY_SECONDS}s)")
                if attempt < ALLDEBRID_MAX_RETRIES - 1:
                    await sleep(RETRY_DELAY_SECONDS)
        
        logger.error(f"Failed after {ALLDEBRID_MAX_RETRIES} attempts")
        return None

# --- Global instance ---
alldebrid_service = AllDebridService()