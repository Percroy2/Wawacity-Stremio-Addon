import json
import os
import re
from fastapi import APIRouter, Request, Query, Path
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse, HTMLResponse, Response
from typing import Optional

from wawacity.core.config import ADDON_MANIFEST, WAWACITY_URL, PROXY_URL, CUSTOM_HTML, ADDON_PASSWORD, MEDIAFLOW_URL, MEDIAFLOW_PASSWORD
from wawacity.core.categories import build_manifest
from wawacity.utils.validators import validate_config, decode_config, normalize_wawacity_url
from wawacity.services.stream import stream_service
from wawacity.services.catalog import catalog_service
from wawacity.services.mediaflow import mediaflow_service, is_mediaflow_enabled, get_mediaflow_settings
from wawacity.utils.stremio_extra import parse_catalog_extra
from wawacity.utils.poster import decode_poster_source
from wawacity.services.alldebrid import alldebrid_service
from wawacity.scrapers.movie import movie_scraper
from wawacity.scrapers.series import series_scraper
from wawacity.scrapers.audiobook import audiobook_scraper
from wawacity.utils.logger import logger

router = APIRouter()


def _addon_base_url(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _render_configure_html(initial_config: Optional[dict] = None) -> str:
    with open("wawacity/public/index.html", "r", encoding="utf-8") as f:
        html_content = f.read()

    html_content = html_content.replace("{{CUSTOM_HTML}}", CUSTOM_HTML)
    html_content = html_content.replace("{{DEFAULT_WAWACITY_URL}}", WAWACITY_URL)
    html_content = html_content.replace("{{DEFAULT_MEDIAFLOW_URL}}", MEDIAFLOW_URL)
    config_json = json.dumps(initial_config) if initial_config else "null"
    html_content = html_content.replace("{{INITIAL_CONFIG}}", config_json)

    return html_content


# --- Main routes ---
@router.get("/", summary="Accueil", description="Redirection automatique vers la page de configuration")
async def root():
    return RedirectResponse("/configure")

@router.get(
    "/configure",
    summary="Configuration",
    description="Interface web pour configurer AllDebrid, TMDB et l'URL Wawacity",
)
async def configure():
    return HTMLResponse(content=_render_configure_html())

@router.get(
    "/{b64config}/configure",
    summary="Reconfigurer",
    description="Modifier la configuration existante",
)
async def configure_addon(
    b64config: str = Path(
        ..., description="Configuration encodée (base64) avec clés API et URL Wawacity"
    ),
):
    return HTMLResponse(content=_render_configure_html(decode_config(b64config)))

# --- Manifest route ---
@router.get("/{b64config}/manifest.json", summary="Manifest Stremio", description="Informations de l'addon pour l'installation dans Stremio")
async def get_manifest(
    b64config: str = Path(..., description="Configuration encodée (base64) avec clés API AllDebrid/TMDB")
):
    config = validate_config(b64config)
    if not config:
        return JSONResponse(content=ADDON_MANIFEST)

    return JSONResponse(content=build_manifest(config, ADDON_MANIFEST))

# --- Catalog routes ---
@router.get(
    "/{b64config}/catalog/{content_type}/{catalog_id}",
    summary="Catalogue Stremio",
    description="Liste les livres audio disponibles sur Wawacity",
)
async def get_catalog(
    request: Request,
    b64config: str = Path(..., description="Configuration encodée (base64)"),
    content_type: str = Path(..., description="Type Stremio (series pour Livres)"),
    catalog_id: str = Path(..., description="ID du catalogue (wawacity_livres)"),
):
    config = validate_config(b64config)
    if not config:
        return JSONResponse(content={"metas": [], "cacheMaxAge": 60})

    catalog_id_clean = catalog_id.replace(".json", "")
    extra = parse_catalog_extra("")

    return JSONResponse(
        content=await catalog_service.get_catalog(
            config, catalog_id_clean, extra, _addon_base_url(request)
        )
    )


@router.get(
    "/{b64config}/catalog/{content_type}/{catalog_id}/{extra_path:path}",
    summary="Catalogue Stremio (filtres)",
    description="Catalogue avec recherche, genre ou pagination",
)
async def get_catalog_with_extra(
    request: Request,
    b64config: str = Path(..., description="Configuration encodée (base64)"),
    content_type: str = Path(..., description="Type Stremio (series pour Livres)"),
    catalog_id: str = Path(..., description="ID du catalogue (wawacity_livres)"),
    extra_path: str = Path(..., description="Paramètres extra Stremio (search, genre, skip)"),
):
    config = validate_config(b64config)
    if not config:
        return JSONResponse(content={"metas": [], "cacheMaxAge": 60})

    catalog_id_clean = catalog_id.replace(".json", "")
    extra = parse_catalog_extra(extra_path)

    return JSONResponse(
        content=await catalog_service.get_catalog(
            config, catalog_id_clean, extra, _addon_base_url(request)
        )
    )


@router.get("/poster/{token}", summary="Proxy affiche", include_in_schema=False)
async def proxy_poster(token: str = Path(..., description="URL source encodée")):
    source_url = decode_poster_source(token)
    if not source_url:
        return Response(status_code=400)

    from wawacity.utils.http_client import http_client

    try:
        response = await http_client.get(source_url, timeout=15.0)
    except Exception:
        return Response(status_code=502)

    if response.status_code != 200:
        return Response(status_code=502)

    content_type = response.headers.get("content-type", "image/webp")
    return Response(
        content=response.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# --- Meta routes ---
@router.get(
    "/{b64config}/meta/{content_type}/{meta_id}",
    summary="Métadonnées Stremio",
    description="Fiche détaillée d'un livre audio",
)
async def get_meta(
    request: Request,
    b64config: str = Path(..., description="Configuration encodée (base64)"),
    content_type: str = Path(..., description="Type Stremio"),
    meta_id: str = Path(..., description="ID du contenu (wa:ebook:...)"),
):
    config = validate_config(b64config)
    if not config:
        return JSONResponse(content={"meta": {}})

    return JSONResponse(
        content=await catalog_service.get_meta(
            config, content_type, meta_id, _addon_base_url(request)
        )
    )

# --- Streaming routes ---
@router.get("/{b64config}/stream/{content_type}/{content_id}", 
           summary="Rechercher des streams", 
           description="Trouve et retourne les liens de streaming pour un film ou une série depuis Wawacity")
async def get_streams(
    request: Request,
    b64config: str = Path(..., description="Configuration encodée (base64) avec clés API AllDebrid/TMDB"),
    content_type: str = Path(..., description="Type de contenu: 'movie' ou 'series'"),
    content_id: str = Path(..., description="ID IMDB (films) ou IMDB:saison:episode (séries)")
):
    config = validate_config(b64config)
    if not config:
        logger.error("Invalid configuration - Check format or missing/empty keys")
        return JSONResponse(content={"streams": []})
    
    content_id_formatted = content_id.replace(".json", "")
    logger.log("API", f"Stream request: {content_type}/{content_id_formatted}")
    
    try:
        base_url = str(request.base_url).rstrip('/')
        
        streams = await stream_service.get_streams(
            content_type=content_type,
            content_id=content_id_formatted,
            config=config,
            base_url=base_url
        )
        
        return JSONResponse(content={
            "streams": streams,
            "cacheMaxAge": 1
        })
        
    except Exception as e:
        logger.error(f"Stream request failed: {e}")
        return JSONResponse(content={"streams": []})

@router.get("/cached-audio/{cache_id}", summary="Chapitre audio extrait", include_in_schema=False)
async def serve_cached_audio(cache_id: str = Path(...)):
    from wawacity.services.alldebrid import AUDIO_CACHE_DIR

    if not re.fullmatch(r"[a-f0-9]{32}", cache_id):
        return Response(status_code=404)

    cache_path = os.path.join(AUDIO_CACHE_DIR, f"{cache_id}.mp3")
    if not os.path.isfile(cache_path):
        return Response(status_code=404)

    return FileResponse(
        cache_path,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# --- AllDebrid resolution route ---
@router.get("/resolve", 
           summary="Résoudre un lien", 
           description="Convertit un lien dl-protect en lien direct via AllDebrid pour le streaming")
async def resolve(
    request: Request,
    link: str = Query(..., description="Lien dl-protect à convertir (ex: https://dl-protect.link/abc123)"),
    b64config: str = Query(..., description="Configuration encodée contenant votre clé API AllDebrid"),
    episode: Optional[int] = Query(
        None,
        description="Numéro de chapitre (épisode) pour les archives multi-fichiers",
    ),
):
    config = validate_config(b64config)
    if not config:
        return FileResponse("wawacity/public/error.mkv")
    
    apikey = config.get("alldebrid", "")
    if not apikey:
        return FileResponse("wawacity/public/error.mkv")

    file_index = max(0, episode - 1) if episode and episode > 0 else 0
    direct_link = await stream_service.resolve_link(
        link,
        apikey,
        config,
        file_index=file_index,
        serve_base_url=_addon_base_url(request),
    )
    
    if direct_link and direct_link != "LINK_DOWN":
        playback_url = mediaflow_service.wrap_playback_url(direct_link, config)
        return RedirectResponse(url=playback_url or direct_link, status_code=302)
    elif direct_link == "LINK_DOWN":
        return FileResponse("wawacity/public/link_down_error.mkv")
    else:
        return FileResponse("wawacity/public/error.mkv")

# --- Debug routes ---
@router.get("/debug/test-search", 
           summary="Test de recherche", 
           description="Teste la recherche Wawacity directement")
async def debug_search(
    title: str = Query(..., description="Titre du film ou série à rechercher"),
    year: Optional[str] = Query(None, description="Année de sortie (optionnel)"),
    type: str = Query("film", description="Type de contenu: 'film', 'serie' ou 'audiobook'"),
    wawacity_url: Optional[str] = Query(
        None, description="URL Wawacity (sinon valeur WAWACITY_URL du serveur)"
    ),
):
    try:
        base_url = normalize_wawacity_url(wawacity_url) if wawacity_url else WAWACITY_URL

        if type == "audiobook":
            results = await audiobook_scraper.search(title, year, base_url)
        elif type == "serie":
            results = await series_scraper.search(title, year, base_url)
        else:
            results = await movie_scraper.search(title, year, base_url)

        return {
            "title": title,
            "year": year,
            "type": type,
            "wawacity_url": base_url,
            "count": len(results),
            "results": results,
        }
    except Exception as e:
        return {
            "error": str(e),
            "title": title,
            "year": year,
            "type": type
        }

@router.get("/debug/test-alldebrid", 
           summary="Test AllDebrid", 
           description="Teste la conversion d'un lien dl-protect via votre clé AllDebrid")
async def debug_alldebrid(
    link: str = Query(..., description="Lien dl-protect à convertir (ex: https://dl-protect.link/abc123)"),
    apikey: str = Query(..., description="Clé API AllDebrid")
):
    try:
        result = await alldebrid_service.convert_link(link, apikey)
        return {
            "input_link": link,
            "alldebrid_link": result,
            "status": "success" if result and result != "LINK_DOWN" else "failed"
        }
    except Exception as e:
        return {
            "input_link": link,
            "error": str(e),
            "status": "error"
        }

# --- Health check ---
@router.get("/health", 
           summary="État de santé", 
           description="Teste l'état du serveur, de Wawacity, de la base de données et du proxy")
async def health_check(
    wawacity_url: Optional[str] = Query(
        None, description="URL Wawacity à tester (sinon WAWACITY_URL du serveur)"
    ),
):
    import time
    from wawacity.utils.http_client import http_client
    from wawacity.utils.database import database

    target_wawacity_url = (
        normalize_wawacity_url(wawacity_url) if wawacity_url else WAWACITY_URL
    )

    start_time = time.time()
    health_status = {
        "status": "healthy",
        "version": ADDON_MANIFEST["version"],
        "timestamp": int(time.time()),
        "wawacity_url": target_wawacity_url,
        "checks": {},
    }
    
    # --- Server test ---
    health_status["checks"]["server"] = {
        "status": "ok",
        "message": "Addon server running"
    }
    
    # --- Database test ---
    try:
        await database.fetch_val("SELECT 1")
        health_status["checks"]["database"] = {
            "status": "ok",
            "message": "Database connection active"
        }
    except Exception as e:
        health_status["checks"]["database"] = {
            "status": "error",
            "message": f"Database error: {str(e)}"
        }
        health_status["status"] = "degraded"
    
    # --- Wawacity test ---
    wawacity_start = time.time()
    try:
        response = await http_client.get(target_wawacity_url, timeout=5)
        wawacity_time = round((time.time() - wawacity_start) * 1000)
        
        if response.status_code == 200:
            health_status["checks"]["wawacity"] = {
                "status": "ok",
                "message": "Wawacity accessible",
                "response_time_ms": wawacity_time
            }
        else:
            health_status["checks"]["wawacity"] = {
                "status": "error",
                "message": f"Wawacity HTTP {response.status_code}",
                "response_time_ms": wawacity_time
            }
            health_status["status"] = "degraded"
            
    except Exception as e:
        wawacity_time = round((time.time() - wawacity_start) * 1000)
        health_status["checks"]["wawacity"] = {
            "status": "error",
            "message": f"Wawacity unreachable: {str(e)}",
            "response_time_ms": wawacity_time
        }
        health_status["status"] = "unhealthy"
    
    # --- Proxy test ---
    if PROXY_URL:
        try:
            test_response = await http_client.get("https://httpbin.org/ip", timeout=5)
            if test_response.status_code == 200:
                health_status["checks"]["proxy"] = {
                    "status": "ok",
                    "message": "Proxy functional"
                }
            else:
                health_status["checks"]["proxy"] = {
                    "status": "error",
                    "message": "Proxy not responding"
                }
                health_status["status"] = "degraded"
        except Exception as e:
            health_status["checks"]["proxy"] = {
                "status": "error",
                "message": f"Proxy error: {str(e)}"
            }
            health_status["status"] = "degraded"
    else:
        health_status["checks"]["proxy"] = {
            "status": "disabled",
            "message": "No proxy configured"
        }

    # --- MediaFlow test ---
    public_url, internal_url, mf_password = get_mediaflow_settings({})
    if public_url and mf_password:
        mf_start = time.time()
        try:
            ip = await mediaflow_service.get_public_ip(internal_url, mf_password)
            mf_time = round((time.time() - mf_start) * 1000)
            if ip:
                health_status["checks"]["mediaflow"] = {
                    "status": "ok",
                    "message": f"MediaFlow reachable (IP: {ip})",
                    "response_time_ms": mf_time,
                    "public_url": public_url,
                }
            else:
                health_status["checks"]["mediaflow"] = {
                    "status": "error",
                    "message": "MediaFlow unreachable or invalid password",
                    "response_time_ms": mf_time,
                }
                health_status["status"] = "degraded"
        except Exception as e:
            mf_time = round((time.time() - mf_start) * 1000)
            health_status["checks"]["mediaflow"] = {
                "status": "error",
                "message": f"MediaFlow error: {str(e)}",
                "response_time_ms": mf_time,
            }
            health_status["status"] = "degraded"
    else:
        health_status["checks"]["mediaflow"] = {
            "status": "disabled",
            "message": "MediaFlow not configured",
        }
    
    # --- Final response ---
    total_time = round((time.time() - start_time) * 1000)
    health_status["total_response_time_ms"] = total_time
    
    return health_status

# --- Password configuration route ---
@router.get("/password-config", 
           summary="Configuration mot de passe", 
           description="Retourne si un mot de passe est requis pour la configuration")
async def get_password_config():
    return JSONResponse(content={
        "password_required": bool(ADDON_PASSWORD.strip())
    })

# --- Password verification route ---
@router.post("/verify-password", 
            summary="Vérification mot de passe", 
            description="Vérifie si le mot de passe fourni est valide")
async def verify_password(password: str = Query(..., description="Mot de passe à vérifier")):
    if not ADDON_PASSWORD.strip():
        return JSONResponse(content={"valid": True})
    
    valid_passwords = [pwd.strip() for pwd in ADDON_PASSWORD.split(",") if pwd.strip()]
    is_valid = password in valid_passwords
    
    return JSONResponse(content={"valid": is_valid})