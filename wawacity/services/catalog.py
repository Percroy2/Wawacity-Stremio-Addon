from typing import Dict, List, Optional

from wawacity.core.categories import LIVRES_CATALOG_ID, is_category_enabled
from wawacity.scrapers.audiobook import audiobook_scraper
from wawacity.utils.audiobook_ids import parse_audiobook_content_id
from wawacity.utils.helpers import get_wawacity_url
from wawacity.utils.logger import logger


class CatalogService:
    async def get_catalog(
        self,
        config: Dict,
        catalog_id: str,
        extra: Dict[str, str],
    ) -> Dict:
        if catalog_id != LIVRES_CATALOG_ID or not is_category_enabled(config, "audiobook"):
            return {"metas": []}

        search = extra.get("search") or None
        genre = extra.get("genre") or None
        skip_raw = extra.get("skip", "0") or "0"

        try:
            skip = max(0, int(skip_raw))
        except ValueError:
            skip = 0

        wawacity_url = get_wawacity_url(config)

        try:
            metas = await audiobook_scraper.list_catalog(
                wawacity_url,
                search=search,
                genre=genre,
                skip=skip,
            )
            return {"metas": metas}
        except Exception as e:
            logger.error(f"Catalog request failed: {e}")
            return {"metas": []}

    async def get_meta(
        self,
        config: Dict,
        content_type: str,
        meta_id: str,
    ) -> Dict:
        meta_id = meta_id.replace(".json", "")

        if not is_category_enabled(config, "audiobook"):
            return {"meta": {}}

        ebook_id, _, _ = parse_audiobook_content_id(meta_id)
        if not ebook_id and meta_id.startswith("wa:ebook:"):
            ebook_id = meta_id[len("wa:ebook:") :]

        if not ebook_id:
            return {"meta": {}}

        wawacity_url = get_wawacity_url(config)

        try:
            meta = await audiobook_scraper.get_meta(wawacity_url, ebook_id)
            if not meta:
                return {"meta": {}}
            return {"meta": meta}
        except Exception as e:
            logger.error(f"Meta request failed for {meta_id}: {e}")
            return {"meta": {}}


catalog_service = CatalogService()
