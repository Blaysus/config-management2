"""Чтение тестового репозитория с именами из заглавных латинских букв."""

from pathlib import Path
import re

from .apk_index import RepositoryError


NODE_PATTERN = r"[A-Z]+"
MAX_GRAPH_BYTES = 1024 * 1024


def _parse_line(line: str, number: int) -> tuple[str, tuple[str, ...]]:
    """Разобрать строку A: B C и проверить имена узлов."""
    name, separator, value = line.partition(":")
    name = name.strip()
    children = tuple(dict.fromkeys(value.split()))
    names = (name, *children)
    if not separator or not all(re.fullmatch(NODE_PATTERN, n) for n in names):
        raise RepositoryError(f"Граф, строка {number}: ожидается A: B C")
    return name, children


def _validate_targets(graph: dict[str, tuple[str, ...]]) -> None:
    """Потребовать отдельную строку для каждого упомянутого узла."""
    for name, children in graph.items():
        for child in children:
            if child not in graph:
                raise RepositoryError(
                    f"Граф: для зависимости {child} узла {name} нет описания"
                )


def parse_test_graph(text: str) -> dict[str, tuple[str, ...]]:
    """Прочитать граф, пропуская пустые строки и строки с #."""
    graph = {}
    for number, source in enumerate(text.splitlines(), start=1):
        line = source.strip()
        if not line or line.startswith("#"):
            continue
        name, children = _parse_line(line, number)
        if name in graph:
            raise RepositoryError(f"Граф: повторное описание узла {name}")
        graph[name] = children
    if not graph:
        raise RepositoryError("Граф: файл не содержит узлов")
    _validate_targets(graph)
    return graph


def load_test_graph(repository: str, base_dir: Path) -> dict:
    """Прочитать граф в UTF-8 относительно каталога конфигурации."""
    try:
        path = Path(repository).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        with path.open("rb") as source:
            data = source.read(MAX_GRAPH_BYTES + 1)
        if len(data) > MAX_GRAPH_BYTES:
            raise RepositoryError("Граф: превышен допустимый размер файла")
        return parse_test_graph(data.decode("utf-8"))
    except (OSError, RuntimeError, UnicodeError) as error:
        raise RepositoryError(f"Не удалось прочитать граф: {error}") from error
