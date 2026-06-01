from typing import Dict, List, Any

CATEGORY_DEFINITIONS: Dict[str, Dict[str, str]] = {
    "movie": {
        "label": "Films",
        "stremio_type": "movie",
        "description": "Films (Wawacity ?p=films)",
    },
    "series": {
        "label": "Séries",
        "stremio_type": "series",
        "description": "Séries TV (Wawacity ?p=series)",
    },
    "audiobook": {
        "label": "Audiobooks",
        "stremio_type": "series",
        "description": "Livres audio (Wawacity ?p=ebooks&s=audiobooks)",
    },
}

DEFAULT_ENABLED_CATEGORIES: List[str] = list(CATEGORY_DEFINITIONS.keys())


def normalize_enabled_categories(categories: Any) -> List[str]:
    if not isinstance(categories, list):
        return list(DEFAULT_ENABLED_CATEGORIES)

    normalized = []
    for item in categories:
        if isinstance(item, str) and item in CATEGORY_DEFINITIONS and item not in normalized:
            normalized.append(item)

    return normalized or list(DEFAULT_ENABLED_CATEGORIES)


def get_enabled_categories(config: Dict[str, Any]) -> List[str]:
    return normalize_enabled_categories(config.get("enabled_categories"))


def get_enabled_stremio_types(config: Dict[str, Any]) -> List[str]:
    types: List[str] = []
    for key in get_enabled_categories(config):
        stremio_type = CATEGORY_DEFINITIONS[key]["stremio_type"]
        if stremio_type not in types:
            types.append(stremio_type)
    return types


def is_category_enabled(config: Dict[str, Any], category: str) -> bool:
    return category in get_enabled_categories(config)


def build_manifest(config: Dict[str, Any], base_manifest: Dict[str, Any]) -> Dict[str, Any]:
    manifest = dict(base_manifest)
    enabled = get_enabled_categories(config)
    manifest["types"] = get_enabled_stremio_types(config)

    prefixes = ["tt"]
    if "audiobook" in enabled:
        prefixes.extend(["wa", "ol"])
    manifest["idPrefixes"] = prefixes

    manifest["behaviorHints"] = {
        **(base_manifest.get("behaviorHints") or {}),
        "configurable": True,
    }
    return manifest
