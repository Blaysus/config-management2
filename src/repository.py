"""Загрузка APKINDEX через HTTP(S) или из локального текстового файла."""

import gzip
from http.client import HTTPException
from io import BytesIO
from pathlib import Path
import tarfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
import zlib

from .apk_index import Package, RepositoryError, find_package, parse_index
from .config import Config


INDEX_FILENAME = "APKINDEX.tar.gz"
REQUEST_TIMEOUT = 20
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_INDEX_BYTES = 32 * 1024 * 1024
SINGLE_INDEX_COUNT = 1
USER_AGENT = "config-management2/0.2"


def build_index_url(repository: str) -> str:
    """Дополнить URL каталога именем индекса, сохраняя query-параметры."""
    parts = urlsplit(repository)
    path = parts.path.rstrip("/")
    if path.rsplit("/", 1)[-1] != INDEX_FILENAME:
        path += "/" + INDEX_FILENAME
    return urlunsplit(parts._replace(path=path))


def _read_limited(source, limit: int) -> bytes:
    """Прочитать поток с ограничением объема и дополнительным байтом."""
    data = source.read(limit + 1)
    if len(data) > limit:
        raise RepositoryError(f"Индекс превышает лимит {limit} байт")
    return data


def download_index(repository: str) -> bytes:
    """Скачать индекс с таймаутом и понятными сообщениями сетевых ошибок."""
    url = build_index_url(repository)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return _read_limited(response, MAX_DOWNLOAD_BYTES)
    except HTTPError as error:
        error.close()
        raise RepositoryError(
            f"Не удалось скачать индекс: HTTP {error.code} ({url})"
        ) from error
    except (URLError, OSError, HTTPException, ValueError) as error:
        raise RepositoryError(
            f"Не удалось скачать индекс {url}: {error}"
        ) from error


def _decode_index(data: bytes) -> str:
    """Декодировать текст индекса в UTF-8 без замены ошибочных символов."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepositoryError(
            "APKINDEX: некорректная кодировка UTF-8"
        ) from error


def _read_archive_member(archive: tarfile.TarFile) -> bytes:
    """Прочитать единственный обычный APKINDEX без извлечения на диск."""
    members = [
        member for member in archive
        if member.name in {"APKINDEX", "./APKINDEX"}
    ]
    if len(members) != SINGLE_INDEX_COUNT:
        raise RepositoryError("Архив должен содержать один файл APKINDEX")
    member = members[0]
    if not member.isfile() or member.size > MAX_INDEX_BYTES:
        raise RepositoryError("APKINDEX: неверный тип или размер файла")
    with archive.extractfile(member) as source:
        return _read_limited(source, MAX_INDEX_BYTES)


def extract_index(data: bytes) -> str:
    """Прочитать gzip и tar, включая подписанные составные архивы Alpine."""
    try:
        with gzip.GzipFile(fileobj=BytesIO(data)) as compressed:
            raw = _read_limited(compressed, MAX_ARCHIVE_BYTES)
        with tarfile.open(
            fileobj=BytesIO(raw), mode="r:", ignore_zeros=True
        ) as archive:
            return _decode_index(_read_archive_member(archive))
    except (tarfile.TarError, OSError, EOFError, zlib.error) as error:
        raise RepositoryError(
            f"Некорректный архив APKINDEX: {error}"
        ) from error


def read_test_index(repository: str, base_dir: Path) -> str:
    """Прочитать локальный текстовый APKINDEX относительно TOML-файла."""
    try:
        path = Path(repository).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        with path.open("rb") as source:
            return _decode_index(_read_limited(source, MAX_INDEX_BYTES))
    except (OSError, RuntimeError) as error:
        raise RepositoryError(
            f"Не удалось прочитать APKINDEX: {error}"
        ) from error


def load_packages(config: Config, base_dir: Path) -> dict:
    """Загрузить и разобрать индекс один раз для последующего обхода."""
    if config.test_mode:
        text = read_test_index(config.repository, base_dir)
    else:
        text = extract_index(download_index(config.repository))
    return parse_index(text)


def get_package(config: Config, base_dir: Path) -> Package:
    """Получить прямые зависимости ровно той версии, которая указана в TOML."""
    packages = load_packages(config, base_dir)
    return find_package(packages, config.package_name, config.package_version)
