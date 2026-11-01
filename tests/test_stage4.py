"""Этап 4: обратные зависимости, глубина, циклы и версии APK."""

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from itertools import product
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.analysis import analyze_graph
from src.apk_index import RepositoryError, parse_index
from src.config import Config
from src.graph import build_graph
from src.main import main
from src.resolver import PackageResolver
from src.reverse import apk_reverse_edges, reverse_edges
from test_stage2 import make_archive
from test_stage3 import expected_breadth_first, graph_from_bits


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ERROR_EXIT_CODE = 2
TREE = {"A": ("B", "C"), "B": ("D",), "C": ("D",), "D": ()}
APK_TEXT = (
    "P:lib\nV:1.0-r0\np:so:libshared.so.1=7\n\n"
    "P:lib\nV:2.0-r0\np:so:libshared.so.1=9\n\n"
    "P:app\nV:1.0-r0\nD:lib>=1.0-r0 so:libshared.so.1>=7\n\n"
    "P:old\nV:1.0-r0\nD:lib=1.0-r0\n\n"
    "P:new\nV:1.0-r0\nD:lib>=2.0-r0\n\n"
    "P:virtual\nV:1.0-r0\nD:so:libshared.so.1=7\n\n"
    "P:wrapper\nV:1.0-r0\nD:app\n\n"
    "P:conflict\nV:1.0-r0\nD:!lib\n\n"
    "P:outside\nV:1.0-r0\nD:missing\n"
)


def run_reverse(*arguments) -> tuple[int, str, str]:
    """Вызвать обратный режим с перехватом вывода и кода завершения."""
    output, errors = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        code = main(["--reverse", *arguments])
    return code, output.getvalue(), errors.getvalue()


def local_reverse(name: str, root: str, depth: int):
    """Построить обратный граф из одного из локальных примеров."""
    config = Config(root, f"../graphs/{name}.txt", True, "1.0-r0", depth)
    return analyze_graph(config, PROJECT_ROOT / "examples/stage4", reverse=True)


class ReverseGraphTests(unittest.TestCase):
    """Разворот ребер и повторное использование DFS третьего этапа."""

    def test_transpose_keeps_all_nodes(self):
        """Развернуть общие зависимости и сохранить исходный узел A."""
        self.assertEqual(reverse_edges(TREE), {
            "A": (), "B": ("A",), "C": ("A",), "D": ("B", "C"),
        })

    def test_reverse_search_uses_entire_repository(self):
        """Для листа D найти B, C и транзитивный A во всем репозитории."""
        graph = local_reverse("diamond", "D", 3)
        self.assertEqual(graph.edges, {
            "D": ("B", "C"), "B": ("A",), "A": (), "C": ("A",),
        })
        self.assertEqual(graph.depths, {"D": 0, "B": 1, "A": 2, "C": 1})

    def test_zero_depth(self):
        """При нулевой глубине оставить только исходный пакет."""
        graph = local_reverse("diamond", "D", 0)
        self.assertEqual(graph.edges, {"D": ()})
        self.assertEqual(graph.depths, {"D": 0})

    def test_one_level_excludes_transitive_dependents(self):
        """На первом уровне найти только B и C, без транзитивного A."""
        graph = local_reverse("diamond", "D", 1)
        self.assertEqual(graph.edges, {"D": ("B", "C"), "B": (), "C": ()})

    def test_no_dependents_even_with_forward_dependencies(self):
        """Для A не возвращать его собственные прямые зависимости B и C."""
        self.assertEqual(local_reverse("diamond", "A", 3).edges, {"A": ()})

    def test_isolated_node(self):
        """Сохранить изолированный узел без обратных зависимостей."""
        self.assertEqual(local_reverse("isolated", "A", 3).edges, {"A": ()})

    def test_cycle_terminates_and_preserves_reverse_edges(self):
        """Обойти цикл в обратном направлении без бесконечной рекурсии."""
        graph = local_reverse("cycle", "B", 10000)
        self.assertEqual(graph.edges, {
            "B": ("A",), "A": ("C",), "C": ("B",),
        })
        self.assertEqual(graph.cycles, [("B", "A", "C", "B")])

    def test_self_cycle(self):
        """Не потерять петлю при развороте графа."""
        graph = local_reverse("self_cycle", "A", 3)
        self.assertEqual(graph.edges, {"A": ("A",)})
        self.assertEqual(graph.cycles, [("A", "A")])

    def test_shared_dependent_has_minimum_depth(self):
        """Определить глубину общего зависимого узла по короткому пути."""
        graph = local_reverse("shorter_path", "F", 3)
        self.assertEqual(graph.depths["A"], 3)
        self.assertEqual(graph.depths["C"], 2)

    def test_all_three_node_graphs(self):
        """Сверить разворот 512 графов и ограниченный обход с эталонным BFS."""
        names = ("A", "B", "C")
        for bits in product((False, True), repeat=len(names) ** 2):
            source = graph_from_bits(names, bits)
            expected = {
                node: tuple(
                    parent for parent in names if node in source[parent]
                )
                for node in names
            }
            inverse = reverse_edges(source)
            self.assertEqual(inverse, expected)
            self.assertEqual(reverse_edges(inverse), source)
            for limit in range(4):
                graph = build_graph("A", inverse.__getitem__, limit)
                reference = expected_breadth_first(expected, limit)
                self.assertEqual((graph.edges, graph.depths), reference)

    def test_duplicate_edges_are_not_repeated(self):
        """Не повторять зависимый пакет при дублирующемся исходном ребре."""
        self.assertEqual(reverse_edges({"A": ("B", "B"), "B": ()}),
                         {"A": (), "B": ("A",)})

    def test_undefined_target_is_rejected(self):
        """Не скрывать ссылку на узел, который отсутствует в графе."""
        with self.assertRaisesRegex(RepositoryError, "не описан"):
            reverse_edges({"A": ("B",)})


