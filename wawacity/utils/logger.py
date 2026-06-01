import sys
from loguru import logger

# --- Custom log levels ---
CUSTOM_LOG_LEVELS = {
    "STARTUP": {"no": 21, "icon": "🚀", "color": "<green>"},
    "SCRAPER": {"no": 22, "icon": "🌐", "color": "<blue>"},
    "API": {"no": 23, "icon": "🔗", "color": "<cyan>"},
    "STREAM": {"no": 24, "icon": "🎬", "color": "<green>"},
    "CACHE": {"no": 25, "icon": "💾", "color": "<white>"},
    "TMDB": {"no": 26, "icon": "🎭", "color": "<magenta>"},
    "ALLDEBRID": {"no": 27, "icon": "☁️", "color": "<blue>"},
    "MEDIAFLOW": {"no": 32, "icon": "🔀", "color": "<cyan>"},
    "DATABASE": {"no": 28, "icon": "🗄️", "color": "<yellow>"},
    "LOCK": {"no": 29, "icon": "🔒", "color": "<yellow>"},
    "CLEANUP": {"no": 30, "icon": "🧹", "color": "<white>"},
    "DEAD_LINK": {"no": 31, "icon": "💀", "color": "<red>"},
}

# --- Standard log levels ---
STANDARD_LOG_LEVELS = {
    "ERROR": {"icon": "❌", "color": "<red>"},
    "WARNING": {"icon": "⚠️", "color": "<yellow>"},
    "INFO": {"icon": "ℹ️", "color": "<blue>"},
    "DEBUG": {"icon": "🐛", "color": "<white>"},
    "SUCCESS": {"icon": "✅", "color": "<green>"},
}

def setup_logger(level: str = "INFO"):
    """Setup logger with custom formatting and levels"""
    
    # --- Remove default handler ---
    logger.remove()
    
    # --- Configure custom log levels ---
    for level_name, level_config in CUSTOM_LOG_LEVELS.items():
        logger.level(
            level_name,
            no=level_config["no"],
            icon=level_config["icon"],
            color=level_config["color"],
        )

    # --- Configure standard log levels ---
    for level_name, level_config in STANDARD_LOG_LEVELS.items():
        logger.level(
            level_name, 
            icon=level_config["icon"], 
            color=level_config["color"]
        )

    # --- Log format ---
    log_format = (
        "<white>{time:YYYY-MM-DD}</white> <magenta>{time:HH:mm:ss}</magenta> | "
        "<level>{level.icon} {level}</level> | "
        "<cyan>{module}</cyan>.<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    # --- Console handler ---
    logger.add(
        sys.stderr,
        level=level,
        format=log_format,
        backtrace=False,
        diagnose=False,
        enqueue=True,
        colorize=True
    )


# --- Initialize logger ---
setup_logger("INFO")

# --- Disable uvicorn logs completely ---
import logging

# --- Completely disable uvicorn access logs ---
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("uvicorn.access").propagate = False

# --- Reduce uvicorn error logs to critical only ---  
logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
logging.getLogger("uvicorn").setLevel(logging.CRITICAL)

# --- Disable FastAPI logs ---
logging.getLogger("fastapi").setLevel(logging.CRITICAL)