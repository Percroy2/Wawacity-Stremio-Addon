import asyncio
from wawacity.scrapers.bookys import BookysScraper


async def main():
    s = BookysScraper()
    url = "https://www6.bookys-ebooks.com"
    r = await s.search("Le chant de pasiphaé", bookys_url=url)
    print("count", len(r) if r else 0)
    if r:
        print(r[0].get("id"), (r[0].get("name") or "")[:60])


asyncio.run(main())