class ReverseApkTests(unittest.TestCase):
    """Поиск зависимых пакетов по именам, версиям и виртуальным требованиям."""

    def setUp(self):
        """Собрать обратные связи двух версий библиотеки без сети."""
        self.edges = apk_reverse_edges(PackageResolver(parse_index(APK_TEXT)))

    def test_compatible_old_version_is_not_lost(self):
        """Учесть >=1 и для старой версии, даже если доступна версия 2."""
        self.assertEqual(self.edges["lib@1.0-r0"], (
            "app@1.0-r0", "old@1.0-r0", "virtual@1.0-r0",
        ))

    def test_version_constraints_filter_dependents(self):
        """Не включать требование =1 в обратные зависимости версии 2."""
        self.assertEqual(self.edges["lib@2.0-r0"], (
            "app@1.0-r0", "new@1.0-r0",
        ))

    def test_two_requirements_produce_one_dependent(self):
        """Не повторять пакет, зависящий от имени и so того же поставщика."""
        self.assertEqual(self.edges["lib@1.0-r0"].count("app@1.0-r0"), 1)

    def test_conflicts_are_not_dependents(self):
        """Не превращать !lib в положительную обратную зависимость."""
        self.assertNotIn("conflict@1.0-r0", self.edges["lib@1.0-r0"])

    def test_unrelated_missing_dependency_does_not_block_search(self):
        """Сохранить поиск по lib при отсутствии чужого пакета missing."""
        self.assertIn("outside@1.0-r0", self.edges)
        self.assertEqual(self.edges["outside@1.0-r0"], ())

    def test_all_matching_virtual_providers_are_considered(self):
        """Учесть зависимость у каждого подходящего поставщика virtual."""
        text = ("P:a\nV:1\np:virtual\n\nP:b\nV:1\np:virtual\n\n"
                "P:app\nV:1\nD:virtual")
        edges = apk_reverse_edges(PackageResolver(parse_index(text)))
        self.assertEqual(edges["a@1"], ("app@1",))
        self.assertEqual(edges["b@1"], ("app@1",))

    def test_unversioned_provide_does_not_satisfy_version(self):
        """Не включать >=1, если у виртуального предоставления нет версии."""
        text = "P:a\nV:2\np:virtual\n\nP:app\nV:1\nD:virtual>=1"
        edges = apk_reverse_edges(PackageResolver(parse_index(text)))
        self.assertEqual(edges["a@2"], ())

    def test_index_is_downloaded_once(self):
        """Построить транзитивный обратный граф из одной загрузки индекса."""
        config = Config("lib", "https://example.org", False, "1.0-r0", 3)
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive(APK_TEXT))) as opener:
            graph = analyze_graph(config, PROJECT_ROOT, reverse=True)
        opener.assert_called_once()
        self.assertEqual(graph.depths["wrapper@1.0-r0"], 2)
        self.assertNotIn("new@1.0-r0", graph.edges)

    def test_missing_root_version_is_reported(self):
        """Не подменять отсутствующую версию исходного пакета."""
        config = Config("lib", "https://example.org", False, "9.0-r0", 3)
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive(APK_TEXT))):
            with self.assertRaisesRegex(RepositoryError, "Доступные версии"):
                analyze_graph(config, PROJECT_ROOT, reverse=True)


