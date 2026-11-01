"""Прямой и обратный обход графов тестовых и реальных репозиториев."""

from pathlib import Path

from .apk_index import RepositoryError, find_package
from .config import Config
from .graph import DependencyGraph, build_graph
from .graph_file import load_test_graph
from .repository import load_packages
from .resolver import PackageResolver, package_key
from .reverse import apk_reverse_edges, reverse_edges


def analyze_graph(
    config: Config, base_dir: Path, reverse: bool = False
) -> DependencyGraph:
    """Обойти исходные или обратные связи существующим рекурсивным DFS."""
    if config.test_mode:
        graph = load_test_graph(config.repository, base_dir)
        if config.package_name not in graph:
            raise RepositoryError(
                f"Узел {config.package_name} не найден в графе"
            )
        if reverse:
            graph = reverse_edges(graph)
        return build_graph(
            config.package_name, graph.__getitem__, config.max_depth
        )
    packages = load_packages(config, base_dir)
    root = find_package(packages, config.package_name, config.package_version)
    resolver = PackageResolver(packages)
    if reverse:
        graph = apk_reverse_edges(resolver)
        return build_graph(
            package_key(root), graph.__getitem__, config.max_depth
        )
    return build_graph(package_key(root), resolver.neighbors, config.max_depth)
