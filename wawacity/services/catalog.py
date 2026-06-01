from typing import Dict, List, Optional

from wawacity.core.config import CONTENT_CACHE_TTL
from wawacity.core.categories import (
    LIVRES_SEARCH_CATALOG_ID,
    LIVRES_CATALOG_IDS,
    is_category_enabled,
)
from wawacity.scrapers.audiobook import audiobook_scraper
from wawacity.scrapers.bookys import bookys_scraper
from wawacity.services.alldebrid import alldebrid_service
from wawacity.services.openlibrary import openlibrary_service
from wawacity.utils.audiobook_ids import parse_audiobook_content_id
from wawacity.utils.bookys_ids import parse_bookys_content_id, bookys_book_path
from wawacity.utils.cache import set_cache
from wawacity.utils.database import database
from wawacity.utils.helpers import (
    get_wawacity_url,
    get_bookys_url,
    is_bookys_enabled,
    pick_audiobook_stream_link,
)
from wawacity.utils.poster import poster_proxy_url, is_bookys_poster_url
from wawacity.utils.logger import logger


async def _apply_openlibrary_covers(metas: List[Dict]) -> List[Dict]:
    enriched: List[Dict] = []
    for meta in metas:
        item = dict(meta)
        meta_id = item.get("id") or ""
        poster = item.get("poster") or ""
        needs_cover = meta_id.startswith("bk:ebook:") or is_bookys_poster_url(poster)
        if needs_cover:
            cover = await openlibrary_service.get_cover_url(item.get("name") or "")
            if cover:
                item["poster"] = cover
                item["background"] = cover
                if item.get("videos"):
                    item["videos"] = [
                        {**video, "thumbnail": cover} for video in item["videos"]
                    ]
        enriched.append(item)
    return enriched


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


def _videos_from_audio_chapters(
    stremio_id: str,
    chapters: List[Dict],
    poster: str = "",
) -> List[Dict]:
    videos: List[Dict] = []
    for chapter in chapters:
        episode = int(chapter.get("episode") or len(videos) + 1)
        video: Dict = {
            "id": f"{stremio_id}:1:{episode}",
            "title": chapter.get("title") or f"Chapitre {episode}",
            "season": 1,
            "episode": episode,
        }
        if poster:
            video["thumbnail"] = poster
        videos.append(video)
    return videos


async def _enrich_meta_with_archive_chapters(
    meta: Dict,
    stream_link: str,
    config: Dict,
) -> Dict:
    apikey = (config.get("alldebrid") or "").strip()
    if not apikey or not stream_link:
        return meta

    try:
        chapters = await alldebrid_service.list_audio_chapters(
            stream_link, apikey, config
        )
    except Exception as e:
        logger.error(f"Archive chapter listing for meta failed: {e}")
        return meta

    if len(chapters) <= 1:
        return meta

    stremio_id = meta.get("id") or ""
    poster = meta.get("poster") or ""
    enriched = dict(meta)
    enriched["videos"] = _videos_from_audio_chapters(stremio_id, chapters, poster)
    logger.log(
        "SCRAPER",
        f"Meta enriched with {len(enriched['videos'])} chapter(s) for '{meta.get('name', '')}'",
    )
    return enriched


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

        # Ancien catalogue « wawacity_livres_search » : manifest obsolète côté Stremio.
        # Toujours vide pour éviter une 2e rangée identique tant que le client n'a pas resynchronisé.
        if catalog_id == LIVRES_SEARCH_CATALOG_ID:
            return {"metas": [], "cacheMaxAge": 60}

        search = (extra.get("search") or "").strip() or None
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

            bookys_url = get_bookys_url(config)
            if is_bookys_enabled(config) and bookys_url and not genre:
                bookys_metas = await bookys_scraper.list_catalog(
                    bookys_url,
                    search=search,
                    skip=skip,
                )
                if search:
                    seen_ids = {m.get("id") for m in metas}
                    for meta in bookys_metas:
                        if meta.get("id") not in seen_ids:
                            metas.append(meta)
                            seen_ids.add(meta.get("id"))
                else:
                    seen_ids = {m.get("id") for m in bookys_metas}
                    for meta in metas:
                        if meta.get("id") not in seen_ids:
                            bookys_metas.append(meta)
                            seen_ids.add(meta.get("id"))
                    metas = bookys_metas

            if is_bookys_enabled(config):
                metas = await _apply_openlibrary_covers(metas)

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

        book_path, _, _ = parse_bookys_content_id(meta_id)
        if book_path:
            bookys_url = get_bookys_url(config)
            if not bookys_url:
                return {"meta": {}}
            try:
                meta = await bookys_scraper.get_meta(bookys_url, book_path, config)
                if not meta:
                    return {"meta": {}}
                meta = dict(meta)
                streams = await bookys_scraper.get_streams_by_book_path(
                    book_path, bookys_url
                )
                stream_link = pick_audiobook_stream_link(streams)
                if stream_link:
                    meta = await _enrich_meta_with_archive_chapters(
                        meta, stream_link, config
                    )
                    await set_cache(
                        database,
                        "bookys_meta",
                        f"v2:{book_path}",
                        None,
                        meta,
                        CONTENT_CACHE_TTL,
                        bookys_url,
                    )
                cover = await openlibrary_service.get_cover_url(meta.get("name") or "")
                if cover:
                    meta["poster"] = cover
                    meta["background"] = cover
                elif addon_base_url and meta.get("poster"):
                    poster = poster_proxy_url(addon_base_url, meta["poster"])
                    meta["poster"] = poster
                    meta["background"] = poster
                return {"meta": meta}
            except Exception as e:
                logger.error(f"Bookys meta request failed for {meta_id}: {e}")
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
