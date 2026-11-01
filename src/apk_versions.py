"""Сравнение числовых частей, суффиксов и ревизий версий APK v2."""

import re

from .apk_index import RepositoryError


DOT_NUMBER, NUMBER, LETTER, SUFFIX, SUFFIX_NUMBER, REVISION, END = range(7)
LESS, EQUAL, GREATER = -1, 0, 1
MAX_NUMBER_DIGITS = 17
SUFFIX_RANK = {
    "alpha": -4, "beta": -3, "pre": -2, "rc": -1,
    "cvs": 0, "svn": 1, "git": 2, "hg": 3, "p": 4,
}
VERSION_FORMAT = re.compile(
    r"(\d+(?:\.\d+)*(?:[a-z]\d*)?)"
    r"((?:_(?:alpha|beta|pre|rc|cvs|svn|git|hg|p)\d*)*)"
    r"(?:-r(\d+))?", re.ASCII
)


def _number(value: str) -> int:
    """Ограничить длину числовой части, как в формате APK v2."""
    if len(value) > MAX_NUMBER_DIGITS:
        raise RepositoryError("APK: слишком длинная числовая часть версии")
    return int(value)


def _numeric_tokens(value: str, kind: int) -> list[tuple[int, int]]:
    """Учесть ведущие нули в компоненте после точки."""
    if kind == DOT_NUMBER and value.startswith("0"):
        zeros = len(value) - len(value.lstrip("0"))
        return [(kind, -(zeros - 1)), (NUMBER, _number(value[zeros - 1:]))]
    return [(kind, _number(value))]


def _main_tokens(value: str) -> list[tuple[int, int]]:
    """Разделить основную часть на числа, точки и буквенный суффикс."""
    result = []
    kind = NUMBER
    for part in re.findall(r"\d+|[a-z]|\.", value, flags=re.ASCII):
        if part == ".":
            kind = DOT_NUMBER
        elif part.isdigit():
            result.extend(_numeric_tokens(part, kind))
            kind = NUMBER
        else:
            result.append((LETTER, ord(part)))
            kind = NUMBER
    return result


def version_tokens(version: str) -> list[tuple[int, int]]:
    """Проверить версию и получить ее упорядоченные компоненты."""
    match = VERSION_FORMAT.fullmatch(version)
    if not match:
        raise RepositoryError(f"Некорректная версия APK: {version!r}")
    main, suffixes, revision = match.groups()
    result = _main_tokens(main)
    for suffix, number in re.findall(r"_([a-z]+)(\d*)", suffixes):
        result.append((SUFFIX, SUFFIX_RANK[suffix]))
        if number:
            result.append((SUFFIX_NUMBER, _number(number)))
    if revision is not None:
        result.append((REVISION, _number(revision)))
    return result + [(END, EQUAL)]


def _different_types(left: tuple, right: tuple) -> int:
    """Поставить предварительные выпуски перед версией без суффикса."""
    if left[0] == SUFFIX and left[1] < EQUAL:
        return LESS
    if right[0] == SUFFIX and right[1] < EQUAL:
        return GREATER
    return GREATER if left[0] < right[0] else LESS


def compare_versions(left: str, right: str, fuzzy: bool = False) -> int:
    """Сравнить версии; fuzzy разрешает совпадение общих начальных частей."""
    for first, second in zip(version_tokens(left), version_tokens(right)):
        if first[0] != second[0]:
            return EQUAL if fuzzy else _different_types(first, second)
        if first[1] != second[1]:
            return GREATER if first[1] > second[1] else LESS
    return EQUAL
