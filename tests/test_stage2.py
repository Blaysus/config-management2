"""Этап 2: APKINDEX, загрузка, точная версия, ошибки и вывод CLI."""

from contextlib import redirect_stderr, redirect_stdout
import gzip
from http.client import IncompleteRead
from io import BytesIO, StringIO
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.apk_index import RepositoryError, find_package, parse_index
from src.config import Config
from src.main import main
from src.repository import (
    build_index_url, download_index, extract_index, get_package,
    read_test_index,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ERROR_EXIT_CODE = 2
SMALL_LIMIT = 8
INDEX_TEXT = (
    "P:demo\nV:1.0-r0\nD:old-lib\n\n"
    "P:demo\nV:2.0-r0\n"
    "D:libfoo>=1.2 so:libc.musl-x86_64.so.1 cmd:sh !legacy\n"
    "p:virtual-demo=2.0\n\n"
    "P:empty\nV:1.0-r0\n"
)
REPOSITORY_URL = "https://example.org/alpine/main/x86_64"


def make_tar(entries: dict[str, bytes]) -> bytes:
    """Собрать небольшой tar в памяти без файлов в репозитории."""
    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
    return stream.getvalue()


def make_archive(text: str = INDEX_TEXT) -> bytes:
    """Собрать gzip/tar с текстовым APKINDEX для независимых тестов."""
    return gzip.compress(make_tar({"APKINDEX": text.encode("utf-8")}))


def run_main(*arguments) -> tuple[int, str, str]:
    """Вызвать CLI с перехватом обоих потоков и кода завершения."""
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(arguments))
    return code, stdout.getvalue(), stderr.getvalue()


class IndexTests(unittest.TestCase):
    """Самостоятельный разбор записей и выбор версии пакета."""

    def test_exact_version_is_selected(self):
        """Выбрать вторую версию и не вернуть зависимости первой."""
        package = find_package(parse_index(INDEX_TEXT), "demo", "2.0-r0")
        self.assertEqual(package.version, "2.0-r0")
        self.assertEqual(package.dependencies, (
            "libfoo>=1.2", "so:libc.musl-x86_64.so.1", "cmd:sh",
        ))
        self.assertEqual(package.conflicts, ("!legacy",))

    def test_provides_are_not_dependencies(self):
        """Не смешивать поле p предоставлений с полем D зависимостей."""
        package = find_package(parse_index(INDEX_TEXT), "demo", "2.0-r0")
        self.assertNotIn("virtual-demo=2.0", package.dependencies)

    def test_no_dependency_field_means_no_dependencies(self):
        """Принять пакет без поля D как пакет без зависимостей."""
        package = find_package(parse_index(INDEX_TEXT), "empty", "1.0-r0")
        self.assertEqual(package.dependencies, ())

    def test_empty_dependency_field(self):
        """Разрешить явно пустое поле зависимостей."""
        package = parse_index("P:demo\nV:1.0\nD:\n")[("demo", "1.0")]
        self.assertEqual(package.dependencies, ())

    def test_missing_package_is_reported(self):
        """Сообщить об отсутствующем имени пакета."""
        with self.assertRaisesRegex(RepositoryError, "Пакет.*не найден"):
            find_package(parse_index(INDEX_TEXT), "unknown", "1.0")

    def test_missing_version_lists_available_versions(self):
        """Не подменять запрошенную версию и показать доступные."""
        with self.assertRaisesRegex(RepositoryError, "1.0-r0, 2.0-r0"):
            find_package(parse_index(INDEX_TEXT), "demo", "9.0")

    def test_crlf_blank_lines_and_no_final_newline(self):
        """Принять CRLF, пробелы между записями и конец без перевода."""
        text = "P:A\r\nV:1.0\r\n \r\n\r\nP:B\r\nV:1.0"
        self.assertEqual(set(parse_index(text)), {("A", "1.0"), ("B", "1.0")})

    def test_missing_or_invalid_identity_fields(self):
        """Отклонить записи без корректных P и V."""
        for text in ("V:1.0", "P:A", "P:two words\nV:1.0", "P:A\nV:x"):
            with self.subTest(text=text):
                with self.assertRaises(RepositoryError):
                    parse_index(text)

    def test_empty_or_malformed_index(self):
        """Отклонить пустой индекс и строки без разделителя поля."""
        for text in ("", "\n\n", "invalid", "PP:A\nV:1.0"):
            with self.subTest(text=text):
                with self.assertRaises(RepositoryError):
                    parse_index(text)

    def test_duplicate_fields_and_packages(self):
        """Отклонить неоднозначные поля и повторную пару имя-версия."""
        values = (
            "P:A\nP:B\nV:1.0", "P:A\nV:1.0\nV:2.0",
            "P:A\nV:1.0\nD:B\nD:C",
            "P:A\nV:1.0\n\nP:A\nV:1.0",
        )
        for text in values:
            with self.subTest(text=text):
                with self.assertRaisesRegex(RepositoryError, "повтор"):
                    parse_index(text)

    def test_empty_conflict_is_rejected(self):
        """Отклонить символ конфликта без имени."""
        with self.assertRaisesRegex(RepositoryError, "конфликта"):
            parse_index("P:A\nV:1.0\nD:!")


