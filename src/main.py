"""CLI этапов 1 и 2: проверка настроек и прямые зависимости APK."""

import argparse
from dataclasses import fields
from pathlib import Path
import sys

from .apk_index import Package, RepositoryError
from .config import Config, ConfigError, load_config
from .repository import get_package


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "examples/config.toml"
ERROR_EXIT_CODE = 2


def create_parser() -> argparse.ArgumentParser:
    """Создать опцию выбора TOML и режим демонстрации первого этапа."""
    parser = argparse.ArgumentParser(
        description="Вариант 22: прямые зависимости выбранной версии APK."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        metavar="PATH",
        help="путь к TOML-файлу (по умолчанию examples/config.toml)",
    )
    parser.add_argument(
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
        else:
            base_dir = args.config.resolve().parent
            print_dependencies(get_package(config, base_dir))
    except (ConfigError, RepositoryError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return ERROR_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
