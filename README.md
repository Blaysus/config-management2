# Практическая работа № 2, вариант 22

Программа строит граф зависимостей пакетов Alpine Linux и печатает PlantUML.
Выполнены этапы 1–5. Нужен Python 3.11+, сборка и установка пакетов не нужны.
HTTP, TOML и архивы обрабатываются стандартной библиотекой Python.

## Запуск

Из корня проекта:

```sh
python3 -m src.main
python3 -m src.main --config examples/curl.toml
python3 -m src.main --config examples/musl.toml
```

По умолчанию stdout содержит только готовую диаграмму `@startuml ... @enduml`.
Ее можно сохранить в файл перенаправлением вывода. Режимы предыдущих этапов:

```sh
python3 -m src.main --show-config
python3 -m src.main --direct
python3 -m src.main --graph --config examples/stage3/diamond.toml
python3 -m src.main --reverse --config examples/stage4/diamond.toml
```

`./run.sh` принимает те же аргументы. При ошибке сообщение идет в stderr,
код завершения — 2, при успехе — 0. Незавершенная диаграмма не печатается.

## Настройки TOML

Все пять ключей обязательны, секции и лишние ключи не допускаются.

| Ключ | Назначение |
| --- | --- |
| `package_name` | Имя пакета; в тестовом графе — заглавные латинские буквы. |
| `repository` | HTTP(S)-адрес репозитория или путь к локальному файлу. |
| `test_mode` | `true` для файла, `false` для HTTP(S). |
| `package_version` | Точная версия APK, включая ревизию `-rN`. |
| `max_depth` | Целое число не меньше 0; глубина корня — 0. |

Примеры настроек находятся в `examples/`. Тестовый граф имеет вид `A: B C`;
каждый узел описывается отдельно, лист записывается как `D:`.
Путь отсчитывается от каталога TOML, версия в таком графе не используется.
Для `--direct` локальный файл имеет формат APKINDEX.

DFS учитывает глубину, общие узлы и циклы. На границе зависимости не читаются.
Обратный поиск идет по всему репозиторию, исходный пакет не входит в список.
Для APK учитываются версии, предоставления p и приоритет k; это анализ одного
репозитория, без установки и подбора совместимого набора для всей системы.
Отсутствующая версия не подменяется другой. Подпись индекса не проверяется.

## Примеры визуализации

Готовые тексты для zlib, curl и musl находятся в `examples/stage5/`.
[Сравнение с apk dot и объяснение различий](docs/stage5.md).
Зеленым отмечен корень, желтым — предел глубины, красными стрелками — циклы.
Для PNG дополнительно нужны Java и PlantUML:

```sh
mkdir -p output
curl -fL https://github.com/plantuml/plantuml/releases/latest/download/plantuml.jar \
  -o output/plantuml.jar
java -jar output/plantuml.jar -charset UTF-8 -tpng \
  -o ../../output/stage5 examples/stage5/*.puml
```

Изображения и jar сохраняются в `output/`, который исключен из Git.

## Тесты

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s tests -p 'test_stage5.py' -v
```

Тесты разделены по этапам и работают без внешней сети, Java и Docker.

## Функции

Исходники находятся в `src/`, тесты — в `tests/`. Назначение функций:

- `config.py`: `load_config` читает TOML, `parse_config` создает `Config`.
  `_validate_fields` проверяет ключи, `_require_text` — непустые строки;
  `_validate_package_name`, `_validate_package_version`, `_validate_max_depth` —
  имя, версию и глубину. `_validate_repository` выбирает `_validate_url`
  или `_validate_test_file` для проверки адреса или файла.
- `apk_index.py`: `_parse_fields` читает поля, `_provider_priority` проверяет k,
  `_make_package` создает пакет; `parse_index` разбирает записи,
  `find_package` ищет точную версию.
- `repository.py`: `build_index_url` строит адрес, `download_index` скачивает
  индекс, `_read_limited` ограничивает объем, `_decode_index` проверяет UTF-8.
  `_read_archive_member` читает APKINDEX, `extract_index` раскрывает gzip/tar,
  `read_test_index` читает файл, `load_packages` получает все записи,
  `get_package` возвращает пакет.
- `apk_versions.py`: `_number`, `_numeric_tokens`, `_main_tokens` разбирают числа
  и основную часть версии; `version_tokens` добавляет суффиксы и ревизию,
  `_different_types` сравнивает типы частей, `compare_versions` — версии целиком.
- `resolver.py`: `parse_requirement` разбирает требование, `package_key` подписывает
  узел, `_matches` проверяет версию. `_compare_providers`, `_choose_provider`
  выбирают поставщика; `PackageResolver.__init__`, `_add_provides` строят индекс,
  `matching_providers` отбирает кандидатов, `resolve` выбирает пакет,
  `neighbors` возвращает зависимости.
- `graph_file.py`: `_parse_line` читает строку, `_validate_targets` проверяет ссылки,
  `parse_test_graph` разбирает граф, `load_test_graph` читает файл.
- `graph.py`: `build_graph` запускает `_visit` — рекурсивный DFS;
  `_cycle_visit` находит циклы, `_record_cycle` сохраняет их в `DependencyGraph`.
- `reverse.py`: `reverse_edges` разворачивает граф, `apk_reverse_edges` строит
  обратные связи APK, `_unique_edges` убирает повторы.
- `analysis.py`: `analyze_graph` выбирает источник и направление обхода.
- `plantuml.py`: `_validate_graph`, `_validate_depths` проверяют данные,
  `_node_line` экранирует подпись и задает цвет, `_edge_lines` формирует стрелки,
  `to_plantuml` собирает диаграмму.
- `main.py`: `create_parser` задает аргументы, `main` запускает режим;
  `print_config`, `print_dependencies`, `print_graph`, `print_reverse`
  выводят результаты первых четырех этапов.

Описание каждого теста и его вспомогательных функций находится в docstring.
