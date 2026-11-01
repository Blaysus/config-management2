"""Самостоятельный разбор текстового индекса Alpine APK v2."""

from dataclasses import dataclass
import re

from .config import PACKAGE_PATTERN, VERSION_PATTERN


FIELD_NAME_LENGTH = 1
TRACKED_FIELDS = {"P", "V", "D", "p", "k"}
DEFAULT_PROVIDER_PRIORITY = 0
MAX_PRIORITY_DIGITS = 10


class RepositoryError(ValueError):
    """Ошибка получения индекса, его формата или поиска пакета."""


@dataclass(frozen=True)
class Package:
    """Пакет с исходными требованиями зависимостей и конфликтами APK."""

    name: str
    version: str
    dependencies: tuple[str, ...]
    conflicts: tuple[str, ...]
    provides: tuple[str, ...] = ()
    provider_priority: int = DEFAULT_PROVIDER_PRIORITY


def _parse_fields(record: str) -> dict[str, str]:
    """Прочитать P, V, D, p и k, сохраняя двоеточия внутри значений."""
    fields = {}
    for line in record.splitlines():
        key, separator, value = line.partition(":")
        if len(key) != FIELD_NAME_LENGTH or not separator:
            raise RepositoryError(f"APKINDEX: некорректная строка {line!r}")
        if key in TRACKED_FIELDS:
            if key in fields:
                raise RepositoryError(f"APKINDEX: повторное поле {key}")
            fields[key] = value.strip()
    return fields


def _provider_priority(value: str) -> int:
    """Проверить неотрицательный целый приоритет поставщика из поля k."""
    if len(value) > MAX_PRIORITY_DIGITS or not re.fullmatch(r"[0-9]+", value):
        raise RepositoryError("APKINDEX: некорректный приоритет k")
    return int(value)


def _make_package(fields: dict[str, str]) -> Package:
    """Проверить имя и версию; отделить конфликты от зависимостей."""
    name = fields.get("P", "")
    version = fields.get("V", "")
    if not re.fullmatch(PACKAGE_PATTERN, name):
        raise RepositoryError("APKINDEX: отсутствует или некорректно поле P")
    if not re.fullmatch(VERSION_PATTERN, version):
        raise RepositoryError("APKINDEX: отсутствует или некорректно поле V")
    terms = fields.get("D", "").split()
    if "!" in terms:
        raise RepositoryError("APKINDEX: пустое имя конфликта в поле D")
    dependencies = tuple(term for term in terms if not term.startswith("!"))
    conflicts = tuple(term for term in terms if term.startswith("!"))
    provides = tuple(fields.get("p", "").split())
    priority = _provider_priority(fields.get("k", "0"))
    return Package(name, version, dependencies, conflicts, provides, priority)


def parse_index(text: str) -> dict[tuple[str, str], Package]:
    """Разобрать записи, разделенные пустыми строками, включая CRLF."""
    packages = {}
    normalized = text.replace("\r\n", "\n")
    for record in re.split(r"\n(?:[ \t]*\n)+", normalized.strip()):
        if not record.strip():
            continue
        package = _make_package(_parse_fields(record))
        key = (package.name, package.version)
        if key in packages:
            raise RepositoryError(
                f"APKINDEX: повторный пакет {package.name} {package.version}"
            )
        packages[key] = package
    if not packages:
        raise RepositoryError("APKINDEX: индекс не содержит пакетов")
    return packages


def find_package(
    packages: dict[tuple[str, str], Package], name: str, version: str
) -> Package:
    """Найти точную версию без подмены последней доступной версией."""
    key = (name, version)
    if key in packages:
        return packages[key]
    versions = sorted(ver for pkg, ver in packages if pkg == name)
    if not versions:
        raise RepositoryError(f"Пакет {name!r} не найден в репозитории")
    available = ", ".join(versions)
    raise RepositoryError(
        f"Версия {version!r} пакета {name!r} не найдена. "
        f"Доступные версии: {available}"
    )
