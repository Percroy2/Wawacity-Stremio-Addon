import re
from typing import Optional, Tuple

BOOK_PATH_RE = re.compile(r"^(\d+)-")


def bookys_href_to_stremio_id(href: str) -> Optional[str]:
    if not href:
        return None

    raw = href.strip()
    marker = "/livres/"
    if marker in raw:
        raw = raw.split(marker, 1)[1]
    raw = raw.split("?")[0].split("#")[0].strip("/")
    if not raw or not BOOK_PATH_RE.match(raw):
        return None

    return f"bk:ebook:{raw}"


def parse_bookys_content_id(content_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    content_id = content_id.replace(".json", "")

    if not content_id.startswith("bk:ebook:"):
        return None, None, None

    remainder = content_id[len("bk:ebook:") :]
    parts = remainder.rsplit(":", 2)

    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return parts[0], parts[1], parts[2]

    return remainder, None, None


def bookys_book_path(book_path: str) -> str:
    return book_path.strip().strip("/")
