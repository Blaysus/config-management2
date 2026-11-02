# Этап 5: примеры и сравнение с apk dot

Проверено 2 ноября 2026 года. Использован один снимок индекса
Alpine v3.20/main/x86_64 для обеих программ. Наш код читает индекс
самостоятельно; apk-tools вызван только для сравнения по заданию.

Исходные диаграммы: [zlib](../examples/stage5/zlib.puml),
[curl](../examples/stage5/curl.puml), [musl](../examples/stage5/musl.puml).
Все три отрисованы PlantUML 1.2026.9 с движком Smetana.
Готовые PNG лежат локально в `output/stage5/`, который исключен из Git.

## Условия

- Наши конфигурации: `examples/config.toml`, `examples/curl.toml`, `examples/musl.toml`.
- Максимальная глубина нашего обхода: 3; корень имеет глубину 0.
- Штатный инструмент: apk-tools 2.14.4, команда `apk dot`, архитектура x86_64.
- У apk dot в этом запуске нет ограничения глубины.
- Сравнивались логические узлы и уникальные пары концов ребер, а не оформление.

SHA-256 индекса APKINDEX.tar.gz:

```text
0efcc0b0838bcce24a677896864b6535a4a5586d64358027048ddc387ffe2b4e
```

Образ контейнера `alpine:3.20`:

```text
alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
```

## Результаты

| Пакет и версия | Наши узлы / ребра | apk dot: узлы / ребра |
| --- | --- | --- |
| zlib 1.3.2-r0 | 2 / 1 | 2 / 1 |
| curl 8.14.1-r2 | 15 / 30 | 22 / 48 |
| musl 1.2.5-r4 | 1 / 0 | 0 / 0, пустой вывод |

Для **zlib** графы совпали: `zlib -> musl`.

Для **curl** все 30 наших ребер присутствуют в выводе apk dot.
Его дополнительные 18 ребер объясняются двумя причинами:

1. Наш обход останавливается на глубине 3. Поэтому не раскрываются
   `busybox-binsh` и `libunistring`; в полном графе это добавляет 3 ребра.
   При глубине 100 наша программа получает 16 узлов и 33 ребра.
2. apk dot показывает альтернативных поставщиков `/bin/sh`, включая
   `dash-binsh` и `yash-binsh`, вместе с их зависимостями. Наша программа
   выбирает `busybox-binsh` по полю приоритета k: 100 против 60 и 50.
   Эти альтернативные ветви дают оставшиеся 15 ребер.

В сыром DOT для curl было 50 стрелок, но 48 уникальных пар узлов:
разные требования могут вести к одному пакету. Например, curl требует
libcurl по имени и по `so:libcurl.so.4`. В нашей диаграмме это одно ребро.

Для **musl** зависимости отсутствуют. В проверенном запуске apk dot
завершился с кодом 1 и ничего не напечатал. Наша программа показывает
один узел musl, чтобы пакет без зависимостей тоже был виден.

Различия `имя@версия` у нас и `имя-версия` в DOT, цвета, подписи требований
и расположение элементов не меняют структуру графа.
Сырые результаты штатного инструмента сохранены локально в
`output/stage5/native/`. Архив индекса, jar и картинки в Git не добавляются.

## Повторение сравнения

Сначала получить наш вывод:

```sh
python3 -m src.main --config examples/config.toml
python3 -m src.main --config examples/curl.toml
python3 -m src.main --config examples/musl.toml
```

Для штатного инструмента нужен Docker. Репозиторий копируется в контейнер
как локальный файл; после этого сеть контейнера отключена:

```sh
mkdir -p output/native-repo/x86_64 output/native-dot
curl -fL https://dl-cdn.alpinelinux.org/alpine/v3.20/main/x86_64/APKINDEX.tar.gz \
  -o output/native-repo/x86_64/APKINDEX.tar.gz
docker run --rm --platform linux/amd64 --network none \
  -v "$PWD/output/native-repo:/repo:ro" \
  -v "$PWD/output/native-dot:/result" alpine:3.20 sh -c '
    printf "/repo\n" > /etc/apk/repositories
    apk --allow-untrusted --no-network update || exit
    for pkg in zlib curl musl; do
      apk --allow-untrusted --no-network --from repositories dot "$pkg" \
        > "/result/$pkg.dot"
      printf "%s\n" "$?" > "/result/$pkg.exit"
    done'
```

Версии на сервере меняются. Для точного повторения чисел нужен индекс
с указанной контрольной суммой; для нового индекса сравнение следует повторить.
