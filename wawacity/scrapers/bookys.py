import asyncio
import re
from typing import Dict, List, Optional
from urllib.parse import quote_plus

from selectolax.parser import HTMLParser

from wawacity.core.config import (
    CONTENT_CACHE_TTL,
    BOOKYS_EBOOK_AUDIO_CAT_ID,
    FLARESOLVERR_URL,
)
from wawacity.scrapers.base import BaseScraper
from wawacity.utils.bookys_ids import bookys_book_path, bookys_href_to_stremio_id
from wawacity.utils.cache import get_cache, set_cache
from wawacity.utils.database import SearchLock, database
from wawacity.utils.helpers import format_url, quote_url_param
from wawacity.utils.http_client import http_client
from wawacity.utils.logger import logger

BOOKYS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

ALLOWED_HOSTERS = {
    "1fichier": "1fichier",
    "turbobit": "Turbobit",
    "rapidgator": "Rapidgator",
    "uptobox": "Uptobox",
    "dailyuploads": "Dailyuploads",
}

HOSTER_URL_PATTERNS = (
    "1fichier.com",
    "turbobit.",
    "rapidgator.",
    "uptobox.",
    "dailyuploads.",
    "dl-protect.",
)

FETCH_TIMEOUT = 20.0
CATALOG_PAGE_SIZE = 20
BOOKYS_EBOOK_AUDIO_SLUG = "ebook-audio"
SEARCH_MAX_BOOKS = 8


