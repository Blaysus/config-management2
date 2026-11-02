"""Текстовое представление графа зависимостей на языке PlantUML."""

import json

from .apk_index import RepositoryError
from .graph import DependencyGraph, ROOT_DEPTH


HEADER = (
    "@startuml",
    "!pragma layout smetana",
    "left to right direction",
    "skinparam shadowing false",
    "skinparam defaultFontName SansSerif",
    "title Граф зависимостей",
)
FOOTER = (
    "legend right",
    "  Зелёный: исходный пакет",
    "  Жёлтый: предел глубины",
    "  Красные стрелки: цикл",
    "endlegend",
    "@enduml",
)


def _validate_depths(graph: DependencyGraph, max_depth: int) -> None:
    """Проверить целые глубины узлов и нулевую глубину исходного пакета."""
    for depth in graph.depths.values():
        if type(depth) is not int or not ROOT_DEPTH <= depth <= max_depth:
            raise RepositoryError("PlantUML: некорректная глубина узла")
    if graph.depths[graph.root] != ROOT_DEPTH:
        raise RepositoryError("PlantUML: глубина корня должна быть равна 0")


def _validate_graph(graph: DependencyGraph, max_depth: int) -> None:
    """Проверить исходный узел, ссылки и полноту сведений о глубине."""
    if type(max_depth) is not int or max_depth < ROOT_DEPTH:
        raise RepositoryError("PlantUML: неверный предел глубины")
    if graph.root not in graph.edges:
        raise RepositoryError("PlantUML: исходный узел отсутствует в графе")
    if set(graph.edges) != set(graph.depths):
        raise RepositoryError("PlantUML: глубина указана не для всех узлов")
    for children in graph.edges.values():
        if any(child not in graph.edges for child in children):
            raise RepositoryError(
                "PlantUML: ребро ссылается на неизвестный узел"
            )
    _validate_depths(graph, max_depth)


def _node_line(
    node: str, alias: str, graph: DependencyGraph, limit: int
) -> str:
    """Экранировать подпись и отметить корень или границу глубины цветом."""
    label = node
    color = "#D5E8D4" if node == graph.root else "#FFFFFF"
    if graph.depths[node] >= limit:
        label += "\n[предел глубины]"
        color = "#FFF2CC"
    quoted = json.dumps(label, ensure_ascii=False)
    return f"rectangle {quoted} as {alias} {color}"


def _edge_lines(graph: DependencyGraph, aliases: dict[str, str]) -> list[str]:
    """Вывести уникальные ребра и выделить найденные циклы красным."""
    cycles = {
        pair for cycle in graph.cycles for pair in zip(cycle, cycle[1:])
    }
    edges = {
        (parent, child) for parent, children in graph.edges.items()
        for child in children
    }
    result = []
    for parent, child in sorted(edges):
        arrow = "-[#C0504D]->" if (parent, child) in cycles else "-->"
        result.append(f"{aliases[parent]} {arrow} {aliases[child]}")
    return result


def to_plantuml(graph: DependencyGraph, max_depth: int) -> str:
    """Сформировать завершенную диаграмму с устойчивыми именами узлов."""
    _validate_graph(graph, max_depth)
    aliases = {
        node: f"n{index}" for index, node in enumerate(sorted(graph.edges))
    }
    lines = list(HEADER)
    lines.append(f"caption Максимальная глубина: {max_depth}")
    for node, alias in aliases.items():
        lines.append(_node_line(node, alias, graph, max_depth))
    lines.extend(_edge_lines(graph, aliases))
    lines.extend(FOOTER)
    return "\n".join(lines) + "\n"
