"""Разрешение требований APK в пакеты выбранного репозитория."""

from dataclasses import dataclass
from functools import cmp_to_key
import re

from .apk_index import Package, RepositoryError
from .apk_versions import EQUAL, GREATER, LESS, compare_versions


SINGLE_PROVIDER = 1
REQUIREMENT = re.compile(
    r"([^<>=~!\s@]+)(?:(<=|>=|=~|<~|>~|=|~|<|>)([^<>=~!\s@]+))?"
)
OPERATORS = {
    "=": {EQUAL}, "<": {LESS}, ">": {GREATER},
    "<=": {LESS, EQUAL}, ">=": {EQUAL, GREATER},
    "~": {EQUAL}, "=~": {EQUAL},
    "<~": {LESS, EQUAL}, ">~": {EQUAL, GREATER},
}


@dataclass(frozen=True)
class Requirement:
    """Имя зависимости, знак сравнения и требуемая версия."""

    name: str
    operator: str
    version: str


@dataclass(frozen=True)
class Provider:
    """Пакет и версия предоставляемой им возможности."""

    package: Package
    version: str | None


def parse_requirement(text: str) -> Requirement:
    """Разобрать имя, namespace и ограничение версии требования APK."""
    match = REQUIREMENT.fullmatch(text)
    if not match:
        raise RepositoryError(f"Некорректная зависимость APK: {text!r}")
    name, operator, version = match.groups()
    return Requirement(name, operator or "", version or "")


def package_key(package: Package) -> str:
    """Получить однозначную подпись узла с именем и версией."""
    return f"{package.name}@{package.version}"


def _matches(provider: Provider, requirement: Requirement) -> bool:
    """Проверить ограничение по версии пакета или предоставления."""
    if not requirement.operator:
        return True
    if provider.version is None:
        return False
    result = EQUAL
    if provider.version != requirement.version:
        result = compare_versions(
            provider.version, requirement.version, "~" in requirement.operator
        )
    return result in OPERATORS[requirement.operator]


def _compare_providers(left: Provider, right: Provider) -> int:
    """Сравнить версии возможности, пакета и объявленный приоритет k."""
    if left.version is not None and right.version is not None:
        result = EQUAL
        if left.version != right.version:
            result = compare_versions(left.version, right.version)
        if result != EQUAL:
            return result
    if left.package.name == right.package.name:
        return compare_versions(left.package.version, right.package.version)
    first = left.package.provider_priority
    second = right.package.provider_priority
    return (first > second) - (first < second)


def _choose_provider(candidates: list[Provider], text: str) -> Package:
    """Выбрать лучший вариант и отклонить равнозначных разных поставщиков."""
    best = max(candidates, key=cmp_to_key(_compare_providers))
    names = sorted({
        item.package.name for item in candidates
        if _compare_providers(item, best) == EQUAL
    })
    if len(names) > SINGLE_PROVIDER:
        raise RepositoryError(
            f"Несколько поставщиков {text}: {', '.join(names)}"
        )
    return best.package


class PackageResolver:
    """Индекс имен и виртуальных предоставлений, используемый DFS."""

    def __init__(self, packages: dict):
        """Добавить собственные имена пакетов и записи из поля p."""
        self.packages = {package_key(p): p for p in packages.values()}
        self.providers = {}
        for package in packages.values():
            own = Provider(package, package.version)
            self.providers.setdefault(package.name, []).append(own)
            self._add_provides(package)

    def _add_provides(self, package: Package) -> None:
        """Сохранить версию возможности отдельно от версии ее поставщика."""
        for text in package.provides:
            requirement = parse_requirement(text)
            if requirement.operator not in {"", "="}:
                raise RepositoryError(f"Некорректное предоставление: {text}")
            provider = Provider(package, requirement.version or None)
            self.providers.setdefault(requirement.name, []).append(provider)

    def resolve(self, text: str) -> Package:
        """Выбрать подходящую версию; неоднозначные имена не угадывать."""
        requirement = parse_requirement(text)
        candidates = [
            item for item in self.providers.get(requirement.name, ())
            if _matches(item, requirement)
        ]
        direct = [p for p in candidates if p.package.name == requirement.name]
        candidates = direct or candidates
        if not candidates:
            raise RepositoryError(f"Не найдена зависимость: {text}")
        return _choose_provider(candidates, text)

    def neighbors(self, key: str) -> tuple[str, ...]:
        """Получить реальные узлы всех прямых положительных зависимостей."""
        package = self.packages[key]
        return tuple(
            package_key(self.resolve(dep)) for dep in package.dependencies
        )