class ArchiveTests(unittest.TestCase):
    """Чтение реального контейнера APKINDEX и поврежденных данных."""

    def test_regular_archive(self):
        """Прочитать обычный gzip/tar с APKINDEX."""
        self.assertEqual(extract_index(make_archive()), INDEX_TEXT)

    def test_signed_concatenated_gzip_archive(self):
        """Найти индекс после отдельного gzip/tar с подписью."""
        signature = gzip.compress(make_tar({".SIGN.RSA.test": b"signature"}))
        self.assertEqual(extract_index(signature + make_archive()), INDEX_TEXT)

    def test_dot_slash_member_name(self):
        """Принять стандартное имя ./APKINDEX."""
        data = gzip.compress(make_tar({"./APKINDEX": b"P:A\nV:1.0"}))
        self.assertIn(("A", "1.0"), parse_index(extract_index(data)))

    def test_invalid_or_truncated_archive(self):
        """Сообщить об HTML вместо gzip, неверном tar и обрыве данных."""
        values = (b"<html>error</html>", gzip.compress(b"bad tar"),
                  make_archive()[:SMALL_LIMIT], make_archive()[:-SMALL_LIMIT])
        for data in values:
            with self.subTest(data=data[:SMALL_LIMIT]):
                with self.assertRaises(RepositoryError):
                    extract_index(data)

    def test_missing_and_duplicate_index(self):
        """Требовать один однозначный файл APKINDEX."""
        values = (
            {"DESCRIPTION": b"test"},
            {"APKINDEX": b"P:A\nV:1", "./APKINDEX": b"P:B\nV:1"},
        )
        for entries in values:
            with self.subTest(entries=entries):
                with self.assertRaisesRegex(RepositoryError, "один файл"):
                    extract_index(gzip.compress(make_tar(entries)))

    def test_symlink_is_not_read(self):
        """Не следовать ссылке вместо обычного файла APKINDEX."""
        stream = BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo("APKINDEX")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
        with self.assertRaisesRegex(RepositoryError, "тип или размер"):
            extract_index(gzip.compress(stream.getvalue()))

    def test_invalid_utf8_is_reported(self):
        """Не заменять поврежденную кодировку индекса молча."""
        data = gzip.compress(make_tar({"APKINDEX": b"\xff"}))
        with self.assertRaisesRegex(RepositoryError, "UTF-8"):
            extract_index(data)

    def test_archive_and_member_size_limits(self):
        """Остановить чтение слишком большого архива или APKINDEX."""
        for limit in ("MAX_ARCHIVE_BYTES", "MAX_INDEX_BYTES"):
            with self.subTest(limit=limit):
                with patch("src.repository." + limit, SMALL_LIMIT):
                    with self.assertRaises(RepositoryError):
                        extract_index(make_archive())


class DownloadTests(unittest.TestCase):
    """HTTP-клиент без реальных сетевых запросов в автоматических тестах."""

    def test_directory_and_index_urls(self):
        """Дополнить каталог и сохранить готовый URL с query-параметрами."""
        expected = REPOSITORY_URL + "/APKINDEX.tar.gz"
        for source in (REPOSITORY_URL, REPOSITORY_URL + "/", expected):
            with self.subTest(source=source):
                self.assertEqual(build_index_url(source), expected)
        self.assertEqual(build_index_url(REPOSITORY_URL + "?key=value"),
                         expected + "?key=value")

    def test_download_uses_timeout_and_expected_url(self):
        """Передать запросу адрес индекса, таймаут и User-Agent."""
        with patch("src.repository.urlopen",
                   return_value=BytesIO(b"data")) as op:
            self.assertEqual(download_index(REPOSITORY_URL), b"data")
        request = op.call_args.args[0]
        self.assertEqual(request.full_url, build_index_url(REPOSITORY_URL))
        self.assertEqual(op.call_args.kwargs, {"timeout": 20})
        self.assertIn("config-management2", request.get_header("User-agent"))

    def test_http_and_connection_errors(self):
        """Обработать HTTP, недоступный узел, таймаут и обрыв ответа."""
        errors = (
            HTTPError(REPOSITORY_URL, 404, "Not Found", {}, None),
            URLError("unreachable"), TimeoutError("timeout"),
            IncompleteRead(b"partial"),
        )
        for error in errors:
            with self.subTest(error=error):
                with patch("src.repository.urlopen", side_effect=error):
                    with self.assertRaisesRegex(RepositoryError, "скачать"):
                        download_index(REPOSITORY_URL)

    def test_download_size_limit(self):
        """Ограничить объем загружаемого ответа."""
        with patch("src.repository.MAX_DOWNLOAD_BYTES", SMALL_LIMIT):
            with patch("src.repository.urlopen",
                       return_value=BytesIO(b"x" * (SMALL_LIMIT + 1))):
                with self.assertRaisesRegex(RepositoryError, "лимит"):
                    download_index(REPOSITORY_URL)


