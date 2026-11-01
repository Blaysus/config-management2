"""CLI варианта 22: настройки, прямые и обратные зависимости APK."""

import argparse
from dataclasses import fields
from pathlib import Path
import sys

from .apk_index import Package, RepositoryError
from .analysis import analyze_graph
from .config import Config, ConfigError, load_config
from .graph import DependencyGraph
from .repository import get_package


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "examples/config.toml"
ERROR_EXIT_CODE = 2


def create_parser() -> argparse.ArgumentParser:
    """Создать выбор TOML и режимы четырех этапов работы."""
    parser = argparse.ArgumentParser(
        description="Вариант 22: граф зависимостей APK, рекурсивный DFS."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        metavar="PATH",
        help="путь к TOML-файлу (по умолчанию examples/config.toml)",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--direct", action="store_true",
        help="вывести только прямые зависимости (этап 2)",
    )
    modes.add_argument(
        "--reverse", action="store_true",
        help="вывести обратные зависимости (этап 4)",
    )
    modes.add_argument(
        "--show-config",
        action="store_true",
        help="только проверить и вывести настройки (демонстрация этапа 1)",
    )
    return parser


def print_config(config: Config) -> None:
    """Вывести все пять настроек как ключ = значение в постоянном порядке."""
    for field in fields(config):
        value = getattr(config, field.name)
        if isinstance(value, bool):
            value = str(value).lower()
        print(f"{field.name} = {value}")


def print_dependencies(package: Package) -> None:
    """Вывести все прямые требования APK, сохраняя ограничения версий."""
    print(f"Прямые зависимости {package.name} ({package.version}):")
    if not package.dependencies:
        print("Прямых зависимостей нет.")
    for dependency in package.dependencies:
        print(dependency)


def main(argv: list[str] | None = None) -> int:
    """Запустить выбранный режим; вернуть 0 при успехе и 2 при ошибке."""
    args = create_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.show_config:
            print_config(config)
        elif args.direct:
            package = get_package(config, args.config.resolve().parent)
            print_dependencies(package)
        else:
            graph = analyze_graph(
                config, args.config.resolve().parent, reverse=args.reverse
            )
            if args.reverse:
                print_reverse(graph, config.max_depth)
            else:
                print_graph(graph, config.max_depth)
    except (ConfigError, RepositoryError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return ERROR_EXIT_CODE
    return 0


def print_graph(graph: DependencyGraph, max_depth: int) -> None:
    """Показать списки смежности, границу глубины и найденные циклы."""
    print(f"Граф зависимостей (глубина {max_depth}):")
    for node, children in graph.edges.items():
        ending = "-"
        if graph.depths[node] >= max_depth:
            ending = "[предел глубины]"
        print(f"{node} -> {', '.join(children) if children else ending}")
    for cycle in graph.cycles:
        print("Цикл: " + " -> ".join(cycle))


def print_reverse(graph: DependencyGraph, max_depth: int) -> None:
    """Вывести зависимые пакеты и их расстояние до исходного пакета."""
    print(f"Обратные зависимости {graph.root} (глубина {max_depth}):")
    dependents = [node for node in graph.edges if node != graph.root]
    if not dependents:
        print("Обратных зависимостей в пределах заданной глубины нет.")
    for node in dependents:
        print(f"{node} (глубина {graph.depths[node]})")
    for cycle in graph.cycles:
        print("Цикл: " + " -> ".join(cycle))


if __name__ == "__main__":
    sys.exit(main())
