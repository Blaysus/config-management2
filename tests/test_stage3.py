"""Этап 3: рекурсивный DFS, глубина, циклы и тестовые репозитории."""

from collections import deque
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from itertools import product
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from src.analysis import analyze_graph
from src.apk_index import RepositoryError, parse_index
from src.apk_versions import compare_versions, version_tokens
from src.config import Config
from src.graph import build_graph
from src.graph_file import load_test_graph, parse_test_graph
from src.main import main
from src.resolver import PackageResolver, parse_requirement
from test_stage2 import make_archive


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ERROR_EXIT_CODE = 2
TREE = {"A": ("B", "C"), "B": ("D",), "C": ("D",), "D": ()}
APK_TEXT = (
    "P:root\nV:1.0-r0\nD:lib>=1.2 so:libc.so.1 !obsolete\n\n"
    "P:lib\nV:1.2-r0\nD:leaf\n\n"
    "P:lib\nV:1.10-r0\nD:leaf\n\n"
    "P:lib\nV:2.0_rc1-r0\nD:leaf\n\n"
    "P:leaf\nV:1.0-r0\n\n"
    "P:musl\nV:1.2.5-r0\np:so:libc.so.1=1\n"
)


def expected_breadth_first(source: dict, limit: int) -> tuple[dict, dict]:
    """Получить независимый эталон глубин и ребер с помощью BFS."""
    depths = {"A": 0}
    edges = {}
    queue = deque(["A"])
    while queue:
        node = queue.popleft()
        children = source[node] if depths[node] < limit else ()
        edges[node] = children
        for child in children:
            if child not in depths:
                depths[child] = depths[node] + 1
                queue.append(child)
    return edges, depths


def graph_from_bits(names: tuple, bits: tuple) -> dict:
    """Составить один из всех ориентированных графов на заданных узлах."""
    graph = {name: [] for name in names}
    for (source, target), enabled in zip(product(names, repeat=2), bits):
        if enabled:
            graph[source].append(target)
    return {name: tuple(children) for name, children in graph.items()}


def has_cycle_by_indegree(edges: dict) -> bool:
    """Проверить наличие цикла независимым алгоритмом удаления истоков."""
    indegree = {node: 0 for node in edges}
    for children in edges.values():
        for child in children:
            indegree[child] += 1
    queue = deque(node for node, count in indegree.items() if not count)
    removed = set()
    while queue:
        node = queue.popleft()
        removed.add(node)
        for child in edges[node]:
            indegree[child] -= 1
            if not indegree[child]:
                queue.append(child)
    return removed != set(edges)


def run_graph_cli(*arguments) -> tuple[int, str, str]:
    """Вызвать CLI третьего этапа и перехватить результат."""
    output, errors = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        code = main(list(arguments))
    return code, output.getvalue(), errors.getvalue()


