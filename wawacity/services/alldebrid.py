import os
import hashlib
import asyncio
from typing import Optional, Dict, List, Any, Tuple
from urllib.parse import urlencode
from asyncio import sleep

import httpx

from wawacity.services.mediaflow import (
    build_archive_fetch_url,
    get_mediaflow_server_access,
    is_mediaflow_enabled,
    mediaflow_service,
)
from wawacity.utils.http_client import http_client
from wawacity.utils.archive import (
    is_archive_filename,
    is_audio_filename,
    list_audio_chapters_in_archive,
    extract_rar_audio_to_path,
    extract_zip_audio_to_path,
)
from wawacity.utils.audio_tags import (
    format_chapter_title_from_filename,
    merge_chapter_title,
)
from wawacity.core.config import (
    ALLDEBRID_API_URL,
    ALLDEBRID_MAX_RETRIES,
    RETRY_DELAY_SECONDS,
    CONTENT_CACHE_TTL,
)
from wawacity.utils.cache import get_cache, set_cache
from wawacity.utils.database import database
from wawacity.utils.logger import logger

AUDIO_CACHE_DIR = os.environ.get("AUDIO_CACHE_DIR", "/app/data/audio_cache")
ARCHIVE_LIST_TIMEOUT = 90.0
ARCHIVE_EXTRACT_TIMEOUT = 180.0

# Liens déjà pointant vers un hébergeur (ex. Bookys → 1fichier) : pas de redirector dl-protect.
DIRECT_HOSTER_MARKERS = (
    "1fichier.com",
    "turbobit.",
    "rapidgator.",
    "uptobox.",
    "dailyuploads.",
    "mega.nz",
    "mediafire.com",
)


