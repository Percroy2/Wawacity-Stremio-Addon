from typing import Dict, List, Optional
import asyncio
from re import findall

from selectolax.parser import HTMLParser

from wawacity.scrapers.base import BaseScraper
from wawacity.core.config import WAWACITY_URL, CONTENT_CACHE_TTL
from wawacity.core.categories import AUDIOBOOK_GENRE_SLUGS
from wawacity.utils.http_client import http_client
from wawacity.utils.helpers import format_url, quote_url_param, extract_filename_from_link
from wawacity.utils.audiobook_ids import wawacity_href_to_stremio_id, wawacity_page_path
from wawacity.utils.cache import get_cache, set_cache
from wawacity.utils.database import database, SearchLock
from wawacity.utils.logger import logger

AUDIOBOOK_SUBCATEGORY = "audiobooks"
CATALOG_PAGE_SIZE = 20
CATALOG_FETCH_TIMEOUT = 20.0


class AudiobookScraper(BaseScraper):
    async def search(
        self,
        title: str,
        year: Optional[str] = None,
        wawacity_url: Optional[str] = None,
    ) -> List[Dict]:
        base_url = (wawacity_url or WAWACITY_URL).rstrip("/")

        try:
            search_result = await self._search_audiobook(title, base_url)
            if not search_result:
                return []

            return await self._extract_links(search_result, base_url)
        except Exception as e:
            logger.error(f"Audiobook search failed for '{title}': {e}")
            return []

    async def get_streams_by_page_path(
        self, page_path: str, wawacity_url: Optional[str] = None
    ) -> List[Dict]:
        base_url = (wawacity_url or WAWACITY_URL).rstrip("/")
        page_path = page_path.lstrip("/")

        async with SearchLock("audiobook_page", page_path, None):
            cached = await get_cache(database, "audiobook_page", page_path, None, base_url)
            if cached is not None:
                return cached

            search_result = {"link": page_path, "text": ""}
            results = await self._extract_links(search_result, base_url)

            if results:
                await set_cache(
                    database,
                    "audiobook_page",
                    page_path,
                    None,
                    results,
                    CONTENT_CACHE_TTL,
                    base_url,
                )

            return results

    async def list_catalog(
        self,
        wawacity_url: str,
        search: Optional[str] = None,
        genre: Optional[str] = None,
        skip: int = 0,
    ) -> List[Dict]:
        base_url = wawacity_url.rstrip("/")
        page = skip // CATALOG_PAGE_SIZE + 1
        genre_slug = AUDIOBOOK_GENRE_SLUGS.get(genre, genre) if genre else None
        cache_label = f"{search or ''}:{genre_slug or ''}:{page}"

        cached = await get_cache(
            database, "audiobook_catalog", cache_label, None, base_url
        )
        if cached is not None:
            return cached

        listing_url = self._build_listing_url(base_url, search, genre_slug, page)
        logger.log("SCRAPER", f"Fetching audiobook catalog: {listing_url}")

        try:
            response = await asyncio.wait_for(
                http_client.get(listing_url),
                timeout=CATALOG_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"Audiobook catalog timed out: {listing_url}")
            return []
        except Exception as e:
            logger.error(f"Audiobook catalog request failed: {e}")
            return []

        if response.status_code != 200:
            logger.error(f"Audiobook catalog failed: {response.status_code}")
            return []

        metas = self._parse_listing_page(response.text, base_url)

        if metas:
            await set_cache(
                database,
                "audiobook_catalog",
                cache_label,
                None,
                metas,
                CONTENT_CACHE_TTL,
                base_url,
            )

        return metas

    async def get_meta(self, wawacity_url: str, ebook_id: str) -> Optional[Dict]:
        base_url = wawacity_url.rstrip("/")
        page_path = wawacity_page_path(ebook_id)
        stremio_id = f"wa:ebook:{ebook_id}"

        cached = await get_cache(database, "audiobook_meta", ebook_id, None, base_url)
        if cached is not None and isinstance(cached, dict):
            return cached

        page_url = f"{base_url}/{page_path.lstrip('/')}"
        try:
            response = await asyncio.wait_for(
                http_client.get(page_url),
                timeout=CATALOG_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"Audiobook meta timed out: {page_url}")
            return None
        except Exception as e:
            logger.error(f"Audiobook meta request failed: {e}")
            return None

        if response.status_code != 200:
            return None

        meta = self._parse_detail_page(response.text, base_url, stremio_id, ebook_id)
        if meta:
            await set_cache(
                database,
                "audiobook_meta",
                ebook_id,
                None,
                meta,
                CONTENT_CACHE_TTL,
                base_url,
            )

        return meta

    def _build_listing_url(
        self,
        base_url: str,
        search: Optional[str],
        genre_slug: Optional[str],
        page: int,
    ) -> str:
        query = f"?p=ebooks&s={AUDIOBOOK_SUBCATEGORY}"

        if search:
            query += f"&search={quote_url_param(str(search)[:31])}"
        if genre_slug:
            query += f"&genre={quote_url_param(genre_slug)}"
        if page > 1:
            query += f"&page={page}"

        return f"{base_url}/{query}"

    def _parse_listing_page(self, html: str, base_url: str) -> List[Dict]:
        parser = HTMLParser(html)
        seen = set()
        metas: List[Dict] = []

        for block in parser.css("div.wa-sub-block.wa-post-detail-item"):
            title_link = block.css_first(
                'div.wa-sub-block-title a[href^="?p=ebook&id="]'
            )
            if not title_link:
                continue

            href = title_link.attributes.get("href", "")
            stremio_id = wawacity_href_to_stremio_id(href)
            if not stremio_id or stremio_id in seen:
                continue

            seen.add(stremio_id)
            name = title_link.text(strip=True) or "Livre audio"

            img = block.css_first("img.img-responsive")
            poster = ""
            if img:
                poster = format_url(img.attributes.get("src", ""), base_url)

            genres = []
            for genre_link in block.css('a[href*="genre="]'):
                label = genre_link.text(strip=True)
                if label and label not in genres:
                    genres.append(label)

            desc_node = block.css_first("p")
            description = desc_node.text(strip=True) if desc_node else ""
            if len(description) > 300:
                description = description[:297] + "..."

            metas.append(
                {
                    "id": stremio_id,
                    "type": "series",
                    "name": name,
                    "poster": poster,
                    "description": description,
                    "genres": genres[:3] if genres else ["Audiobook"],
                }
            )

        return metas

    def _parse_detail_page(
        self, html: str, base_url: str, stremio_id: str, ebook_id: str
    ) -> Optional[Dict]:
        parser = HTMLParser(html)
        title_node = parser.css_first("div.wa-sub-block-title a")
        if not title_node:
            return None

        name = title_node.text(strip=True) or "Livre audio"

        img = parser.css_first("img.img-responsive")
        poster = ""
        if img:
            poster = format_url(img.attributes.get("src", ""), base_url)

        genres = []
        for genre_link in parser.css('a[href*="genre="]'):
            label = genre_link.text(strip=True)
            if label and label not in genres:
                genres.append(label)

        desc_node = parser.css_first("div.wa-sub-block.wa-post-detail-item p")
        description = desc_node.text(strip=True) if desc_node else ""

        video_id = f"{stremio_id}:1:1"

        return {
            "id": stremio_id,
            "type": "series",
            "name": name,
            "poster": poster,
            "description": description,
            "genres": genres[:5] if genres else ["Audiobook"],
            "videos": [
                {
                    "id": video_id,
                    "title": "Livre audio",
                    "season": 1,
                    "episode": 1,
                }
            ],
        }

    async def _search_audiobook(self, title: str, base_url: str) -> Optional[Dict]:
        encoded_title = quote_url_param(str(title)[:31])
        search_url = (
            f"{base_url}/?p=ebooks&s={AUDIOBOOK_SUBCATEGORY}&search={encoded_title}"
        )

        logger.log("SCRAPER", f"Searching audiobooks: {search_url}")

        try:
            response = await http_client.get(search_url)
            if response.status_code != 200:
                logger.error(f"Audiobook search failed: {response.status_code}")
                return None

            parser = HTMLParser(response.text)
            search_nodes = parser.css('a[href^="?p=ebook&id="]')

            if not search_nodes:
                logger.error(f"No audiobook links found for '{title}'")
                return None

            first_link = search_nodes[0].attributes.get("href", "")
            detail_url = f"{base_url}/{first_link}"
            response2 = await http_client.get(detail_url)
            if response2.status_code != 200:
                return None

            parser2 = HTMLParser(response2.text)
            title_nodes = parser2.css("div.wa-sub-block-title a")

            page_title = title
            if title_nodes:
                page_title = title_nodes[0].text(strip=True) or title

            return {"link": first_link, "text": page_title}
        except Exception as e:
            logger.error(f"Failed to search audiobook: {e}")
            return None

    async def _extract_links(self, search_result: Dict, base_url: str) -> List[Dict]:
        results: List[Dict] = []
        page_path = search_result.get("link", "")
        page_title = search_result.get("text", "")

        if not page_path:
            return results

        quality_txt = "AudioBooks"
        language_txt = "N/A"

        parts = page_title.split("]")
        if len(parts) >= 2:
            label_part = parts[-1].translate(str.maketrans({"[": "", "]": ""})).strip()
            items = findall(r"([\w\- ]+)(?!\()", label_part)
            if items and items[0].strip():
                quality_txt = items[0].strip()

        page_url = f"{base_url}/{page_path.lstrip('/')}"

        try:
            response = await http_client.get(page_url)
            if response.status_code != 200:
                return results

            parser = HTMLParser(response.text)
            if not page_title:
                title_node = parser.css_first("div.wa-sub-block-title a")
                if title_node:
                    page_title = title_node.text(strip=True) or "Livre audio"

            link_rows = parser.css("#DDLLinks tr.link-row:nth-child(n+2)")

            if not link_rows:
                logger.log("SCRAPER", f"No download links for audiobook '{page_title}'")
                return results

            filtered_rows = self.filter_nodes(link_rows, r"Lien .*")
            if not filtered_rows:
                return results

            for row in filtered_rows:
                hoster_cell = row.css_first('td[width="120px"].text-center')
                hoster_name = hoster_cell.text().strip() if hoster_cell else ""

                if hoster_name.lower() not in ["1fichier", "turbobit", "rapidgator"]:
                    continue

                size_td = row.css_first('td[width="80px"].text-center')
                file_size = size_td.text().strip() if size_td else "?"

                link_node = row.css_first('a[href*="dl-protect."].link')
                if not link_node:
                    continue

                url = self.extract_link_from_node(link_node)
                if not url:
                    continue

                url = format_url(url, base_url)
                link_text = link_node.text(strip=True) if link_node else ""
                decoded_fn = extract_filename_from_link(url, link_text)
                hoster_title = hoster_name.title()

                results.append(
                    {
                        "label": f"{quality_txt} ({hoster_title})",
                        "language": language_txt,
                        "quality": quality_txt,
                        "hoster": hoster_title,
                        "size": file_size,
                        "dl_protect": url,
                        "display_name": decoded_fn or page_title,
                        "category": "audiobook",
                    }
                )

            results.sort(key=self.quality_sort_key)
        except Exception as e:
            logger.error(f"Failed to extract audiobook links: {e}")

        return results


audiobook_scraper = AudiobookScraper()
