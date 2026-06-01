"""Lecture des titres embarqués (ID3, atoms MP4, Vorbis) pour les chapitres audio."""

import io
import os
import re
from typing import Optional

from mutagen.flac import FLAC
from mutagen.id3 import ID3NoHeaderError
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

# ID3v2 est en tête de fichier ; 256 KiB suffisent pour la quasi-totalité des tags.
TAG_HEADER_BYTES = 262144


def format_chapter_title_from_filename(filename: str) -> str:
    name = os.path.splitext(filename or "")[0]
    name = name.replace("_", " ").replace(".", " ")
    return name.strip() or filename or "Chapitre"


def _normalize_title(value: str) -> str:
    return re.sub(r"[\s._-]+", "", (value or "").lower())


def merge_chapter_title(filename: str, tag_title: Optional[str]) -> str:
    """Combine préfixe numérique du fichier et titre ID3 quand les deux apportent de l'info."""
    file_label = format_chapter_title_from_filename(os.path.basename(filename))
    if not tag_title:
        return file_label

    tag = tag_title.strip()
    if not tag or tag.lower() == file_label.lower():
        return file_label

    if _normalize_title(tag) == _normalize_title(file_label):
        return file_label

    base = os.path.splitext(os.path.basename(filename))[0]
    prefix_match = re.match(r"^(\d{1,3})\s*[-._\s]+", base)
    if prefix_match:
        num = prefix_match.group(1)
        padded = num.zfill(2) if len(num) <= 2 else num
        tag_body = re.sub(
            rf"^(?:{re.escape(num)}|{re.escape(padded)})\s*[-._\s]*",
            "",
            tag,
            count=1,
        ).strip()
        if not tag_body:
            return file_label

        file_suffix = re.sub(
            rf"^{re.escape(padded)}\s*-\s*",
            "",
            file_label,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        if _normalize_title(tag_body) == _normalize_title(file_suffix):
            return file_label

        return f"{padded} - {tag_body}"

    if file_label.lower() in tag.lower():
        return tag

    return tag


def read_title_from_audio_bytes(data: bytes, filename: str) -> Optional[str]:
    if not data:
        return None

    ext = os.path.splitext(filename or "")[1].lower()
    try:
        if ext == ".mp3":
            return _read_mp3_title(data)
        if ext in (".m4b", ".m4a"):
            return _read_mp4_title(data)
        if ext == ".flac":
            return _read_flac_title(data)
    except Exception:
        return None
    return None


def _frame_text(frame) -> Optional[str]:
    if frame is None:
        return None
    if hasattr(frame, "text") and frame.text:
        return str(frame.text[0]).strip()
    return str(frame).strip() or None


def _read_mp3_title(data: bytes) -> Optional[str]:
    try:
        audio = MP3(io.BytesIO(data))
    except ID3NoHeaderError:
        return None
    except Exception:
        return None

    if not audio.tags:
        return None

    for key in ("TIT2", "TIT1"):
        title = _frame_text(audio.tags.get(key))
        if title:
            return title
    return None


def _read_mp4_title(data: bytes) -> Optional[str]:
    try:
        audio = MP4(io.BytesIO(data))
    except Exception:
        return None

    if not audio.tags:
        return None

    for key in ("\xa9nam", "©nam"):
        values = audio.tags.get(key)
        if values and str(values[0]).strip():
            return str(values[0]).strip()
    return None


def _read_flac_title(data: bytes) -> Optional[str]:
    try:
        audio = FLAC(io.BytesIO(data))
    except Exception:
        return None

    title = audio.get("title")
    if title and str(title[0]).strip():
        return str(title[0]).strip()
    return None