class DfsTests(unittest.TestCase):
    """Проверки достижимого подграфа и правильного ограничения глубины."""

    def test_zero_depth_does_not_request_dependencies(self):
        """При глубине ноль оставить корень, не читая его зависимости."""
        neighbors = Mock(side_effect=AssertionError("must not be called"))
        graph = build_graph("A", neighbors, 0)
        self.assertEqual(graph.edges, {"A": ()})
        self.assertEqual(graph.depths, {"A": 0})
        neighbors.assert_not_called()

    def test_one_level_stops_at_direct_dependencies(self):
        """Не добавлять транзитивный D за пределом первого уровня."""
        graph = build_graph("A", TREE.__getitem__, 1)
        self.assertEqual(graph.edges, {"A": ("B", "C"), "B": (), "C": ()})

    def test_transitive_dependencies_and_shared_node(self):
        """Сохранить оба ребра к общей зависимости D без ложного цикла."""
        graph = build_graph("A", TREE.__getitem__, 3)
        self.assertEqual(graph.edges, TREE)
        self.assertEqual(graph.depths, {"A": 0, "B": 1, "D": 2, "C": 1})
        self.assertEqual(graph.cycles, [])

    def test_cycle_is_recorded_without_infinite_recursion(self):
        """Завершить обход A-B-C-A и сохранить замыкающее ребро."""
        source = {"A": ("B",), "B": ("C",), "C": ("A",)}
        graph = build_graph("A", source.__getitem__, 10000)
        self.assertEqual(graph.edges, source)
        self.assertEqual(graph.cycles, [("A", "B", "C", "A")])

    def test_self_cycle(self):
        """Обработать зависимость узла от самого себя."""
        graph = build_graph("A", {"A": ("A",)}.__getitem__, 3)
        self.assertEqual(graph.edges, {"A": ("A",)})
        self.assertEqual(graph.cycles, [("A", "A")])

    def test_shorter_path_reopens_truncated_node(self):
        """Раскрыть E повторно, если после длинного найден короткий путь."""
        source = {
            "A": ("B", "C"), "B": ("D",), "D": ("E",),
            "C": ("E",), "E": ("F",), "F": (),
        }
        graph = build_graph("A", source.__getitem__, 3)
        self.assertEqual(graph.depths["E"], 2)
        self.assertEqual(graph.depths["F"], 3)
        self.assertEqual(graph.edges["E"], ("F",))

    def test_all_three_node_graphs_match_independent_bfs(self):
        """Сверить DFS с BFS на 512 графах и четырех пределах глубины."""
        names = ("A", "B", "C")
        for bits in product((False, True), repeat=len(names) ** 2):
            source = graph_from_bits(names, bits)
            for limit in range(4):
                expected = expected_breadth_first(source, limit)
                graph = build_graph("A", source.__getitem__, limit)
                self.assertEqual((graph.edges, graph.depths), expected)
                self.assertEqual(
                    bool(graph.cycles), has_cycle_by_indegree(graph.edges)
                )

    def test_cycle_found_after_shorter_path_reopens_node(self):
        """Найти цикл D-E-D, даже если первый путь обрывался на E."""
        source = {"A": ("B", "C"), "B": ("D",), "C": ("E",),
                  "D": ("E",), "E": ("D",)}
        graph = build_graph("A", source.__getitem__, 3)
        self.assertEqual(graph.cycles, [("D", "E", "D")])

    def test_recursion_limit_is_reported(self):
        """Преобразовать предел рекурсии в понятную ошибку приложения."""
        with patch("src.graph._visit", side_effect=RecursionError):
            with self.assertRaisesRegex(RepositoryError, "рекурсии"):
                build_graph("A", TREE.__getitem__, 3)

    def test_invalid_depth(self):
        """Отклонить отрицательную, дробную и логическую глубину."""
        for limit in (-1, 1.5, True, "2"):
            with self.subTest(limit=limit):
                with self.assertRaises(RepositoryError):
                    build_graph("A", TREE.__getitem__, limit)

    def test_duplicate_edges_are_removed(self):
        """Не размножать повторно указанное ребро."""
        source = {"A": ("B", "B"), "B": ()}
        graph = build_graph("A", source.__getitem__, 2)
        self.assertEqual(graph.edges["A"], ("B",))


class GraphFileTests(unittest.TestCase):
    """Проверка формата A: B C и ошибок файлов тестового репозитория."""

    def test_spaces_comments_crlf_and_leaf(self):
        """Разрешить пробелы, комментарии, CRLF и пустой список у листа."""
        text = "# test\r\n\r\n A : B B\r\n B:\r\n"
        self.assertEqual(parse_test_graph(text), {"A": ("B",), "B": ()})

    def test_invalid_names_and_format(self):
        """Отклонить строчные буквы, цифры и неверный разделитель."""
        values = ("a:", "A1:", "A B", "A: B:C", ": A", "A: b")
        for text in values:
            with self.subTest(text=text):
                with self.assertRaisesRegex(RepositoryError, "строка"):
                    parse_test_graph(text)

    def test_empty_duplicate_or_undefined_nodes(self):
        """Сообщить о пустом файле, повторном узле и отсутствующем листе."""
        for text in ("", "# empty\n", "A:\nA:\n", "A: B"):
            with self.subTest(text=text):
                with self.assertRaises(RepositoryError):
                    parse_test_graph(text)

    def test_relative_file_is_resolved_from_config_directory(self):
        """Читать файл относительно TOML, а не текущего каталога."""
        base = PROJECT_ROOT / "examples/stage3"
        graph = load_test_graph("../graphs/diamond.txt", base)
        self.assertEqual(graph, TREE)

    def test_missing_invalid_or_large_file(self):
        """Обработать отсутствие файла, неверный UTF-8 и большой объем."""
        with TemporaryDirectory() as directory:
            base = Path(directory)
            with self.assertRaises(RepositoryError):
                load_test_graph("graph.txt", base)
            (base / "graph.txt").write_bytes(b"\xff")
            with self.assertRaises(RepositoryError):
                load_test_graph("graph.txt", base)
            (base / "graph.txt").write_text("A:\n", encoding="utf-8")
            with patch("src.graph_file.MAX_GRAPH_BYTES", 1):
                with self.assertRaisesRegex(RepositoryError, "размер"):
                    load_test_graph("graph.txt", base)


