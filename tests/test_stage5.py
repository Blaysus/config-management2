"""Этап 5: синтаксис PlantUML, полнота графа, экранирование и CLI."""

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
from itertools import combinations, product
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.apk_index import RepositoryError
from src.graph import DependencyGraph, build_graph
from src.main import main
from src.plantuml import to_plantuml
from test_stage2 import make_archive
from test_stage3 import graph_from_bits


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ERROR_EXIT_CODE = 2
NODE_LINE = re.compile(r'^rectangle ("(?:[^"\\]|\\.)*") as (n\d+) ')
EDGE_LINE = re.compile(r'^(n\d+) (?:-->|-\[#[A-Fa-f0-9]+\]->) (n\d+)$')
APK_TEXT = (
    "P:zlib\nV:1.3.2-r0\nD:so:libc.musl-x86_64.so.1\n\n"
    "P:musl\nV:1.2.5-r4\np:so:libc.musl-x86_64.so.1=1\n"
)


def diagram_graph(text: str) -> tuple[dict, set]:
    """Восстановить узлы и ребра из сформированного текста диаграммы."""
    labels = {}
    edges = set()
    for line in text.splitlines():
        node = NODE_LINE.match(line)
        if node:
            label = json.loads(node[1]).removesuffix("\n[предел глубины]")
            labels[node[2]] = label
        edge = EDGE_LINE.fullmatch(line)
        if edge:
            edges.add((edge[1], edge[2]))
    return labels, {(labels[parent], labels[child]) for parent, child in edges}


def run_diagram(*arguments) -> tuple[int, str, str]:
    """Вызвать новый режим по умолчанию с перехватом обоих потоков."""
    output, errors = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        code = main(list(arguments))
    return code, output.getvalue(), errors.getvalue()


