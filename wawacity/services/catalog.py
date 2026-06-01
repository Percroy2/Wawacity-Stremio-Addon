from typing import Dict, List, Optional

from wawacity.core.config import CONTENT_CACHE_TTL
from wawacity.core.categories import (
    LIVRES_SEARCH_CATALOG_ID,
    LIVRES_CATALOG_IDS,
    is_category_enabled,
)
from wawacity.scrapers.audiobook import audiobook_scraper
from wawacity.utils.audiobook_ids import parse_audiobook_content_id
from wawacity.utils.helpers import get_wawacity_url
from wawacity.utils.poster import poster_proxy_url
from wawacity.utils.logger import logger


def _proxy_catalog_posters(metas: List[Dict], addon_base_url: Optional[str]) -> List[Dict]:
    if not addon_base_url:
        return metas

    proxied: List[Dict] = []
    for meta in metas:
        item = dict(meta)
        poster = item.get("poster")
        if poster:
            proxied_poster = poster_proxy_url(addon_base_url, poster)
            item["poster"] = proxied_poster
            item["background"] = proxied_poster
            if item.get("videos"):
                item["videos"] = [
                    {
                        **video,
                        "thumbnail": proxied_poster,
                    }
                    for video in item["videos"]
                ]
        proxied.append(item)
    return proxied


class CatalogService:
    async def get_catalog(
        self,
        config: Dict,
        catalog_id: str,
        extra: Dict[str, str],
        addon_base_url: Optional[str] = None,
    ) -> Dict:
        if catalog_id not in LIVRES_CATALOG_IDS or not is_category_enabled(
            config, "audiobook"
        ):
            return {"metas": [], "cacheMaxAge": CONTENT_CACHE_TTL}

        search = (extra.get("search") or "").strip() or None
        if catalog_id == LIVRES_SEARCH_CATALOG_ID and not search:
            return {"metas": [], "cacheMaxAge": 60}
        genre = (extra.get("genre") or "").strip() or None
        skip_raw = extra.get("skip", "0") or "0"

        try:
            skip = max(0, int(skip_raw))
        except ValueError:
            skip = 0

        wawacity_url = get_wawacity_url(config)

        if search:
            logger.log("SCRAPER", f"Catalog search: '{search}' (skip={skip})")

        try:
            metas = await audiobook_scraper.list_catalog(
                wawacity_url,
                search=search,
                genre=genre,
                skip=skip,
            )
            metas = _proxy_catalog_posters(metas, addon_base_url)
            logger.log("SCRAPER", f"Catalog returning {len(metas)} item(s)")
            return {"metas": metas, "cacheMaxAge": 120}
        except Exception as e:
            logger.error(f"Catalog request failed: {e}")
            return {"metas": [], "cacheMaxAge": 60}

    async def get_meta(
        self,
        config: Dict,
        content_type: str,
        meta_id: str,
        addon_base_url: Optional[str] = None,
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
            meta = await audiobook_scraper.get_meta(wawacity_url, ebook_id, config)
            if not meta:
                return {"meta": {}}
            if addon_base_url and meta.get("poster"):
                poster = poster_proxy_url(addon_base_url, meta["poster"])
                meta = dict(meta)
                meta["poster"] = poster
                meta["background"] = poster
            return {"meta": meta}
        except Exception as e:
            logger.error(f"Meta request failed for {meta_id}: {e}")
            return {"meta": {}}


catalog_service = CatalogService()
