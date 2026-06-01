import io
import os
import re
import zipfile
from typing import List

import httpx
import rarfile

rarfile.UNRAR_TOOL = "unrar-free"

ARCHIVE_EXTENSIONS = (".rar", ".zip", ".7z")
AUDIO_EXTENSIONS = (".mp3", ".m4b", ".m4a", ".opus", ".flac", ".aac", ".wav", ".ogg")


def is_archive_filename(filename: str) -> bool:
    lower = (filename or "").lower()
    return any(lower.endswith(ext) for ext in ARCHIVE_EXTENSIONS)


def is_audio_filename(filename: str) -> bool:
    lower = (filename or "").lower()
    return any(lower.endswith(ext) for ext in AUDIO_EXTENSIONS)


def _natural_sort_key(filename: str) -> List:
    parts = re.split(r"(\d+)", (filename or "").lower())
    return [int(part) if part.isdigit() else part for part in parts]


class HttpRangeReader:
    """Seekable HTTP reader using Range requests (for archive listing)."""

    def __init__(self, url: str, timeout: float = 120.0):
        self.url = url
        self._pos = 0
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=4),
        )
        response = self._client.get(url, headers={"Range": "bytes=0-0"})
        if response.status_code not in (200, 206):
            response.raise_for_status()

        content_range = response.headers.get("content-range", "")
        if "/" in content_range:
            self._size = int(content_range.rsplit("/", 1)[1])
        else:
            self._size = int(response.headers.get("content-length", 0))

    def read(self, size: int = -1) -> bytes:
        if self._pos >= self._size:
            return b""

        if size < 0:
            size = self._size - self._pos

        end = min(self._pos + size - 1, self._size - 1)
        response = self._client.get(
            self.url,
            headers={"Range": f"bytes={self._pos}-{end}"},
        )
        response.raise_for_status()
        data = response.content
        self._pos += len(data)
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self._size + offset
        self._pos = max(0, min(self._pos, self._size))
        return self._pos

    def tell(self) -> int:
        return self._pos

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def list_audio_files_in_archive(direct_url: str, archive_name: str) -> List[str]:
    lower_name = (archive_name or "").lower()

    if lower_name.endswith(".zip"):
        return _list_zip_audio(direct_url)

    if lower_name.endswith(".rar"):
        return _list_rar_audio(direct_url)

    return []


def _list_zip_audio(direct_url: str) -> List[str]:
    with HttpRangeReader(direct_url) as reader:
        with zipfile.ZipFile(reader) as archive:
            names = [
                info.filename
                for info in archive.infolist()
                if not info.is_dir() and is_audio_filename(info.filename)
            ]
    names.sort(key=_natural_sort_key)
    return names


def _list_rar_audio(direct_url: str) -> List[str]:
    with HttpRangeReader(direct_url) as reader:
        with rarfile.RarFile(reader) as archive:
            names = [
                info.filename
                for info in archive.infolist()
                if not info.is_dir() and is_audio_filename(info.filename)
            ]
    names.sort(key=_natural_sort_key)
    return names


def extract_rar_audio_to_path(
    direct_url: str,
    member_name: str,
    destination: str,
) -> None:
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with HttpRangeReader(direct_url) as reader:
        with rarfile.RarFile(reader) as archive:
            archive.extract(member_name, path=os.path.dirname(destination))
            extracted = os.path.join(os.path.dirname(destination), member_name)
            if os.path.dirname(member_name):
                extracted = os.path.join(os.path.dirname(destination), member_name)
            if extracted != destination:
                if os.path.exists(destination):
                    os.remove(destination)
                os.replace(extracted, destination)


def extract_zip_audio_to_path(
    direct_url: str,
    member_name: str,
    destination: str,
) -> None:
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with HttpRangeReader(direct_url) as reader:
        with zipfile.ZipFile(reader) as archive:
            with archive.open(member_name) as source, open(destination, "wb") as target:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
