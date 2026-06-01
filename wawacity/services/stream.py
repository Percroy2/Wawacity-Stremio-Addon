from typing import List, Dict, Optional

from wawacity.services.tmdb import tmdb_service
from wawacity.services.openlibrary import openlibrary_service
from wawacity.services.alldebrid import alldebrid_service
from wawacity.scrapers.movie import movie_scraper
from wawacity.scrapers.series import series_scraper
from wawacity.scrapers.audiobook import audiobook_scraper
from wawacity.core.categories import is_category_enabled
from wawacity.utils.database import SearchLock, is_dead_link, mark_dead_link, database
from wawacity.utils.cache import get_cache, set_cache
from wawacity.utils.validators import extract_media_info
from wawacity.utils.helpers import encode_config_to_base64, quote_url_param, get_wawacity_url
from wawacity.utils.logger import logger
from wawacity.core.config import CONTENT_CACHE_TTL, DEAD_LINK_TTL


class StreamService:
    async def get_streams(
        self,
        content_type: str,
        content_id: str,
        config: Dict,
        base_url: str,
    ) -> List[Dict]:
        media_info = extract_media_info(content_id, content_type)
        category = media_info.get("category", content_type)

        if not is_category_enabled(config, category):
            logger.log("STREAM", f"Category '{category}' disabled in configuration")
            return []

        metadata = await self._get_metadata(media_info, config.get("tmdb", ""))
        if not metadata:
            logger.error(f"Failed to fetch metadata for {media_info.get('content_id', content_id)}")
            return []

        wawacity_url = get_wawacity_url(config)

        results = await self._search_content(
            metadata["title"],
            metadata.get("year"),
            category,
            media_info.get("season"),
            media_info.get("episode"),
            wawacity_url,
        )

        if not results:
            logger.error(
                f"No content found for '{metadata['title']}' ({metadata.get('year', 'N/A')})"
            )
            return []

        streams = await self._format_streams(
            results,
            config,
            base_url,
            media_info.get("season"),
            media_info.get("episode"),
            metadata.get("year"),
            category,
        )

        excluded_words = config.get("excluded_words", [])
        if excluded_words:
            filtered_streams = self._filter_excluded_words(streams, excluded_words)
            excluded_count = len(streams) - len(filtered_streams)
            if excluded_count > 0:
                logger.log("STREAM", f"Excluded {excluded_count} streams by filter")
            return filtered_streams

        return streams

    async def _get_metadata(self, media_info: Dict, tmdb_key: str) -> Optional[Dict]:
        category = media_info.get("category")

        if category == "audiobook":
            if media_info.get("search_title"):
                return {
                    "title": media_info["search_title"],
                    "year": None,
                    "type": "audiobook",
                }

            openlibrary_id = media_info.get("openlibrary_id") or media_info.get("content_id")
            if openlibrary_id:
                return await openlibrary_service.get_metadata(openlibrary_id)

            return None

        imdb_id = media_info.get("imdb_id")
        if not imdb_id:
            return None

        return await tmdb_service.get_metadata(imdb_id, tmdb_key)

    async def _search_content(
        self,
        title: str,
        year: Optional[str],
        category: str,
        season: Optional[str],
        episode: Optional[str],
        wawacity_url: str,
    ) -> List[Dict]:
        if category == "audiobook":
            return await self._search_audiobook(title, year, wawacity_url)
        if category == "series":
            return await self._search_series(title, year, season, episode, wawacity_url)
        return await self._search_movie(title, year, wawacity_url)

    async def _search_movie(self, title: str, year: Optional[str], wawacity_url: str) -> List[Dict]:
        async with SearchLock("film", title, year):
            cached_results = await get_cache(database, "film", title, year, wawacity_url)
            if cached_results is not None:
                return cached_results

            results = await movie_scraper.search(title, year, wawacity_url)

            if results:
                await set_cache(
                    database,
                    "film",
                    title,
                    year,
                    results,
                    CONTENT_CACHE_TTL,
                    wawacity_url,
                )

            return results

    async def _search_audiobook(
        self, title: str, year: Optional[str], wawacity_url: str
    ) -> List[Dict]:
        async with SearchLock("audiobook", title, year):
            cached_results = await get_cache(
                database, "audiobook", title, year, wawacity_url
            )
            if cached_results is not None:
                return cached_results

            results = await audiobook_scraper.search(title, year, wawacity_url)

            if results:
                await set_cache(
                    database,
                    "audiobook",
                    title,
                    year,
                    results,
                    CONTENT_CACHE_TTL,
                    wawacity_url,
                )

            return results

    async def _search_series(
        self,
        title: str,
        year: Optional[str],
        season: Optional[str],
        episode: Optional[str],
        wawacity_url: str,
    ) -> List[Dict]:
        async with SearchLock("serie", title, year):
            cached_results = await get_cache(database, "serie", title, year, wawacity_url)
            if cached_results is not None:
                if season and episode:
                    filtered = [
                        r
                        for r in cached_results
                        if r.get("season") == season and r.get("episode") == episode
                    ]
                    logger.log("STREAM", f"Filtered S{season}E{episode}: {len(filtered)} results")
                    return filtered
                return cached_results

            results = await series_scraper.search(title, year, wawacity_url)

            if results:
                await set_cache(
                    database,
                    "serie",
                    title,
                    year,
                    results,
                    CONTENT_CACHE_TTL,
                    wawacity_url,
                )

            if season and episode:
                filtered = [
                    r
                    for r in results
                    if r.get("season") == season and r.get("episode") == episode
                ]
                logger.log("STREAM", f"Filtered S{season}E{episode}: {len(filtered)} results")
                return filtered

            return results

    async def _format_streams(
        self,
        results: List[Dict],
        config: Dict,
        base_url: str,
        season: Optional[str],
        episode: Optional[str],
        year: Optional[str],
        category: str = "movie",
    ) -> List[Dict]:
        streams = []
        dead_links_count = 0
        stream_prefix = "🎧 Wawacity" if category == "audiobook" else "🌇 Wawacity"

        for res in results:
            dl_link = res.get("dl_protect")
            if not dl_link:
                continue

            if await is_dead_link(dl_link):
                dead_links_count += 1
                continue

            quality = res.get("quality", "?")
            language = res.get("language", "?")
            hoster = res.get("hoster", "?")
            size = res.get("size", "?")
            display_name = res.get("display_name", "?")

            q_link = quote_url_param(dl_link)
            config_b64 = encode_config_to_base64(config)
            q_b64config = quote_url_param(config_b64)

            playback_url = f"{base_url}/resolve?link={q_link}&b64config={q_b64config}"
            stream_name = f"{stream_prefix} {quality}"

            description_parts = []
            if language and language not in ["N/A", "?"]:
                description_parts.append(f"🌐 {language}")
            if quality and quality not in ["N/A", "?"]:
                icon = "🎧" if category == "audiobook" else "🎞️"
                description_parts.append(f"{icon} {quality}")
            if hoster and hoster not in ["N/A", "?"]:
                description_parts.append(f"☁️ {hoster}")

            size_year_parts = []
            if size and size not in ["N/A", "?"]:
                size_year_parts.append(f"📦 {size}")
            if year:
                size_year_parts.append(f"📅 {year}")
            if size_year_parts:
                description_parts.append(" ".join(size_year_parts))

            if display_name and display_name not in ["N/A", "?"]:
                description_parts.append(f"📁 {display_name}")

            streams.append(
                {
                    "name": stream_name,
                    "description": "\r\n".join(description_parts),
                    "url": playback_url,
                }
            )

        if dead_links_count > 0:
            logger.log("STREAM", f"Skipped {dead_links_count} dead links")

        logger.log("STREAM", f"Returning {len(streams)} stream(s)")
        return streams

    async def resolve_link(self, dl_protect_link: str, apikey: str) -> Optional[str]:
        result = await alldebrid_service.convert_link(dl_protect_link, apikey)

        if result == "LINK_DOWN":
            await mark_dead_link(dl_protect_link, DEAD_LINK_TTL)

        return result

    def _filter_excluded_words(
        self, streams: List[Dict], excluded_words: List[str]
    ) -> List[Dict]:
        if not excluded_words:
            return streams

        filtered_streams = []

        for stream in streams:
            stream_name = stream.get("name", "").lower()
            stream_desc = stream.get("description", "").lower()
            stream_text = f"{stream_name} {stream_desc}"

            exclude_stream = False
            for word in excluded_words:
                if word.lower() in stream_text:
                    exclude_stream = True
                    break

            if not exclude_stream:
                filtered_streams.append(stream)

        return filtered_streams


stream_service = StreamService()
