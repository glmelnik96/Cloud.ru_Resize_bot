"""Import-architecture guards (contract tests).

Кодифицируют две границы нашей архитектуры (идея — Bannerzila
tests/contract/test_import_arch.py, правила — наши):

1. Граница LLM: SDK-клиенты (openai) живут только в llm/;
   произвольный HTTP (httpx/requests) — только в явно
   разрешённых модулях (llm/, hero_gen — контракт App1, urlfetch).
2. Детерминизм рендера: композер и его модули не импортируют
   время/случайность/сеть — одинаковый вход даёт одинаковый PNG.

Тесты читают исходники через AST, ничего не исполняют.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Каталоги, которые собираются в wheel (pyproject [tool.hatch]) — весь прод-код.
SOURCE_DIRS = ("llm", "graph", "agents", "infra", "app")

# --- правило 1: границы LLM/HTTP ---------------------------------------------

# openai SDK — только гейтвей Cloud.ru FM + type-only импорт в agent_runner.
OPENAI_ALLOWED = {
    "llm/cloudru.py",
    # from openai.types.chat import ChatCompletionMessageParam — только типы,
    # ни одного вызова API; клиент по-прежнему создаётся в llm/cloudru.py.
    "graph/agent_runner.py",
}

# Прямой HTTP — только там, где это осознанный сетевой контур.
HTTP_ALLOWED = {
    "llm/cloudru.py",
    "app/services/hero_gen.py",  # HTTP-контракт с App1 (hero-генерация)
    "infra/urlfetch.py",  # осознанный сетевой модуль (SSRF-guard внутри)
}

# --- правило 2: детерминизм рендера ------------------------------------------

# urlfetch сознательно вне списка: это сетевой модуль, а не рендер.
RENDER_MODULES = (
    "infra/composer.py",
    "infra/hero_fit.py",
    "infra/template_manifest.py",
)

NONDETERMINISM = {"time", "random", "secrets", "socket", "urllib", "requests", "httpx", "openai"}


def _imports(path: Path) -> set[str]:
    """Top-level имена всех импортов файла (включая вложенные в функции)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _py_files() -> list[Path]:
    files: list[Path] = []
    for d in SOURCE_DIRS:
        files.extend((REPO / d).rglob("*.py"))
    return files


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def test_helper_detects_toplevel_and_nested_imports(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "def f():\n"
        "    import httpx\n"
        "    from openai.types.chat import X\n",
        encoding="utf-8",
    )
    assert _imports(sample) == {"os", "pathlib", "httpx", "openai"}


def test_openai_only_in_allowed_modules():
    violations = [
        _rel(f) for f in _py_files() if "openai" in _imports(f) and _rel(f) not in OPENAI_ALLOWED
    ]
    assert violations == [], f"openai SDK вне гейтвея: {violations}"


def test_http_clients_only_in_allowed_modules():
    violations = [
        _rel(f)
        for f in _py_files()
        if _imports(f) & {"httpx", "requests"} and _rel(f) not in HTTP_ALLOWED
    ]
    assert violations == [], f"прямой HTTP вне разрешённых модулей: {violations}"


def test_render_is_deterministic():
    violations = {
        m: sorted(bad)
        for m in RENDER_MODULES
        if (bad := _imports(REPO / m) & NONDETERMINISM)
    }
    assert violations == {}, f"недетерминизм в рендере: {violations}"


def test_render_modules_exist():
    # Если модуль переедет, guard выше молча станет вакуумным — ловим это.
    missing = [m for m in RENDER_MODULES if not (REPO / m).is_file()]
    assert missing == [], f"рендер-модули не найдены (обнови RENDER_MODULES): {missing}"


def test_allowed_exceptions_still_exist():
    missing = [m for m in OPENAI_ALLOWED | HTTP_ALLOWED if not (REPO / m).is_file()]
    assert missing == [], f"allow-list указывает на несуществующие файлы: {missing}"
