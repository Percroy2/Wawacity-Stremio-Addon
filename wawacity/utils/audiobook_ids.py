from typing import Optional, Tuple


def wawacity_href_to_stremio_id(href: str) -> Optional[str]:
    if not href:
        return None

    raw = href
    if raw.startswith("?p=ebook&id="):
        raw = raw.split("=", 1)[1]

    raw = raw.strip()
    if not raw:
        return None

    return f"wa:ebook:{raw}"


def parse_audiobook_content_id(content_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    content_id = content_id.replace(".json", "")

    if not content_id.startswith("wa:ebook:"):
        return None, None, None

    remainder = content_id[len("wa:ebook:") :]
    parts = remainder.rsplit(":", 2)

    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return parts[0], parts[1], parts[2]

    return remainder, None, None


def wawacity_page_path(ebook_id: str) -> str:
    return f"?p=ebook&id={ebook_id}"
