import asyncio
from wawacity.scrapers.bookys import bookys_scraper

async def main():
    s = bookys_scraper
    url = "https://www6.bookys-ebooks.com"
    q = "Le chant de pasiphae"
    print("url", s._search_url(url, q))
    r = await s.list_catalog(url, search=q)
    print("count", len(r))
    if r:
        print(r[0]["id"], r[0]["name"][:60])

asyncio.run(main())
