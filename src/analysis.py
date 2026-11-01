"""Выбор источника данных для построения графа третьего этапа."""

from pathlib import Path

from .apk_index import RepositoryError, find_package
from .config import Config
from .graph import DependencyGraph, build_graph
from .graph_file import load_test_graph
from .repository import load_packages
from .resolver import PackageResolver, package_key


def analyze_graph(config: Config, base_dir: Path) -> DependencyGraph:
    """Построить граф тестовых имен или реальных пакетов выбранной версии."""
    if config.test_mode:
        graph = load_test_graph(config.repository, base_dir)
        if config.package_name not in graph:
            raise RepositoryError(
                f"Узел {config.package_name} не найден в графе"
            )
        return build_graph(
            config.package_name, graph.__getitem__, config.max_depth
        )
    packages = load_packages(config, base_dir)
    root = find_package(packages, config.package_name, config.package_version)
    resolver = PackageResolver(packages)
    return build_graph(package_key(root), resolver.neighbors, config.max_depth)
