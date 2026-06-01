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

LIVRES_CATALOG_ID = "wawacity_livres"

AUDIOBOOK_GENRE_OPTIONS: List[str] = [
    "Roman",
    "Aventure",
    "Enfant",
    "Humour",
    "Théâtre",
    "Sport",
    "Actualité",
    "Automobile",
    "Bricolage",
    "Célébrités",
    "Cuisine",
    "Femme",
    "Informatique",
    "Soins/Beauté",
    "Autres",
]

AUDIOBOOK_GENRE_SLUGS: Dict[str, str] = {
    "Roman": "roman",
    "Aventure": "aventure",
    "Enfant": "enfant",
    "Humour": "humour",
    "Théâtre": "theatre",
    "Sport": "sport",
    "Actualité": "actualite",
    "Automobile": "automobile",
    "Bricolage": "bricolage",
    "Célébrités": "celebrites",
    "Cuisine": "cuisine",
    "Femme": "femme",
    "Informatique": "informatique",
    "Soins/Beauté": "soinsbeaute",
    "Autres": "autres",
}


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


def build_livres_catalog() -> Dict[str, Any]:
    return {
        "type": "series",
        "id": LIVRES_CATALOG_ID,
        "name": "Livres",
        "extra": [
            {"name": "search", "isRequired": False},
            {"name": "skip", "isRequired": False},
            {
                "name": "genre",
                "isRequired": False,
                "options": AUDIOBOOK_GENRE_OPTIONS,
            },
        ],
        "genres": AUDIOBOOK_GENRE_OPTIONS,
    }


def build_manifest(config: Dict[str, Any], base_manifest: Dict[str, Any]) -> Dict[str, Any]:
    manifest = dict(base_manifest)
    enabled = get_enabled_categories(config)
    manifest["types"] = get_enabled_stremio_types(config)

    prefixes: List[str] = []
    if "movie" in enabled or "series" in enabled:
        prefixes.append("tt")
    if "audiobook" in enabled:
        prefixes.extend(["wa", "ol"])
    manifest["idPrefixes"] = prefixes or ["tt"]

    if "audiobook" in enabled:
        manifest["resources"] = ["catalog", "meta", "stream"]
        manifest["catalogs"] = [build_livres_catalog()]
    else:
        manifest["resources"] = ["stream"]
        manifest["catalogs"] = []

    manifest["behaviorHints"] = {
        **(base_manifest.get("behaviorHints") or {}),
        "configurable": True,
    }
    return manifest
