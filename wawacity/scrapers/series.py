import asyncio
from enum import Enum
import re
from re import search as re_search
from typing import Dict, List, Optional

from selectolax.parser import HTMLParser
from wawacity.core.config import WAWACITY_URL
from wawacity.scrapers.base import BaseScraper
from wawacity.utils.helpers import (
    extract_filename_from_link,
    format_url,
)
from wawacity.utils.http_client import http_client
from wawacity.utils.logger import logger


class SeriesScraper(BaseScraper):

    SERIES_ATTR_PATTERN = re.compile(r"\b(4K\s+UHD|HD)\b", re.IGNORECASE)
    class PAGE_TYPES(Enum):
        SEASON = 1
        QUALITY = 2

    def _parse_series_attributes(self, attribute_block: str) -> tuple[str, str]:
        # Clean extraneous hyphens (ex: "- MULTI 4K UHD" -> "MULTI 4K UHD")
        clean_block = attribute_block.strip("- ").strip()

        # Check if known quality is found (HD or 4K UHD)
        match = self.SERIES_ATTR_PATTERN.search(clean_block)

        if match:
            quality = match.group(1).upper()
            # Language should be the only thing remaining once qulity block is removed
            language = (
                self.SERIES_ATTR_PATTERN.sub("", clean_block)
                .strip("- ")
                .strip()
                .upper()
            )
        else:
            # if no known quality is found, N/A
            quality = "N/A"
            language = clean_block.upper()

        if not language:
            language = "N/A"

        return quality, language

    # --- Main search entry point ---
    async def search(
        self,
        title: str,
        year: Optional[str] = None,
        wawacity_url: Optional[str] = None,
    ) -> List[Dict]:
        base_url = (wawacity_url or WAWACITY_URL).rstrip("/")

        try:
            # --- Search for series ---
            search_result = await self._search_series(title, year, base_url)
            if not search_result:
                return []

            # --- Extract all episodes ---
            all_episodes = await self._extract_all_episodes(search_result, base_url)

            # --- Sort by season then episode ---
            all_episodes.sort(
                key=lambda x: (
                    int(x.get("season", "0")),
                    int(x.get("episode", "0")),
                    self.quality_sort_key(x),
                )
            )

            return all_episodes

        except Exception as e:
            logger.error(f"Series search failed for '{title}': {e}")
            return []

    # --- Initial series search ---
    async def _search_series(
        self, title: str, year: Optional[str], base_url: str
    ) -> Optional[Dict]:
        return await self._search_generic(
            title,
            year,
            base_url,
            search_path="series",
            link_prefix="?p=serie&id=",
            label="series",
        )

    # --- Extract all episodes from series ---
    async def _extract_all_episodes(
        self, search_result: Dict, base_url: str
    ) -> List[Dict]:
        all_results = []
        series_link = search_result["link"]
        series_url = f"{base_url}/{series_link}"

        try:
            all_series_pages = []

            # --- Extract quality/language from first page ---
            page_title = search_result.get("text", "")
            parts = [item for item in page_title.split("|") if item]
            if len(parts) >= 2:
                first_quality, first_language = self._parse_series_attributes(parts[1])
            else:
                first_quality = "N/A"
                first_language = "N/A"

            all_series_pages.append(
                {
                    "quality": first_quality,
                    "language": first_language,
                    "page_path": series_link,
                    "type": self.PAGE_TYPES.SEASON,
                }
            )

            # --- Get other available seasons pages ---
            response = await http_client.get(series_url)
            if response.status_code == 200:
                parser = HTMLParser(response.text)

                buttons = parser.css(
                    'ul.wa-post-list-ofLinks a[href^="?p=serie&id="]'
                )
                for button_node in buttons:
                    button_text = button_node.text(strip=True)
                    button_link = button_node.attributes.get("href", "")
                    quality = "N/A"
                    language = "N/A"
                    type = ""
                    if "saison" in button_text.lower():
                        # Season button
                        if "(" in button_text and ")" in button_text:
                            quality = button_text.split("(")[-1].replace(")", "")  # TODO: Remove ?
                        type = self.PAGE_TYPES.SEASON
                    else:
                        # Quality button
                        quality, language = self._parse_series_attributes(button_text)
                        type = self.PAGE_TYPES.QUALITY
                    all_series_pages.append(
                        {
                            "quality": quality,
                            "language": language,
                            "page_path": button_link,
                            "type": type,
                        }
                    )

            additional_pages = []
            # Exclude current starting page
            pages_to_explore = [
                p for p in all_series_pages
                if p["type"] is self.PAGE_TYPES.SEASON and p["page_path"] != series_link
            ]

            for page in pages_to_explore:
                season_url = f"{base_url}/{page['page_path']}"
                response = await http_client.get(season_url)
                if response.status_code != 200:
                    continue

                parser = HTMLParser(response.text)

                title_nodes = parser.css(self.SEARCH_LINK_SELECTOR)
                if title_nodes:
                    assert len(title_nodes) == 1
                    page["quality"], page["language"] = self._parse_series_attributes(
                        title_nodes[0].text(strip=True).split("-")[-1]
                    )

                # Search for quality links for this specific season page
                buttons = parser.css(
                    'ul.wa-post-list-ofLinks a[href^="?p=serie&id="]'
                )
                for button_node in buttons:
                    if not button_node:
                        continue

                    button_text = button_node.text(strip=True)
                    if "saison" in button_text.lower():
                        continue

                    quality_page_link = button_node.attributes.get("href", "")
                    if not quality_page_link:
                        continue

                    if quality_page_link and any(
                        p["page_path"] == quality_page_link for p in all_series_pages
                    ) or any(
                        p["page_path"] == quality_page_link for p in additional_pages
                    ):
                        # Security to avoid duplicates
                        continue

                    quality, language = self._parse_series_attributes(button_text)

                    additional_pages.append(
                        {
                            "quality": quality,
                            "language": language,
                            "page_path": quality_page_link,
                            "type": self.PAGE_TYPES.QUALITY,
                        }
                    )
            # Extend original list with discovered pages
            all_series_pages.extend(additional_pages)

            # --- Process each page in parallel ---
            page_tasks = []
            for series_page in all_series_pages:
                page_tasks.append(
                    self._extract_episodes_from_page(series_page, base_url)
                )

            page_results = await asyncio.gather(*page_tasks, return_exceptions=True)

            # --- Merge all results ---
            for result in page_results:
                if isinstance(result, list):
                    all_results.extend(result)

        except Exception as e:
            logger.error(f"Failed to extract all episodes: {e}")

        return all_results

    # --- Extract episodes from single page ---
    async def _extract_episodes_from_page(
        self, series_page: Dict, base_url: str
    ) -> List[Dict]:
        page_results = []
        page_path = series_page.get("page_path", "")
        default_quality = series_page.get("quality", "N/A")
        default_language = series_page.get("language", "N/A")

        if not page_path:
            return page_results

        series_page_url = f"{base_url}/{page_path}"

        try:
            response = await http_client.get(series_page_url)
            if response.status_code != 200:
                return page_results

            parser = HTMLParser(response.text)

            if default_quality == default_language == "N/A":
                title_nodes = parser.css(self.SEARCH_LINK_SELECTOR)
                assert len(title_nodes) == 1
                default_quality, default_language = self._parse_series_attributes(
                    title_nodes[0].text(strip=True).split("-")[-1]
                )

            # --- Get all rows from DDLLinks table ---
            link_rows = parser.css("#DDLLinks tr")
            if not link_rows:
                logger.log("SCRAPER", f"No download links for page: {page_path}")
                return page_results

            current_episode = None
            current_season = "1"
            current_page_language = default_language
            current_page_quality = default_quality

            for row in link_rows:
                # --- Check if episode title row ---
                row_class = str(row.attributes.get("class", ""))

                if "episode-title" in row_class:
                    episode_text = row.text(strip=True)

                    season_match = re_search(r"Saison (\d+)", episode_text)
                    if season_match:
                        current_season = season_match.group(1)

                    episode_match = re_search(r"Épisode (\d+)", episode_text)
                    if episode_match:
                        current_episode = episode_match.group(1)
                    else:
                        current_episode = None
                        continue

                # --- Check if download link row ---
                elif current_episode is not None:
                    link_node = row.css_first('a[href*="dl-protect."].link')
                    if link_node:
                        # --- Extract link information ---
                        hoster_cell = row.css_first('td[width="120px"].text-center')
                        hoster_name = hoster_cell.text().strip() if hoster_cell else ""

                        # --- Filter supported hosters ---
                        if hoster_name.lower() not in self.ALLOWED_HOSTERS.keys():
                            continue

                        size_td = row.css_first('td[width="80px"].text-center')
                        file_size = size_td.text().strip() if size_td else "?"

                        url = self.extract_link_from_node(link_node)
                        if not url:
                            continue

                        url = format_url(url, base_url)

                        # --- Extract filename ---
                        link_text = link_node.text(strip=True) if link_node else ""
                        decoded_fn = extract_filename_from_link(url, link_text)

                        # --- Validation ---
                        if (
                            not current_season
                            or not current_episode
                            or not decoded_fn.strip()
                        ):
                            logger.error(
                                f"Invalid metadata: S{current_season}E{current_episode}, file: {decoded_fn}"
                            )
                            continue

                        page_results.append(
                            {
                                "season": current_season,
                                "episode": current_episode,
                                "label": f"S{current_season.zfill(2)}E{current_episode.zfill(2)} - {current_page_quality} - {current_page_language} ({hoster_name.title()})",
                                "language": current_page_language,
                                "quality": current_page_quality,
                                "hoster": hoster_name.title(),
                                "size": file_size,
                                "dl_protect": url,
                                "display_name": decoded_fn,
                            }
                        )

        except Exception as e:
            logger.error(f"Failed to extract episodes from page: {e}")

        return page_results


# --- Global instance ---
series_scraper = SeriesScraper()