class PlantUmlTests(unittest.TestCase):
    """Проверка текстового представления без Java и сетевых запросов."""

    def test_complete_diagram_with_all_nodes_and_edges(self):
        """Включить корень, общий лист и все ребра исходного графа."""
        source = {"A": ("B", "C"), "B": ("D",), "C": ("D",), "D": ()}
        graph = build_graph("A", source.__getitem__, 3)
        text = to_plantuml(graph, 3)
        labels, edges = diagram_graph(text)
        self.assertTrue(text.startswith("@startuml\n"))
        self.assertTrue(text.endswith("@enduml\n"))
        self.assertEqual(set(labels.values()), set(source))
        self.assertEqual(edges, {("A", "B"), ("A", "C"),
                                 ("B", "D"), ("C", "D")})

    def test_isolated_package_is_visible(self):
        """Объявить пакет без зависимостей отдельным узлом диаграммы."""
        graph = build_graph("musl@1.2.5-r4", lambda node: (), 3)
        labels, edges = diagram_graph(to_plantuml(graph, 3))
        self.assertEqual(set(labels.values()), {"musl@1.2.5-r4"})
        self.assertEqual(edges, set())

    def test_depth_zero_keeps_only_root(self):
        """При нулевой глубине оставить корень с отметкой границы."""
        graph = build_graph("A", {"A": ("B",), "B": ()}.__getitem__, 0)
        text = to_plantuml(graph, 0)
        labels, edges = diagram_graph(text)
        self.assertEqual(set(labels.values()), {"A"})
        self.assertEqual(edges, set())
        self.assertIn('[предел глубины]', text)
        self.assertIn('#FFF2CC', text)

    def test_frontier_is_distinguished_from_leaf(self):
        """Отметить границу отдельно от листа с известным пустым списком."""
        source = {"A": ("B",), "B": ()}
        graph = build_graph("A", source.__getitem__, 1)
        self.assertIn('"B\\n[предел глубины]"', to_plantuml(graph, 1))
        graph = build_graph("A", source.__getitem__, 3)
        self.assertNotIn('[предел глубины]', to_plantuml(graph, 3))

    def test_cycle_edges_are_colored(self):
        """Сохранить направление и выделить все ребра найденного цикла."""
        source = {"A": ("B",), "B": ("C",), "C": ("A",)}
        graph = build_graph("A", source.__getitem__, 5)
        text = to_plantuml(graph, 5)
        self.assertEqual(text.count('-[#C0504D]->'), 3)
        self.assertEqual(diagram_graph(text)[1], {("A", "B"),
                                                ("B", "C"), ("C", "A")})

    def test_self_cycle(self):
        """Вывести петлю одного узла как ребро, а не повторный узел."""
        graph = build_graph("A", {"A": ("A",)}.__getitem__, 3)
        labels, edges = diagram_graph(to_plantuml(graph, 3))
        self.assertEqual(set(labels.values()), {"A"})
        self.assertEqual(edges, {("A", "A")})

    def test_aliases_do_not_collide_for_package_names(self):
        """Различать знаки +, -, _, @ в именах и версиях пакетов."""
        nodes = ("libstdc++@1.0-r0", "a-b@1", "a_b@1")
        source = {nodes[0]: nodes[1:], nodes[1]: (), nodes[2]: ()}
        graph = build_graph(nodes[0], source.__getitem__, 3)
        self.assertEqual(set(diagram_graph(to_plantuml(graph, 3))[0].values()),
                         set(nodes))

    def test_quotes_backslashes_and_newlines_are_escaped(self):
        """Не превращать содержимое подписи в отдельные команды PlantUML."""
        label = 'pkg"\\name\n@enduml\n!include remote'
        graph = build_graph(label, lambda node: (), 3)
        text = to_plantuml(graph, 3)
        labels, edges = diagram_graph(text)
        self.assertEqual(set(labels.values()), {label})
        self.assertEqual(edges, set())
        self.assertEqual(text.splitlines().count('@enduml'), 1)
        self.assertNotIn('\n!include remote', text)

    def test_output_is_stable_for_dictionary_order(self):
        """Не менять результат при другом порядке узлов и ребер словаря."""
        first = DependencyGraph("A", {"A": ("C", "B"), "B": (), "C": ()},
                                {"A": 0, "B": 1, "C": 1})
        second = DependencyGraph("A", {"C": (), "B": (), "A": ("B", "C")},
                                 {"C": 1, "B": 1, "A": 0})
        self.assertEqual(to_plantuml(first, 3), to_plantuml(second, 3))

    def test_duplicate_edges_are_emitted_once(self):
        """Не дублировать одинаковые стрелки при повторном ребре."""
        graph = DependencyGraph("A", {"A": ("B", "B"), "B": ()},
                                {"A": 0, "B": 1})
        self.assertEqual(to_plantuml(graph, 3).count('n0 --> n1'), 1)

    def test_invalid_graph_is_rejected(self):
        """Отклонить потерянные узлы, глубины и исходный пакет."""
        values = (
            DependencyGraph("A"),
            DependencyGraph("A", {"A": ()}, {}),
            DependencyGraph("A", {"A": ("B",)}, {"A": 0}),
            DependencyGraph("A", {"A": ()}, {"A": 1}),
            DependencyGraph("A", {"A": (), "B": ()}, {"A": 0, "B": -1}),
            DependencyGraph("A", {"A": (), "B": ()}, {"A": 0, "B": 4}),
            DependencyGraph("A", {"A": (), "B": ()}, {"A": 0, "B": True}),
        )
        for graph in values:
            with self.subTest(graph=graph):
                with self.assertRaises(RepositoryError):
                    to_plantuml(graph, 3)

    def test_invalid_maximum_depth_is_rejected(self):
        """Не принимать отрицательный, дробный или логический предел."""
        graph = build_graph("A", lambda node: (), 0)
        for limit in (-1, 1.5, True, "3"):
            with self.subTest(limit=limit):
                with self.assertRaises(RepositoryError):
                    to_plantuml(graph, limit)

    def test_all_three_node_graphs_preserve_structure(self):
        """Проверить полноту диаграммы для всех 512 графов из трех узлов."""
        names = ("A", "B", "C")
        for bits in product((False, True), repeat=len(names) ** 2):
            graph = build_graph(
                "A", graph_from_bits(names, bits).__getitem__, 3
            )
            labels, edges = diagram_graph(to_plantuml(graph, 3))
            expected = {(a, b) for a, children in graph.edges.items()
                        for b in children}
            self.assertEqual(set(labels.values()), set(graph.edges))
            self.assertEqual(edges, expected)


