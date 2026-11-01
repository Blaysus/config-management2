"""Командная строка первого этапа: прочитать и вывести настройки."""

import argparse
from dataclasses import fields
from pathlib import Path
import sys

from .config import Config, ConfigError, load_config


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "examples/config.toml"
ERROR_EXIT_CODE = 2


def create_parser() -> argparse.ArgumentParser:
    """Создать справку и единственную опцию выбора файла конфигурации."""
    parser = argparse.ArgumentParser(
        description="Вариант 22, этап 1: проверка TOML и вывод параметров."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        metavar="PATH",
        help="путь к TOML-файлу (по умолчанию examples/config.toml)",
    )
    return parser


def print_config(config: Config) -> None:
    """Вывести все пять настроек как ключ = значение в постоянном порядке."""
    for field in fields(config):
        value = getattr(config, field.name)
        if isinstance(value, bool):
            value = str(value).lower()
        print(f"{field.name} = {value}")


def main(argv: list[str] | None = None) -> int:
    """Запустить CLI; вернуть 0 при успехе и 2 при ошибке конфигурации."""
    args = create_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return ERROR_EXIT_CODE
    print_config(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
