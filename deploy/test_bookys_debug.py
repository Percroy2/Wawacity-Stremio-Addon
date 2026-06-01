import asyncio
from wawacity.utils.flaresolverr import fetch_html

URL = "https://www6.bookys-ebooks.com/search?cat=75&q=Le+chant+de+pasipha%C3%A9"


async def main():
    html = await fetch_html(URL)
    if not html:
        print("no html")
        return
    print("len", len(html))
    from selectolax.parser import HTMLParser
    from wawacity.utils.bookys_ids import bookys_href_to_stremio_id

    parser = HTMLParser(html)
    for needle in ("bys-item", "bys-link", "pasipha", "251544"):
        print(needle, needle in html)
    links = parser.css('a[href*="/livres/"]')
    print("livres links", len(links))
    for link in links:
        href = link.attributes.get("href", "")
        sid = bookys_href_to_stremio_id(href)
        if sid or "251544" in href:
            print(href[:100], "->", sid, "|", link.text(strip=True)[:60])
    from wawacity.scrapers.bookys import BookysScraper
    s = BookysScraper()
    metas = s._parse_search_results_table(parser, "https://www6.bookys-ebooks.com")
    print("parsed metas", len(metas))
    if metas:
        print(metas[0])
    idx = html.lower().find("pasipha")
    if idx >= 0:
        print(html[max(0, idx - 200) : idx + 400])


asyncio.run(main())