class VersionTests(unittest.TestCase):
    """Проверки числового сравнения версий APK и суффиксов выпуска."""

    def test_numeric_suffix_and_revision_order(self):
        """Сравнить числа, предварительные выпуски, суффиксы и ревизии."""
        pairs = (
            ("1.2", "1.10"), ("1.0_alpha", "1.0_beta"),
            ("1.0_beta", "1.0_pre"), ("1.0_pre", "1.0_rc1"),
            ("1.0_rc1", "1.0"), ("1.0", "1.0_p1"),
            ("1.0-r2", "1.0-r10"), ("1.01", "1.1"),
            ("1.001", "1.01"), ("1.0", "1.0a"),
        )
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertEqual(compare_versions(left, right), -1)
                self.assertEqual(compare_versions(right, left), 1)
                self.assertEqual(compare_versions(left, left), 0)

    def test_fuzzy_prefix(self):
        """Сопоставить начальные компоненты без строковой ошибки 1.2/1.20."""
        self.assertEqual(compare_versions("1.2.3-r2", "1.2", True), 0)
        self.assertEqual(compare_versions("1.20", "1.2", True), 1)

    def test_invalid_version(self):
        """Не сортировать неверные версии как произвольные строки."""
        for version in ("latest", "1..0", "1.0_bad", "1.0-rx", "9" * 18):
            with self.subTest(version=version):
                with self.assertRaises(RepositoryError):
                    version_tokens(version)


class ResolverTests(unittest.TestCase):
    """Выбор версий и пакетов, предоставляющих виртуальные зависимости."""

    def setUp(self):
        """Подготовить несколько версий библиотеки и виртуальный libc."""
        self.resolver = PackageResolver(parse_index(APK_TEXT))

    def test_latest_matching_version(self):
        """Выбрать по числам 1.10 вместо 1.2 при ограничении <2.0_rc1."""
        package = self.resolver.resolve("lib<2.0_rc1")
        self.assertEqual(package.version, "1.10-r0")

    def test_exact_version_and_virtual_provider(self):
        """Разрешить точную версию и namespace so через поле p."""
        self.assertEqual(self.resolver.resolve("lib=1.2-r0").version, "1.2-r0")
        self.assertEqual(self.resolver.resolve("so:libc.so.1=1").name, "musl")

    def test_provider_version_is_not_package_version(self):
        """Не сравнивать версию 1.2.5 пакета с требованием версии so=1."""
        with self.assertRaisesRegex(RepositoryError, "Не найдена"):
            self.resolver.resolve("so:libc.so.1=1.2.5-r0")

    def test_missing_or_incompatible_dependency(self):
        """Не скрывать отсутствующую или несовместимую зависимость."""
        for requirement in ("missing", "lib>=9", "lib~9"):
            with self.subTest(requirement=requirement):
                with self.assertRaisesRegex(RepositoryError, "Не найдена"):
                    self.resolver.resolve(requirement)

    def test_ambiguous_virtual_providers(self):
        """Сообщить о нескольких поставщиках вместо случайного выбора."""
        text = "P:a\nV:1\np:virtual\n\nP:b\nV:1\np:virtual"
        resolver = PackageResolver(parse_index(text))
        with self.assertRaisesRegex(RepositoryError, "Несколько поставщиков"):
            resolver.resolve("virtual")

    def test_real_name_has_priority(self):
        """Предпочесть собственное имя пакета виртуальному предоставлению."""
        text = "P:a\nV:1\n\nP:b\nV:2\np:a=2"
        resolver = PackageResolver(parse_index(text))
        self.assertEqual(resolver.resolve("a").name, "a")

    def test_unversioned_provider_cannot_satisfy_version(self):
        """Не считать отсутствие версии совпадением с любой версией."""
        resolver = PackageResolver(parse_index("P:a\nV:1\np:virtual"))
        self.assertEqual(resolver.resolve("virtual").name, "a")
        with self.assertRaises(RepositoryError):
            resolver.resolve("virtual>=1")

    def test_invalid_requirement(self):
        """Отклонить пустое имя, конфликты и незавершенное сравнение."""
        for text in ("", "!old", "lib>=", "two words", "lib@other"):
            with self.subTest(text=text):
                with self.assertRaises(RepositoryError):
                    parse_requirement(text)

    def test_unused_provider_does_not_block_other_packages(self):
        """Не проверять формат версии неиспользуемой виртуальной записи."""
        text = "P:a\nV:1\np:pc:unused=5.3.0_\n\nP:b\nV:1"
        resolver = PackageResolver(parse_index(text))
        self.assertEqual(resolver.resolve("b").name, "b")

    def test_declared_provider_priority(self):
        """Выбрать поставщика /bin/sh по полю k, как в Alpine."""
        text = ("P:a\nV:1\np:/bin/sh\nk:60\n\n"
                "P:b\nV:1\np:/bin/sh\nk:100")
        resolver = PackageResolver(parse_index(text))
        self.assertEqual(resolver.resolve("/bin/sh").name, "b")

    def test_provider_capability_version_has_priority(self):
        """Сравнить версии предоставления раньше приоритетов поставщиков."""
        text = ("P:a\nV:9\np:virtual=1\nk:100\n\n"
                "P:b\nV:1\np:virtual=2\nk:50")
        resolver = PackageResolver(parse_index(text))
        self.assertEqual(resolver.resolve("virtual").name, "b")

    def test_invalid_provider_priority(self):
        """Отклонить отрицательный или нечисловой приоритет k."""
        for value in ("-1", "text", "9" * 11):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RepositoryError, "приоритет"):
                    parse_index(f"P:a\nV:1\nk:{value}")


