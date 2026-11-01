"""Сквозные проверки запуска приложения и демонстрационных конфигураций."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ERROR_EXIT_CODE = 2
PARAMETER_COUNT = 5


class CliTests(unittest.TestCase):
    """Поведение приложения, видимое пользователю при запуске."""

    def run_cli(self, *arguments):
        """Запустить CLI отдельным процессом без сетевых обращений."""
        return subprocess.run(
            [sys.executable, "-m", "src.main", *arguments],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_default_config_prints_exactly_five_parameters(self):
        """Проверить точный вывод всех пяти настроек по умолчанию."""
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            'package_name = zlib\n'
            'repository = https://dl-cdn.alpinelinux.org/'
            'alpine/v3.20/main/x86_64\n'
            'test_mode = false\n'
            'package_version = 1.3.1-r1\n'
            'max_depth = 3\n',
        )

    def test_local_config(self):
        """Проверить запуск тестового режима с нулевой глубиной."""
        result = self.run_cli("--config", "examples/test_config.toml")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), PARAMETER_COUNT)
        self.assertIn("test_mode = true\n", result.stdout)
        self.assertIn("max_depth = 0\n", result.stdout)

    def test_error_example_for_each_parameter(self):
        """Проверить демонстрацию ошибки каждого из пяти параметров."""
        names = (
            "package_name", "repository", "test_mode",
            "package_version", "max_depth",
        )
        for name in names:
            with self.subTest(name=name):
                result = self.run_cli(
                    "--config", f"examples/errors/{name}.toml"
                )
                self.assertEqual(result.returncode, ERROR_EXIT_CODE)
                self.assertEqual(result.stdout, "")
                self.assertIn(name, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_missing_file_returns_error_without_traceback(self):
        """Вывести понятную ошибку при отсутствии конфигурации."""
        result = self.run_cli("--config", "examples/missing.toml")
        self.assertEqual(result.returncode, ERROR_EXIT_CODE)
        self.assertEqual(result.stdout, "")
        self.assertIn("Ошибка:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_help(self):
        """Проверить доступность справки и описания выбора TOML-файла."""
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--config", result.stdout)

    def test_unknown_option_is_rejected(self):
        """Отклонить неизвестную опцию командной строки."""
        result = self.run_cli("--package", "zlib")
        self.assertEqual(result.returncode, ERROR_EXIT_CODE)

    def test_config_option_requires_path(self):
        """Сообщить, что после --config нужен путь к файлу."""
        result = self.run_cli("--config")
        self.assertEqual(result.returncode, ERROR_EXIT_CODE)

    @unittest.skipIf(os.name == "nt", "run.sh предназначен для POSIX")
    def test_run_script_from_another_directory(self):
        """Сохранить относительный путь при запуске из другого места."""
        with TemporaryDirectory() as directory:
            source = PROJECT_ROOT / "examples/config.toml"
            (Path(directory) / "custom.toml").write_bytes(source.read_bytes())
            result = subprocess.run(
                ["sh", str(PROJECT_ROOT / "run.sh"), "--config", "custom.toml"],
                cwd=directory,
                env=os.environ | {"PYTHON": sys.executable},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("package_name = zlib", result.stdout)


if __name__ == "__main__":
    unittest.main()
