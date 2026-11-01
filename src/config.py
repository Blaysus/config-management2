"""Загрузка и проверка пяти параметров TOML-конфигурации."""

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit


MIN_DEPTH = 0
CONTROL_CHAR_LIMIT = 32
DELETE_CHAR_CODE = 127
PACKAGE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._+-]*"
VERSION_PATTERN = r"[0-9][A-Za-z0-9._+~-]*"
REQUIRED_FIELDS = (
    "package_name",
    "repository",
    "test_mode",
    "package_version",
    "max_depth",
)


class ConfigError(ValueError):
    """Ошибка чтения конфигурации или недопустимое значение параметра."""


@dataclass(frozen=True)
class Config:
    """Проверенные настройки приложения."""

    package_name: str
    repository: str
    test_mode: bool
    package_version: str
    max_depth: int


def _require_text(value: object, name: str) -> str:
    """Проверить тип, непустое значение и отсутствие управляющих знаков."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name}: ожидается непустая строка")
    if any(
        ord(char) < CONTROL_CHAR_LIMIT or ord(char) == DELETE_CHAR_CODE
        for char in value
    ):
        raise ConfigError(f"{name}: управляющие символы недопустимы")
    return value


def _validate_package_name(value: object) -> str:
    """Проверить имя пакета, разрешая также имена тестовых узлов A, B, C."""
    name = _require_text(value, "package_name")
    if not re.fullmatch(PACKAGE_PATTERN, name):
        raise ConfigError(
            "package_name: начните с латинской буквы или цифры; "
            "далее допустимы латинские буквы, цифры, '.', '_', '+', '-'"
        )
    return name


def _validate_package_version(value: object) -> str:
    """Проверить текстовый формат версии без обращения к репозиторию."""
    version = _require_text(value, "package_version")
    if not re.fullmatch(VERSION_PATTERN, version):
        raise ConfigError(
            "package_version: ожидается строка, начинающаяся с цифры; "
            "далее допустимы латинские буквы, цифры, '.', '_', '+', '~', '-'"
        )
    return version


def _validate_url(value: str) -> None:
    """Проверить схему, узел и порт HTTP(S)-адреса без сетевого запроса.

    Чтение parts.port выявляет нечисловой порт и выход за его диапазон.
    """
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and not any(char.isspace() for char in value)
        )
        _ = parts.port
    except ValueError as error:
        raise ConfigError(f"repository: некорректный URL: {error}") from error
    if not valid:
        raise ConfigError("repository: ожидается URL с http:// или https://")


def _validate_test_file(value: str, base_dir: Path) -> None:
    """Проверить доступность файла относительно каталога конфигурации."""
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        if not path.is_file():
            raise ConfigError(f"repository: тестовый файл не найден: {path}")
        with path.open("rb"):
            pass
    except (OSError, RuntimeError) as error:
        raise ConfigError(
            f"repository: невозможно прочитать тестовый файл: {error}"
        ) from error


def _validate_repository(
    value: object, test_mode: bool, base_dir: Path
) -> str:
    """Выбрать проверку URL или локального файла по режиму работы."""
    repository = _require_text(value, "repository")
    if test_mode:
        _validate_test_file(repository, base_dir)
    else:
        _validate_url(repository)
    return repository


def _validate_max_depth(value: object) -> int:
    """Принять только целую неотрицательную глубину, исключая bool."""
    if type(value) is not int or value < MIN_DEPTH:
        raise ConfigError("max_depth: ожидается целое число не меньше 0")
    return value


def _validate_fields(data: dict) -> None:
    """Отклонить отсутствующие и неизвестные ключи конфигурации."""
    missing = set(REQUIRED_FIELDS) - data.keys()
    if missing:
        names = ", ".join(sorted(missing))
        raise ConfigError("Отсутствуют параметры: " + names)
    unknown = data.keys() - set(REQUIRED_FIELDS)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigError("Неизвестные параметры: " + names)


def parse_config(data: dict, base_dir: Path) -> Config:
    """Проверить словарь TOML и создать объект настроек."""
    _validate_fields(data)
    if type(data["test_mode"]) is not bool:
        raise ConfigError("test_mode: ожидается true или false без кавычек")
    return Config(
        package_name=_validate_package_name(data["package_name"]),
        repository=_validate_repository(
            data["repository"], data["test_mode"], base_dir
        ),
        test_mode=data["test_mode"],
        package_version=_validate_package_version(data["package_version"]),
        max_depth=_validate_max_depth(data["max_depth"]),
    )


def load_config(path: Path) -> Config:
    """Прочитать TOML в UTF-8 и сообщить понятную ошибку при сбое."""
    try:
        with path.open("rb") as source:
            data = tomllib.load(source)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"Некорректный TOML в {path}: {error}") from error
    except OSError as error:
        raise ConfigError(
            f"Не удалось прочитать конфигурационный файл {path}: {error}"
        ) from error
    return parse_config(data, path.resolve().parent)