def _is_direct_hoster_link(url: str) -> bool:
    lower = (url or "").lower()
    return any(marker in lower for marker in DIRECT_HOSTER_MARKERS)


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

        proxy_base, password = get_mediaflow_server_access(config)
        if proxy_base and password:
            logger.log(
                "ALLDEBRID",
                f"Routing API call via MediaFlow forward ({proxy_base})",
            )
            return await mediaflow_service.forward_get(
                proxy_base, password, destination
            )

        if is_mediaflow_enabled(config):
            logger.error(
                "AllDebrid API requires MediaFlow but mediaflow_url/password missing"
            )
            return httpx.Response(
                503,
                content=b'{"status":"error","error":{"code":"MEDIAFLOW_REQUIRED"}}',
                request=httpx.Request("GET", destination),
            )

        logger.log("ALLDEBRID", "MediaFlow disabled — direct AllDebrid call")
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

    async def _unlock_virtual_link(
        self,
        virtual_link: str,
        apikey: str,
        config: Optional[Dict] = None,
    ) -> Optional[str]:
        response = await self._api_get(
            "/link/unlock",
            {**self._base_params(apikey), "link": virtual_link},
            config,
        )
        if response.status_code != 200:
            logger.error(f"Unlock HTTP {response.status_code}")
            return None

        data = response.json()
        if data.get("status") != "success":
            error = data.get("error", {})
            logger.error(
                f"Unlock rejected: {error.get('code', 'UNKNOWN')} - "
                f"{error.get('message', 'Unknown')}"
            )
            return None

        return data.get("data", {}).get("link")

    async def _list_archive_chapters(
        self,
        virtual_link: str,
        archive_name: str,
        apikey: str,
        config: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        cache_id = hashlib.sha256(
            f"{virtual_link}|{archive_name}|id3v2".encode()
        ).hexdigest()[:32]
        cached = await get_cache(database, "archive_chapters", cache_id, None)
        if cached is not None:
            logger.log(
                "ALLDEBRID",
                f"Archive chapters cache hit ({len(cached)} file(s))",
            )
            return cached

        direct_url = await self._unlock_virtual_link(virtual_link, apikey, config)
        if not direct_url:
            return []

        fetch_url = build_archive_fetch_url(direct_url, config)

        try:
            audio_entries = await asyncio.wait_for(
                asyncio.to_thread(
                    list_audio_chapters_in_archive, fetch_url, archive_name
                ),
                timeout=ARCHIVE_LIST_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.log("ALLDEBRID", f"Archive listing timeout for '{archive_name}'")
            return []
        except Exception as e:
            logger.error(f"Archive listing failed for '{archive_name}': {e}")
            return []

        chapters: List[Dict[str, Any]] = []
        id3_count = 0
        for episode_number, (filename, tag_title) in enumerate(audio_entries, start=1):
            if tag_title:
                id3_count += 1
            chapters.append(
                {
                    "index": episode_number - 1,
                    "episode": episode_number,
                    "filename": filename,
                    "title": merge_chapter_title(
                        os.path.basename(filename), tag_title
                    ),
                    "archive": True,
                }
            )

        if chapters:
            await set_cache(
                database,
                "archive_chapters",
                cache_id,
                None,
                chapters,
                CONTENT_CACHE_TTL,
                None,
            )
            logger.log(
                "ALLDEBRID",
                f"Found {len(chapters)} audio chapter(s) inside archive '{archive_name}' "
                f"({id3_count} with embedded title)",
            )
        return chapters

    async def list_audio_chapters(
        self,
        dl_protect_link: str,
        apikey: str,
        config: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        if _is_direct_hoster_link(dl_protect_link):
            redirected_links = [dl_protect_link]
        else:
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
            if is_archive_filename(filename):
                return await self._list_archive_chapters(
                    redirected_links[0], filename, apikey, config
                )
            if not is_audio_filename(filename):
                return []

        chapters: List[Dict[str, Any]] = []

        for index, info in enumerate(infos):
            if info.get("error"):
                continue

            filename = info.get("filename") or f"Fichier {index + 1}"
            if len(redirected_links) > 1 and not is_audio_filename(filename):
                continue

            chapters.append(
                {
                    "index": index,
                    "episode": len(chapters) + 1,
                    "filename": filename,
                    "title": format_chapter_title_from_filename(filename),
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

    def _audio_cache_path(self, cache_id: str) -> str:
        return os.path.join(AUDIO_CACHE_DIR, f"{cache_id}.mp3")

    async def _extract_archive_chapter_url(
        self,
        virtual_link: str,
        archive_name: str,
        apikey: str,
        config: Optional[Dict],
        audio_index: int,
        serve_base_url: str,
    ) -> Optional[str]:
        cache_id = hashlib.sha256(
            f"{virtual_link}:{audio_index}:{archive_name}".encode()
        ).hexdigest()[:32]
        cache_path = self._audio_cache_path(cache_id)

        if not os.path.exists(cache_path):
            direct_url = await self._unlock_virtual_link(virtual_link, apikey, config)
            if not direct_url:
                return None

            fetch_url = build_archive_fetch_url(direct_url, config)

            try:
                audio_files = await asyncio.wait_for(
                    asyncio.to_thread(
                        list_audio_files_in_archive, fetch_url, archive_name
                    ),
                    timeout=ARCHIVE_LIST_TIMEOUT,
                )
            except Exception as e:
                logger.error(f"Archive listing failed during playback: {e}")
                return None

            if audio_index < 0 or audio_index >= len(audio_files):
                logger.error(
                    f"Archive chapter index {audio_index} out of range ({len(audio_files)} files)"
                )
                return None

            member_name = audio_files[audio_index]
            lower_archive = archive_name.lower()

            try:
                if lower_archive.endswith(".rar"):
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            extract_rar_audio_to_path,
                            fetch_url,
                            member_name,
                            cache_path,
                        ),
                        timeout=ARCHIVE_EXTRACT_TIMEOUT,
                    )
                elif lower_archive.endswith(".zip"):
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            extract_zip_audio_to_path,
                            fetch_url,
                            member_name,
                            cache_path,
                        ),
                        timeout=ARCHIVE_EXTRACT_TIMEOUT,
                    )
                else:
                    return None
            except asyncio.TimeoutError:
                logger.error(f"Archive extraction timeout for '{member_name}'")
                return None
            except Exception as e:
                logger.error(f"Archive extraction failed for '{member_name}': {e}")
                return None

        return f"{serve_base_url.rstrip('/')}/cached-audio/{cache_id}"

    # --- Link conversion ---
    async def convert_link(
        self,
        dl_protect_link: str,
        apikey: str,
        config: Optional[Dict] = None,
        file_index: int = 0,
        serve_base_url: Optional[str] = None,
    ) -> Optional[str]:
        if not apikey:
            logger.error("No AllDebrid API key provided")
            return None

        logger.log("ALLDEBRID", f"Converting: {dl_protect_link}")

        for attempt in range(ALLDEBRID_MAX_RETRIES):
            try:
                if _is_direct_hoster_link(dl_protect_link):
                    logger.log(
                        "ALLDEBRID",
                        "Direct hoster URL (Bookys etc.), using /link/unlock",
                    )
                    redirected_links = [dl_protect_link]
                else:
                    redirected_links = await self._extract_redirector_links(
                        dl_protect_link, apikey, config
                    )
                if not redirected_links:
                    logger.error(f"No redirected links (attempt {attempt + 1}/{ALLDEBRID_MAX_RETRIES}, retry in {RETRY_DELAY_SECONDS}s)")
                    await sleep(RETRY_DELAY_SECONDS)
                    continue

                infos = await self._fetch_link_infos(redirected_links, apikey, config)
                if (
                    len(redirected_links) == 1
                    and infos
                    and not infos[0].get("error")
                    and is_archive_filename(infos[0].get("filename") or "")
                ):
                    if not serve_base_url:
                        logger.error("Archive playback requires serve_base_url")
                        return None
                    archive_name = infos[0].get("filename") or ""
                    extracted = await self._extract_archive_chapter_url(
                        redirected_links[0],
                        archive_name,
                        apikey,
                        config,
                        file_index,
                        serve_base_url,
                    )
                    if extracted:
                        logger.log("ALLDEBRID", "Archive chapter prepared for playback")
                        return extracted
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