class Stage3CliTests(unittest.TestCase):
    """Объединение конфигурации, загрузки, DFS и вывода результатов."""

    def test_test_graph_does_not_access_network(self):
        """Построить локальный граф целиком без сетевых вызовов."""
        with patch("src.repository.urlopen") as opener:
            result = run_graph_cli("--config", "examples/stage3/diamond.toml")
        opener.assert_not_called()
        self.assertEqual((result[0], result[2]), (0, ""))
        self.assertIn("A -> B, C\n", result[1])
        self.assertIn("B -> D\n", result[1])
        self.assertNotIn("Цикл:", result[1])

    def test_cycle_and_depth_boundary_are_visible(self):
        """Вывести цикл и отличить границу глубины от листа."""
        cycle = run_graph_cli("--config", "examples/stage3/cycle.toml")
        self.assertIn("Цикл: A -> B -> C -> A", cycle[1])
        shallow = run_graph_cli("--config", "examples/stage3/depth0.toml")
        self.assertEqual(shallow[1], "Граф зависимостей (глубина 0):\n"
                         "A -> [предел глубины]\n")

    def test_apk_index_is_downloaded_once_for_entire_graph(self):
        """Получить транзитивный граф и виртуальный libc из одного индекса."""
        config = Config("root", "https://example.org", False, "1.0-r0", 3)
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive(APK_TEXT))) as opener:
            graph = analyze_graph(config, PROJECT_ROOT)
        opener.assert_called_once()
        self.assertIn("leaf@1.0-r0", graph.edges)
        self.assertIn("musl@1.2.5-r0", graph.edges)
        self.assertNotIn("obsolete", graph.edges)

    def test_remote_missing_dependency_is_not_queried_at_boundary(self):
        """Не разрешать зависимости узла, если достигнут предел глубины."""
        text = "P:zlib\nV:1.3.2-r0\nD:missing"
        config = Config("zlib", "https://example.org", False, "1.3.2-r0", 0)
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive(text))):
            graph = analyze_graph(config, PROJECT_ROOT)
        self.assertEqual(graph.edges, {"zlib@1.3.2-r0": ()})

    def test_remote_error_has_no_partial_output(self):
        """При неразрешимой зависимости вывести только понятную ошибку."""
        text = "P:zlib\nV:1.3.2-r0\nD:missing"
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive(text))):
            code, output, errors = run_graph_cli()
        self.assertEqual((code, output), (ERROR_EXIT_CODE, ""))
        self.assertIn("Не найдена зависимость", errors)
        self.assertNotIn("Traceback", errors)

    def test_missing_root_is_reported(self):
        """Отклонить исходный узел, которого нет в тестовом репозитории."""
        config = Config("Z", "../graphs/diamond.txt", True, "1.0", 2)
        with self.assertRaisesRegex(RepositoryError, "не найден"):
            analyze_graph(config, PROJECT_ROOT / "examples/stage3")

    def test_modes_are_mutually_exclusive(self):
        """Не принимать одновременно режимы первого и второго этапов."""
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as result:
                main(["--direct", "--show-config"])
        self.assertEqual(result.exception.code, ERROR_EXIT_CODE)

    def test_examples_in_separate_process(self):
        """Запустить примеры общих зависимостей, цикла и одиночного узла."""
        for name in ("diamond", "cycle", "isolated", "shorter_path"):
            with self.subTest(name=name):
                result = subprocess.run(
                    [sys.executable, "-m", "src.main", "--config",
                     f"examples/stage3/{name}.toml"],
                    cwd=PROJECT_ROOT, capture_output=True, text=True,
                    check=False, timeout=10,
                )
                self.assertEqual((result.returncode, result.stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()
