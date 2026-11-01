"""Проверки каждого параметра и ошибок чтения TOML."""

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.config import ConfigError, load_config, parse_config


class ConfigValidationTests(unittest.TestCase):
    """Проверка контракта пяти обязательных параметров."""

    def setUp(self):
        """Подготовить корректные значения всех пяти параметров."""
        self.valid = {
            "package_name": "zlib",
            "repository": "https://example.org/alpine/main",
            "test_mode": False,
            "package_version": "1.3.1-r1",
            "max_depth": 3,
        }

    def parse(self, **changes):
        """Проверить конфигурацию с измененными параметрами."""
        return parse_config(self.valid | changes, Path.cwd())

    def test_valid_config_preserves_all_parameters(self):
        """Убедиться, что проверка сохраняет все переданные настройки."""
        self.assertEqual(asdict(self.parse()), self.valid)

    def test_zero_depth_is_allowed(self):
        """Принять нулевую глубину как допустимую нижнюю границу."""
        self.assertEqual(self.parse(max_depth=0).max_depth, 0)

    def test_test_node_names_and_package_punctuation_are_allowed(self):
        """Принять тестовые имена и допустимые знаки в имени пакета."""
        for name in ("A", "libstdc++", "python3", "pkg_dev.test-1"):
            with self.subTest(name=name):
                config = self.parse(package_name=name)
                self.assertEqual(config.package_name, name)

    def test_invalid_package_name(self):
        """Отклонить пустые имена, пробелы, пути и неверные типы."""
        for value in ("", "  ", "two words", "../zlib", "-zlib", 123):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ConfigError, "package_name"):
                    self.parse(package_name=value)

    def test_invalid_repository_url(self):
        """Отклонить неверную схему, узел, порт и тип адреса."""
        values = (
            "", "ftp://example.org", "example.org", "https://",
            "https://two words.org", "https://example.org:abc",
            "https://example.org:99999", "https://[broken", False,
            "https://example.org\n/path",
        )
        for value in values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ConfigError, "repository"):
                    self.parse(repository=value)

    def test_valid_http_and_https_urls(self):
        """Принять адреса HTTP и HTTPS с допустимым портом."""
        for url in ("http://localhost:8080/main", "https://example.org/main"):
            with self.subTest(url=url):
                self.assertEqual(self.parse(repository=url).repository, url)

    def test_test_mode_requires_boolean(self):
        """Не принимать строки и числа вместо логического режима."""
        for value in ("false", "true", 0, 1, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ConfigError, "test_mode"):
                    self.parse(test_mode=value)

    def test_invalid_package_version(self):
        """Отклонить пустую версию, пробелы и неподходящие типы."""
        for value in ("", "  ", "latest", "1 2", "1.0\n", 1.2, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ConfigError, "package_version"):
                    self.parse(package_version=value)

    def test_max_depth_requires_nonnegative_integer(self):
        """Отклонить отрицательную глубину и неподходящие типы."""
        for value in (-1, 1.5, "3", True, False, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ConfigError, "max_depth"):
                    self.parse(max_depth=value)

    def test_each_missing_parameter_is_reported(self):
        """Проверить сообщение об отсутствии каждого параметра."""
        for name in self.valid:
            with self.subTest(name=name):
                data = self.valid.copy()
                del data[name]
                with self.assertRaisesRegex(ConfigError, name):
                    parse_config(data, Path.cwd())

    def test_unknown_parameter_is_reported(self):
        """Сообщить об опечатке или лишнем ключе конфигурации."""
        with self.assertRaisesRegex(ConfigError, "max_dept"):
            self.parse(max_dept=3)


class ConfigFileTests(unittest.TestCase):
    """Чтение реальных файлов и разрешение пути тестового репозитория."""

    def setUp(self):
        """Выделить временный каталог для конфигурации и репозитория."""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "config.toml"

    def write_test_config(self, repository="graph.txt"):
        """Создать конфигурацию с локальным источником."""
        self.path.write_text(
            'package_name = "A"\n'
            f'repository = "{repository}"\n'
            'test_mode = true\n'
            'package_version = "1.0-r0"\n'
            'max_depth = 0\n',
            encoding="utf-8",
        )

    def test_relative_repository_is_resolved_from_config_directory(self):
        """Разрешить относительный путь от каталога TOML-файла."""
        (self.directory / "graph.txt").write_text("A: B\n", encoding="utf-8")
        self.write_test_config()
        config = load_config(self.path)
        self.assertTrue(config.test_mode)
        self.assertEqual(config.repository, "graph.txt")

    def test_absolute_repository_path(self):
        """Прочитать конфигурацию с абсолютным путем к репозиторию."""
        graph = self.directory / "graph.txt"
        graph.write_text("A:\n", encoding="utf-8")
        self.write_test_config(graph.as_posix())
        self.assertEqual(load_config(self.path).repository, str(graph))

    def test_missing_repository_file(self):
        """Сообщить об отсутствующем файле тестового репозитория."""
        self.write_test_config()
        with self.assertRaisesRegex(ConfigError, "repository"):
            load_config(self.path)

    def test_repository_directory_is_rejected(self):
        """Отклонить каталог вместо файла тестового репозитория."""
        self.write_test_config(".")
        with self.assertRaisesRegex(ConfigError, "repository"):
            load_config(self.path)

    def test_unreadable_repository_is_reported(self):
        """Преобразовать отказ доступа к репозиторию в ConfigError."""
        graph = self.directory / "graph.txt"
        graph.touch()
        data = {
            "package_name": "A", "repository": "graph.txt",
            "test_mode": True, "package_version": "1.0", "max_depth": 0,
        }
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(ConfigError, "repository"):
                parse_config(data, self.directory)

    def test_unresolvable_home_directory_is_reported(self):
        """Сообщить об ошибке раскрытия домашнего каталога."""
        self.write_test_config("~/graph.txt")
        with patch.object(Path, "expanduser", side_effect=RuntimeError("home")):
            with self.assertRaisesRegex(ConfigError, "repository"):
                load_config(self.path)

    def test_missing_configuration_file(self):
        """Сообщить об отсутствующем TOML-файле."""
        with self.assertRaisesRegex(ConfigError, "конфигурационный файл"):
            load_config(self.path)

    def test_directory_instead_of_configuration(self):
        """Отклонить каталог вместо конфигурационного файла."""
        with self.assertRaisesRegex(ConfigError, "конфигурационный файл"):
            load_config(self.directory)

    def test_malformed_toml(self):
        """Сообщить об ошибке синтаксиса TOML."""
        self.path.write_text('package_name = "unfinished\n', encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "TOML"):
            load_config(self.path)

    def test_duplicate_toml_keys(self):
        """Отклонить TOML с повторным определением одного ключа."""
        self.path.write_text('max_depth = 1\nmax_depth = 2\n', encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "TOML"):
            load_config(self.path)

    def test_invalid_utf8(self):
        """Отклонить конфигурацию с неверной кодировкой UTF-8."""
        self.path.write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(ConfigError, "TOML"):
            load_config(self.path)


if __name__ == "__main__":
    unittest.main()