class PlantUmlCliTests(unittest.TestCase):
    """Новый режим по умолчанию и совместимость с предыдущими этапами."""

    def test_default_output_is_only_plantuml(self):
        """Вывести завершенный PlantUML без посторонних строк в stdout."""
        with patch("src.repository.urlopen",
                   return_value=BytesIO(make_archive(APK_TEXT))) as opener:
            code, output, errors = run_diagram()
        opener.assert_called_once()
        self.assertEqual((code, errors), (0, ""))
        self.assertTrue(output.startswith('@startuml\n'))
        self.assertTrue(output.endswith('@enduml\n'))
        self.assertEqual(diagram_graph(output)[1],
                         {("zlib@1.3.2-r0", "musl@1.2.5-r4")})

    def test_local_example_needs_no_network(self):
        """Сформировать диаграмму локального графа без HTTP-запросов."""
        with patch("src.repository.urlopen") as opener:
            code, output, errors = run_diagram(
                "--config", "examples/stage3/diamond.toml"
            )
        opener.assert_not_called()
        self.assertEqual((code, errors), (0, ""))
        self.assertEqual(set(diagram_graph(output)[0].values()), set('ABCD'))

    def test_graph_flag_preserves_stage_three_output(self):
        """Сохранить текстовый граф третьего этапа под флагом --graph."""
        code, output, errors = run_diagram(
            "--graph", "--config", "examples/stage3/diamond.toml"
        )
        self.assertEqual((code, errors), (0, ""))
        self.assertIn('A -> B, C', output)
        self.assertNotIn('@startuml', output)

    def test_all_modes_are_mutually_exclusive(self):
        """Не смешивать PlantUML с несовместимыми режимами вывода."""
        modes = ('--show-config', '--direct', '--graph', '--reverse')
        for pair in combinations(modes, 2):
            with self.subTest(pair=pair), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as result:
                    main(list(pair))
                self.assertEqual(result.exception.code, ERROR_EXIT_CODE)

    def test_error_does_not_leave_partial_diagram(self):
        """При сбое загрузки не начинать печатать незавершенную диаграмму."""
        with patch("src.repository.urlopen", side_effect=TimeoutError):
            code, output, errors = run_diagram()
        self.assertEqual((code, output), (ERROR_EXIT_CODE, ""))
        self.assertIn('Ошибка:', errors)
        self.assertNotIn('Traceback', errors)

    def test_serializer_error_has_no_traceback(self):
        """Преобразовать поврежденный граф в понятное сообщение CLI."""
        with patch("src.main.analyze_graph", return_value=DependencyGraph('A')):
            code, output, errors = run_diagram()
        self.assertEqual((code, output), (ERROR_EXIT_CODE, ""))
        self.assertIn('PlantUML:', errors)
        self.assertNotIn('Traceback', errors)

    def test_process_can_redirect_output_to_puml(self):
        """Получить готовый файл диаграммы обычным перенаправлением stdout."""
        with TemporaryDirectory() as directory:
            destination = Path(directory) / 'graph.puml'
            with destination.open('w', encoding='utf-8') as output:
                result = subprocess.run(
                    [sys.executable, '-m', 'src.main', '--config',
                     'examples/stage3/cycle.toml'],
                    cwd=PROJECT_ROOT, stdout=output, stderr=subprocess.PIPE,
                    text=True, check=False, timeout=10,
                )
            text = destination.read_text(encoding='utf-8')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertTrue(text.startswith('@startuml\n'))
        self.assertEqual(text.count('-[#C0504D]->'), 3)

    @unittest.skipIf(os.name == 'nt', 'run.sh предназначен для POSIX')
    def test_run_script_from_another_directory(self):
        """Сохранить запуск диаграмм через run.sh из другого каталога."""
        with TemporaryDirectory() as directory:
            config = PROJECT_ROOT / 'examples/stage3/isolated.toml'
            result = subprocess.run(
                ['sh', str(PROJECT_ROOT / 'run.sh'), '--config', str(config)],
                cwd=directory, capture_output=True, text=True, check=False,
                env=os.environ | {'PYTHON': sys.executable}, timeout=10,
            )
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(set(diagram_graph(result.stdout)[0].values()), {'A'})


if __name__ == '__main__':
    unittest.main()
