"""Построение обратных связей для тестового графа и пакетов APK."""

from .apk_index import RepositoryError
from .resolver import PackageResolver, package_key, parse_requirement


def _unique_edges(edges: dict[str, list[str]]) -> dict[str, tuple[str, ...]]:
    """Убрать повторные ребра, сохранив порядок пакетов репозитория."""
    return {
        node: tuple(dict.fromkeys(parents)) for node, parents in edges.items()
    }


def reverse_edges(edges: dict[str, tuple[str, ...]]) -> dict:
    """Развернуть все ребра, сохраняя также изолированные узлы."""
    result = {node: [] for node in edges}
    for parent, children in edges.items():
        for child in children:
            if child not in result:
                raise RepositoryError(f"Граф: узел {child} не описан")
            result[child].append(parent)
    return _unique_edges(result)


def apk_reverse_edges(resolver: PackageResolver) -> dict:
    """Связать каждую подходящую версию поставщика с зависимыми пакетами."""
    result = {key: [] for key in resolver.packages}
    for parent, package in resolver.packages.items():
        for text in package.dependencies:
            requirement = parse_requirement(text)
            for provider in resolver.matching_providers(requirement):
                result[package_key(provider.package)].append(parent)
    return _unique_edges(result)
