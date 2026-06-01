import re
from typing import Optional, Dict, List

from wawacity.core.config import CONTENT_CACHE_TTL
from wawacity.utils.http_client import http_client
from wawacity.utils.logger import logger
from wawacity.utils.database import database
from wawacity.utils.cache import get_cache, set_cache

OPENLIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
OPENLIBRARY_WORK_URL = "https://openlibrary.org/works/{work_id}.json"
OPENLIBRARY_COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"


class OpenLibraryService:
    async def get_metadata(self, content_id: str) -> Optional[Dict]:
        work_id = self._extract_work_id(content_id)
        if not work_id:
            return None

        try:
            response = await http_client.get(
                OPENLIBRARY_WORK_URL.format(work_id=work_id),
                timeout=10,
            )
            if response.status_code != 200:
                return None

            data = response.json()
            title = data.get("title")
            if not title:
                return None

            year = None
            for date_key in ("first_publish_date", "created"):
                raw = data.get(date_key)
                if isinstance(raw, str) and len(raw) >= 4 and raw[:4].isdigit():
                    year = raw[:4]
                    break

            return {"title": title, "year": year, "type": "audiobook"}
        except Exception as e:
            logger.error(f"Open Library metadata fetch failed: {e}")
            return None

    async def get_cover_url(self, title: str) -> Optional[str]:
        query = self._normalize_title_for_search(title)
        if not query or len(query) < 3:
            return None

        cache_label = query[:120]
        cached = await get_cache(database, "ol_cover", cache_label, None, None)
        if cached is not None:
            return self._cover_from_cache_entry(cached)

        cover_url = await self._fetch_cover_url(query)
        await set_cache(
            database,
            "ol_cover",
            cache_label,
            None,
            [{"url": cover_url}] if cover_url else [],
            CONTENT_CACHE_TTL,
            None,
        )
        return cover_url

    async def _fetch_cover_url(self, query: str) -> Optional[str]:
        try:
            response = await http_client.get(
                OPENLIBRARY_SEARCH_URL,
                params={"q": query, "limit": 1, "fields": "cover_i,title"},
                timeout=10,
            )
        except Exception as e:
            logger.error(f"Open Library cover search failed: {e}")
            return None

        if response.status_code != 200:
            return None

        try:
            docs = response.json().get("docs") or []
        except ValueError:
            return None

        if not docs:
            return None

        cover_id = docs[0].get("cover_i")
        if not cover_id:
            return None

        return OPENLIBRARY_COVER_URL.format(cover_id=cover_id)

    @staticmethod
    def _normalize_title_for_search(name: str) -> str:
        if not name:
            return ""
        title = name.split("\n")[0].strip()
        title = title.split(" - ")[0].strip()
        title = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()
        return title

    @staticmethod
    def _cover_from_cache_entry(cached: List) -> Optional[str]:
        if not cached:
            return None
        entry = cached[0]
        if isinstance(entry, str):
            return entry or None
        if isinstance(entry, dict):
            return entry.get("url") or None
        return None

    @staticmethod
    def _extract_work_id(content_id: str) -> Optional[str]:
        cleaned = content_id.replace(".json", "").strip()
        if cleaned.startswith("ol:"):
            return cleaned[3:]
        if cleaned.startswith("/works/"):
            return cleaned.split("/works/")[-1].split(".")[0]
        if cleaned.startswith("OL") and cleaned.endswith("W"):
            return cleaned
        return None


openlibrary_service = OpenLibraryService()