class RepositoryTests(unittest.TestCase):
    """Получение пакета из обоих источников с единым разбором APKINDEX."""

    def test_remote_package_uses_exact_version(self):
        """Получить пакет нужной версии через весь путь HTTP/gzip/tar."""
        config = Config("demo", REPOSITORY_URL, False, "2.0-r0", 0)
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive())):
            package = get_package(config, PROJECT_ROOT)
        self.assertEqual(package.dependencies[0], "libfoo>=1.2")

    def test_local_repository_uses_config_directory(self):
        """Получить только прямые B и C из локального примера, без D."""
        config = Config("A", "test_repository.txt", True, "1.0-r0", 0)
        package = get_package(config, PROJECT_ROOT / "examples")
        self.assertEqual(package.dependencies, ("B", "C"))

    def test_missing_or_invalid_local_file(self):
        """Обработать отсутствие, кодировку и лимит локального индекса."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "APKINDEX"
            with self.assertRaises(RepositoryError):
                read_test_index(str(path), PROJECT_ROOT)
            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(RepositoryError, "UTF-8"):
                read_test_index(str(path), PROJECT_ROOT)
            path.write_text(INDEX_TEXT, encoding="utf-8")
            with patch("src.repository.MAX_INDEX_BYTES", SMALL_LIMIT):
                with self.assertRaisesRegex(RepositoryError, "лимит"):
                    read_test_index(str(path), PROJECT_ROOT)


class Stage2CliTests(unittest.TestCase):
    """Вывод второго этапа и сохранение доступности первого этапа."""

    def test_default_command_prints_direct_dependencies(self):
        """По умолчанию вывести зависимости вместо настроек TOML."""
        text = "P:zlib\nV:1.3.2-r0\nD:so:libc.musl-x86_64.so.1"
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive(text))):
            code, stdout, stderr = run_main()
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(stdout, "Прямые зависимости zlib (1.3.2-r0):\n"
                         "so:libc.musl-x86_64.so.1\n")

    def test_show_config_does_not_use_network(self):
        """Сохранить демонстрацию первого этапа без загрузки индекса."""
        with patch("src.repository.urlopen") as opener:
            code, stdout, stderr = run_main("--show-config")
        opener.assert_not_called()
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("package_name = zlib\n", stdout)

    def test_missing_version_has_no_traceback_or_partial_output(self):
        """Сообщить об отсутствующей версии без частичного результата."""
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive("P:zlib\nV:1.0\n"))):
            code, stdout, stderr = run_main()
        self.assertEqual((code, stdout), (ERROR_EXIT_CODE, ""))
        self.assertIn("не найден", stderr)
        self.assertIn("Доступные версии: 1.0", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_no_dependencies_message(self):
        """Различать отсутствие зависимостей и отсутствие пакета."""
        text = "P:zlib\nV:1.3.2-r0\n"
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive(text))):
            code, stdout, stderr = run_main()
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Прямых зависимостей нет.", stdout)

    def test_bad_archive_is_reported_without_traceback(self):
        """Преобразовать поврежденный HTTP-ответ в понятную ошибку CLI."""
        with patch("src.repository.urlopen", return_value=BytesIO(b"bad")):
            code, stdout, stderr = run_main()
        self.assertEqual((code, stdout), (ERROR_EXIT_CODE, ""))
        self.assertIn("Ошибка:", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_offline_example_in_separate_process(self):
        """Проверить точку входа второго этапа без доступа к сети."""
        result = subprocess.run(
            [sys.executable, "-m", "src.main", "--config",
             "examples/test_config.toml"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            result.stdout, "Прямые зависимости A (1.0-r0):\nB\nC\n"
        )


if __name__ == "__main__":
    unittest.main()
