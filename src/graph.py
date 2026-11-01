"""Рекурсивный DFS с ограничением глубины и учетом циклов."""

from collections.abc import Callable
from dataclasses import dataclass, field

from .apk_index import RepositoryError


ROOT_DEPTH = 0


@dataclass
class DependencyGraph:
    """Списки смежности, минимальные глубины и найденные циклы."""

    root: str
    edges: dict[str, tuple[str, ...]] = field(default_factory=dict)
    depths: dict[str, int] = field(default_factory=dict)
    cycles: list[tuple[str, ...]] = field(default_factory=list)


def _record_cycle(graph: DependencyGraph, path: list[str], node: str) -> None:
    """Сохранить замкнутый участок текущего пути без повторов."""
    cycle = tuple(path[path.index(node):] + [node])
    if cycle not in graph.cycles:
        graph.cycles.append(cycle)


def _visit(node, depth, graph, neighbors, max_depth, path) -> None:
    """Обойти узел; повторить раскрытие при обнаружении короткого пути."""
    if node in path:
        return
    previous = graph.depths.get(node)
    if previous is not None and previous <= depth:
        return
    graph.depths[node] = depth
    graph.edges.setdefault(node, ())
    if depth >= max_depth:
        return
    path.append(node)
    children = tuple(dict.fromkeys(neighbors(node)))
    graph.edges[node] = children
    for child in children:
        _visit(child, depth + 1, graph, neighbors, max_depth, path)
    path.pop()


def _cycle_visit(node, graph, path, finished) -> None:
    """Найти циклы в построенном подграфе независимо от путей раскрытия."""
    if node in path:
        _record_cycle(graph, path, node)
        return
    if node in finished:
        return
    path.append(node)
    for child in graph.edges[node]:
        _cycle_visit(child, graph, path, finished)
    path.pop()
    finished.add(node)


def build_graph(
    root: str, neighbors: Callable, max_depth: int
) -> DependencyGraph:
    """Построить достижимую часть графа; глубина корня равна нулю."""
    if type(max_depth) is not int or max_depth < ROOT_DEPTH:
        raise RepositoryError("Глубина должна быть целым числом не меньше 0")
    graph = DependencyGraph(root)
    try:
        _visit(root, ROOT_DEPTH, graph, neighbors, max_depth, [])
        _cycle_visit(root, graph, [], set())
    except RecursionError as error:
        raise RepositoryError(
            "Цепочка зависимостей превышает предел рекурсии Python"
        ) from error
    return graph
