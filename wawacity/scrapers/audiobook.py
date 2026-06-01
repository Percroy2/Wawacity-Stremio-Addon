from typing import List, Dict, Optional
from re import findall

from selectolax.parser import HTMLParser

from wawacity.scrapers.base import BaseScraper
from wawacity.core.config import WAWACITY_URL
from wawacity.utils.http_client import http_client
from wawacity.utils.helpers import format_url, quote_url_param, extract_filename_from_link
from wawacity.utils.logger import logger

AUDIOBOOK_SUBCATEGORY = "audiobooks"


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

        page_url = f"{base_url}/{page_path}"

        try:
            response = await http_client.get(page_url)
            if response.status_code != 200:
                return results

            parser = HTMLParser(response.text)
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