class BookysScraper(BaseScraper):
    async def _get_html(self, url: str) -> Optional[str]:
        if FLARESOLVERR_URL:
            from wawacity.utils.flaresolverr import fetch_html

            return await fetch_html(url)

        try:
            response = await asyncio.wait_for(
                http_client.get(url, headers=BOOKYS_HEADERS),
                timeout=FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"Bookys request timed out: {url}")
            return None
        except Exception as e:
            logger.error(f"Bookys request failed: {e}")
            return None

        if response.status_code != 200:
            logger.error(f"Bookys HTTP {response.status_code}: {url}")
            return None

        text = response.text or ""
        if "Just a moment" in text or "cf-challenge" in text.lower():
            logger.error(
                "Bookys blocked by Cloudflare — set FLARESOLVERR_URL or PROXY_URL"
            )
            return None

        return text

    def _book_page_url(self, base_url: str, book_path: str) -> str:
        return f"{base_url.rstrip('/')}/livres/{bookys_book_path(book_path)}"

    def _search_url(self, base_url: str, query: str, page: int = 1) -> str:
        cat_id = (BOOKYS_EBOOK_AUDIO_CAT_ID or "75").strip()
        url = (
            f"{base_url.rstrip('/')}/search?cat={quote_plus(cat_id)}"
            f"&m=fuzzy&q={quote_plus(query)}"
        )
        if page > 1:
            url += f"&page={page}"
        return url

    def _listing_url(self, base_url: str, page: int) -> str:
        url = f"{base_url.rstrip('/')}/livres/{BOOKYS_EBOOK_AUDIO_SLUG}"
        if page > 1:
            return f"{url}?page={page}"
        return url

    async def search(
        self,
        title: str,
        year: Optional[str] = None,
        bookys_url: Optional[str] = None,
    ) -> List[Dict]:
        if not bookys_url:
            return []

        base_url = bookys_url.rstrip("/")

        book_paths = await self._search_book_paths(title, base_url)
        if not book_paths:
            return []

        results: List[Dict] = []
        for book_path in book_paths[:SEARCH_MAX_BOOKS]:
            results.extend(
                await self.get_streams_by_book_path(book_path, base_url)
            )

        return results

    async def _search_book_paths(self, title: str, base_url: str) -> List[str]:
        query = str(title)[:80]
        search_url = self._search_url(base_url, query)
        logger.log("SCRAPER", f"Bookys search (ebook audio): {search_url}")

        html = await self._get_html(search_url)
        if not html:
            return []

        parser = HTMLParser(html)
        metas = self._parse_search_results_table(parser, base_url)
        paths = []
        for meta in metas:
            stremio_id = meta.get("id", "")
            if stremio_id.startswith("bk:ebook:"):
                paths.append(stremio_id[len("bk:ebook:") :])

        if not paths:
            logger.error(f"No Bookys ebook-audio results for '{title}'")

        return paths

    async def get_streams_by_book_path(
        self, book_path: str, bookys_url: Optional[str] = None
    ) -> List[Dict]:
        if not bookys_url:
            return []

        base_url = bookys_url.rstrip("/")

        book_path = bookys_book_path(book_path)
        cache_key = book_path

        async with SearchLock("bookys_page", cache_key, None):
            cached = await get_cache(database, "bookys_page", cache_key, None, base_url)
            if cached is not None:
                return cached

            results = await self._extract_book_links(book_path, base_url)

            if results:
                await set_cache(
                    database,
                    "bookys_page",
                    cache_key,
                    None,
                    results,
                    CONTENT_CACHE_TTL,
                    base_url,
                )

            return results

    async def _extract_book_links(self, book_path: str, base_url: str) -> List[Dict]:
        page_url = self._book_page_url(base_url, book_path)
        html = await self._get_html(page_url)
        if not html:
            return []

        parser = HTMLParser(html)
        title = ""
        title_node = parser.css_first("h1")
        if title_node:
            title = title_node.text(strip=True)

        rows = parser.css("tr[data-id]")
        if not rows:
            logger.log("SCRAPER", f"No Bookys download rows for '{book_path}'")
            return []

        results: List[Dict] = []

        for row in rows:
            dl_id = row.attributes.get("data-id", "")
            host_link = row.css_first("a.bys-host")
            if not dl_id or not host_link:
                continue

            host_label = host_link.text(strip=True).lower()
            hoster_key = None
            for key in ALLOWED_HOSTERS:
                if key in host_label:
                    hoster_key = key
                    break
            if not hoster_key:
                continue

            cells = row.css("td")
            fmt = cells[1].text(strip=True) if len(cells) > 1 else "?"
            language = cells[2].text(strip=True) if len(cells) > 2 else "N/A"
            size = cells[3].text(strip=True) if len(cells) > 3 else "?"

            dl_href = host_link.attributes.get("href", "")
            if not dl_href:
                continue

            dl_page_url = format_url(dl_href, base_url)
            host_url = await self._resolve_dl_page(dl_page_url, base_url)
            if not host_url:
                continue

            hoster_name = ALLOWED_HOSTERS[hoster_key]
            quality = fmt or "AudioBooks"

            results.append(
                {
                    "label": f"{quality} ({hoster_name})",
                    "language": language,
                    "quality": quality,
                    "hoster": hoster_name,
                    "size": size,
                    "dl_protect": host_url,
                    "display_name": title or book_path.replace("-", " "),
                    "category": "audiobook",
                    "provider": "bookys",
                }
            )

        results.sort(key=self.quality_sort_key)
        return results

    async def _resolve_dl_page(self, dl_page_url: str, base_url: str) -> Optional[str]:
        html = await self._get_html(dl_page_url)
        if not html:
            return None

        parser = HTMLParser(html)
        for link in parser.css("a"):
            href = link.attributes.get("href", "")
            if not href.startswith("http"):
                continue
            if any(pattern in href.lower() for pattern in HOSTER_URL_PATTERNS):
                return href

        match = re.search(
            r'href=["\'](https?://[^"\']+(?:1fichier|turbobit|rapidgator|uptobox|dailyuploads)[^"\']*)["\']',
            html,
            re.I,
        )
        if match:
            return match.group(1)

        return None

    async def list_catalog(
        self,
        bookys_url: str,
        search: Optional[str] = None,
        skip: int = 0,
    ) -> List[Dict]:
        base_url = bookys_url.rstrip("/")
        page = skip // CATALOG_PAGE_SIZE + 1
        cache_label = f"v2:cat{BOOKYS_EBOOK_AUDIO_CAT_ID}:{search or 'ebook-audio'}:{page}"

        cached = await get_cache(database, "bookys_catalog", cache_label, None, base_url)
        if cached is not None:
            return cached

        if search:
            listing_url = self._search_url(base_url, str(search)[:80], page=page)
        else:
            listing_url = self._listing_url(base_url, page)

        logger.log("SCRAPER", f"Bookys catalog: {listing_url}")
        html = await self._get_html(listing_url)
        if not html:
            return []

        metas = self._parse_listing_page(html, base_url)

        if metas:
            await set_cache(
                database,
                "bookys_catalog",
                cache_label,
                None,
                metas,
                CONTENT_CACHE_TTL,
                base_url,
            )

        return metas

    @staticmethod
    def _clean_listing_title(raw: str) -> str:
        if not raw:
            return ""
        title = raw.split("\n")[0].strip()
        title = re.sub(r"\s+Vues:\s*\d+.*$", "", title, flags=re.I).strip()
        return title

    def _parse_ebook_audio_grid(self, parser: HTMLParser, base_url: str) -> List[Dict]:
        seen = set()
        metas: List[Dict] = []

        for card in parser.css(f'a.bys-item[href*="/livres/"]'):
            href = card.attributes.get("href", "")
            stremio_id = bookys_href_to_stremio_id(href)
            if not stremio_id or stremio_id in seen:
                continue

            name = ""
            title_node = card.css_first("b.font-bold")
            if title_node:
                name = title_node.text(strip=True)

            if not name:
                img = card.css_first("img")
                if img:
                    name = (img.attributes.get("alt") or "").strip()

            name = self._clean_listing_title(name)
            if not name or len(name) < 4:
                continue

            poster = ""
            img = card.css_first("img")
            if img:
                poster = (
                    img.attributes.get("src")
                    or img.attributes.get("data-src")
                    or ""
                ).strip()
                poster = format_url(poster, base_url)

            seen.add(stremio_id)
            metas.append(self._build_catalog_meta(stremio_id, name, poster))

        return metas

    def _parse_search_results_table(self, parser: HTMLParser, base_url: str) -> List[Dict]:
        seen = set()
        metas: List[Dict] = []

        for link in parser.css('a.bys-link[href*="/livres/"]'):
            href = link.attributes.get("href", "")
            stremio_id = bookys_href_to_stremio_id(href)
            if not stremio_id or stremio_id in seen:
                continue

            name = self._clean_listing_title(link.text(strip=True))
            if not name or len(name) < 4:
                continue

            seen.add(stremio_id)
            metas.append(self._build_catalog_meta(stremio_id, name, ""))

        if metas:
            return metas

        for link in parser.css('a[href*="/livres/"]'):
            href = link.attributes.get("href", "")
            stremio_id = bookys_href_to_stremio_id(href)
            if not stremio_id or stremio_id in seen:
                continue

            name = self._clean_listing_title(link.text(strip=True))
            if not name or len(name) < 4:
                continue

            seen.add(stremio_id)
            metas.append(self._build_catalog_meta(stremio_id, name, ""))

        return metas

    def _parse_listing_page(self, html: str, base_url: str) -> List[Dict]:
        parser = HTMLParser(html)
        metas = self._parse_ebook_audio_grid(parser, base_url)
        if not metas:
            metas = self._parse_search_results_table(parser, base_url)

        return metas[:CATALOG_PAGE_SIZE]

    def _build_catalog_meta(self, stremio_id: str, name: str, poster: str) -> Dict:
        video_id = f"{stremio_id}:1:1"
        return {
            "id": stremio_id,
            "type": "series",
            "name": name,
            "poster": poster,
            "background": poster,
            "posterShape": "square",
            "description": name,
            "releaseInfo": "Bookys",
            "genres": ["Audiobook", "Bookys"],
            "videos": [
                {
                    "id": video_id,
                    "title": name,
                    "season": 1,
                    "episode": 1,
                    "thumbnail": poster,
                }
            ],
        }

    async def get_meta(
        self,
        bookys_url: str,
        book_path: str,
        config: Optional[Dict] = None,
    ) -> Optional[Dict]:
        base_url = bookys_url.rstrip("/")
        book_path = bookys_book_path(book_path)
        stremio_id = f"bk:ebook:{book_path}"
        cache_key = f"v2:{book_path}"

        cached = await get_cache(database, "bookys_meta", cache_key, None, base_url)
        if cached is not None:
            return cached

        page_url = self._book_page_url(base_url, book_path)
        html = await self._get_html(page_url)
        if not html:
            return None

        meta = self._parse_detail_page(html, stremio_id)
        if not meta:
            return None

        await set_cache(
            database,
            "bookys_meta",
            cache_key,
            None,
            meta,
            CONTENT_CACHE_TTL,
            base_url,
        )
        return meta

    def _parse_detail_page(self, html: str, stremio_id: str) -> Optional[Dict]:
        parser = HTMLParser(html)
        title_node = parser.css_first("h1")
        if not title_node:
            return None

        name = title_node.text(strip=True)
        description = ""
        for item in parser.css("li"):
            text = item.text(strip=True)
            if text.startswith("ISBN") or len(text) > 60:
                description = text
                break

        img = parser.css_first("img[src]")
        poster = ""
        if img:
            poster = img.attributes.get("src", "")

        video_id = f"{stremio_id}:1:1"
        return {
            "id": stremio_id,
            "type": "series",
            "name": name,
            "poster": poster,
            "posterShape": "square",
            "description": description or name,
            "genres": ["Audiobook", "Bookys"],
            "videos": [
                {
                    "id": video_id,
                    "title": "Livre audio",
                    "season": 1,
                    "episode": 1,
                }
            ],
        }


bookys_scraper = BookysScraper()