class ReverseCliTests(unittest.TestCase):
    """Вывод четвертого этапа, ошибки и запуск демонстрационных примеров."""

    def test_reverse_output_and_depths(self):
        """Показать прямые и транзитивные обратные зависимости D."""
        with patch("src.repository.urlopen") as opener:
            code, output, errors = run_reverse(
                "--config", "examples/stage4/diamond.toml"
            )
        opener.assert_not_called()
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(output, "Обратные зависимости D (глубина 3):\n"
                         "B (глубина 1)\nA (глубина 2)\nC (глубина 1)\n")

    def test_empty_and_zero_depth_results(self):
        """Отличить пустой результат от ошибки и не выводить корень."""
        for name in ("depth0", "isolated", "no_dependents"):
            with self.subTest(name=name):
                code, output, errors = run_reverse(
                    "--config", f"examples/stage4/{name}.toml"
                )
                self.assertEqual((code, errors), (0, ""))
                self.assertIn("Обратных зависимостей в пределах", output)

    def test_self_cycle_does_not_list_root_as_dependent(self):
        """Указать петлю отдельно, не добавляя сам пакет в список."""
        code, output, errors = run_reverse(
            "--config", "examples/stage4/self_cycle.toml"
        )
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("Цикл: A -> A", output)
        self.assertNotIn("\nA (глубина", output)

    def test_cycle_does_not_list_root_as_dependent(self):
        """Вывести A и C для B, сохранив информацию о цикле."""
        code, output, errors = run_reverse(
            "--config", "examples/stage4/cycle.toml"
        )
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("A (глубина 1)", output)
        self.assertIn("C (глубина 2)", output)
        self.assertIn("Цикл: B -> A -> C -> B", output)
        self.assertNotIn("\nB (глубина", output)

    def test_modes_are_mutually_exclusive(self):
        """Отклонить конфликт обратного режима с другими режимами CLI."""
        for mode in ("--direct", "--show-config"):
            with self.subTest(mode=mode), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as result:
                    main(["--reverse", mode])
                self.assertEqual(result.exception.code, ERROR_EXIT_CODE)

    def test_malformed_graph_has_no_partial_output(self):
        """Сообщить о неверном графе без частичного списка и traceback."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "graph.txt").write_text("A: B\n", encoding="utf-8")
            config = root / "config.toml"
            config.write_text(
                'package_name = "A"\nrepository = "graph.txt"\n'
                'test_mode = true\npackage_version = "1.0"\nmax_depth = 3\n',
                encoding="utf-8",
            )
            code, output, errors = run_reverse("--config", str(config))
        self.assertEqual((code, output), (ERROR_EXIT_CODE, ""))
        self.assertIn("нет описания", errors)
        self.assertNotIn("Traceback", errors)

    def test_network_error_has_no_partial_output(self):
        """Обработать недоступность репозитория до вывода результатов."""
        with patch("src.repository.urlopen", side_effect=TimeoutError):
            code, output, errors = run_reverse()
        self.assertEqual((code, output), (ERROR_EXIT_CODE, ""))
        self.assertIn("Не удалось скачать", errors)
        self.assertNotIn("Traceback", errors)

    def test_invalid_requirement_has_no_partial_output(self):
        """Не маскировать поврежденное требование индекса под пустой список."""
        text = "P:zlib\nV:1.3.2-r0\n\nP:app\nV:1\nD:zlib>="
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive(text))):
            code, output, errors = run_reverse()
        self.assertEqual((code, output), (ERROR_EXIT_CODE, ""))
        self.assertIn("Некорректная зависимость", errors)
        self.assertNotIn("Traceback", errors)

    def test_examples_in_separate_process(self):
        """Проверить все локальные примеры через настоящую точку входа."""
        names = ("diamond", "depth0", "depth1", "cycle", "self_cycle",
                 "isolated", "no_dependents", "shorter_path")
        for name in names:
            with self.subTest(name=name):
                result = subprocess.run(
                    [sys.executable, "-m", "src.main", "--reverse", "--config",
                     f"examples/stage4/{name}.toml"],
                    cwd=PROJECT_ROOT, capture_output=True, text=True,
                    check=False, timeout=10,
                )
                self.assertEqual((result.returncode, result.stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()
