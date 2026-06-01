from typing import Dict
from urllib.parse import unquote


def parse_catalog_extra(extra_path: str) -> Dict[str, str]:
    if not extra_path:
        return {}

    extra_path = extra_path.removesuffix(".json")
    params: Dict[str, str] = {}

    for part in extra_path.split("&"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[unquote(key)] = unquote(value)

    return params
