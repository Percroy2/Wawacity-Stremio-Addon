from typing import Optional, Dict

from wawacity.utils.http_client import http_client
from wawacity.utils.logger import logger

OPENLIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
OPENLIBRARY_WORK_URL = "https://openlibrary.org/works/{work_id}.json"


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
