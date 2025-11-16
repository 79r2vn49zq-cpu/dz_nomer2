import argparse
import os
import sys
import gzip
import urllib.request
from urllib.parse import urlparse
import re


# ============================
# VALIDATION (Этап 1)
# ============================

def validate_package(name: str):
    if not name or not name.strip():
        raise ValueError("Имя пакета не может быть пустым.")
    return name


def validate_repo(path: str):
    parsed = urlparse(path)

    # URL?
    if parsed.scheme and parsed.netloc:
        return path

    # Локальный файл?
    if not os.path.exists(path):
        raise ValueError(f"Указанный путь не существует: {path}")
    return path


def validate_test_mode(v: str):
    if v.lower() not in ["true", "false", "1", "0"]:
        raise ValueError("test-mode должен быть true/false.")
    return v.lower() in ["true", "1"]


def validate_depth(v: str):
    try:
        d = int(v)
    except:
        raise ValueError("max-depth должно быть числом.")
    if d < 1:
        raise ValueError("max-depth >= 1")
    return d


# ============================
# ЭТАП 2 — получение Packages.gz
# ============================

def download_packages(repo_url: str):
    url = repo_url.rstrip("/") + "/dists/jammy/main/binary-amd64/Packages.gz"
    print(f"Скачиваю Packages.gz:\n{url}\n")
    try:
        data = urllib.request.urlopen(url).read()
        text = gzip.decompress(data).decode("utf-8", errors="ignore")
        return text
    except Exception as e:
        print("[ОШИБКА]", e)
        sys.exit(1)


def extract_dependencies(package: str, packages_text: str):
    blocks = packages_text.split("\n\n")
    for block in blocks:
        if block.startswith("Package: " + package):
            for line in block.split("\n"):
                if line.startswith("Depends: "):
                    raw = line.replace("Depends: ", "").strip()
                    deps = [d.split(" ")[0].strip(",") for d in raw.split(",")]
                    return deps
            return []
    print(f"[ОШИБКА] Пакет '{package}' не найден.")
    sys.exit(1)


# ============================
# ЭТАП 3 — граф + DFS без рекурсии
# ============================

def load_test_graph(path: str):
    """
    Формат файла:
    A: B C
    B: C
    C: A
    D:
    """
    graph = {}
    with open(path, "r") as f:
        for line in f:
            if ":" not in line:
                continue
            pkg, deps = line.split(":", 1)
            pkg = pkg.strip()
            deps = deps.strip().split() if deps.strip() else []
            graph[pkg] = deps
    return graph


def build_graph(start_pkg: str, repo: str, test_mode: bool, max_depth: int):
    """
    Возвращает граф всех транзитивных зависимостей.
    DFS без рекурсии.
    """

    if test_mode:
        print("Режим тестирования включён.")
        full_graph = load_test_graph(repo)

        def get_deps(p):
            return full_graph.get(p, [])
    else:
        print("Используем реальный репозиторий.")
        packages_text = download_packages(repo)
        cache = {}

        def get_deps(pkg):
            if pkg in cache:
                return cache[pkg]
            d = extract_dependencies(pkg, packages_text)
            cache[pkg] = d
            return d

    result_graph = {}

    stack = [(start_pkg, 1)]
    visited = set()
    in_stack = set()

    while stack:
        node, depth = stack.pop()

        if depth > max_depth:
            continue
        if node in visited:
            continue
        if node in in_stack:
            print(f"[ЦИКЛ] Обнаружена циклическая зависимость в узле {node}.")
            continue

        in_stack.add(node)
        deps = get_deps(node)
        result_graph[node] = deps

        for dep in reversed(deps):
            stack.append((dep, depth + 1))

        in_stack.remove(node)
        visited.add(node)

    return result_graph


# ============================
# ЭТАП 4 — порядок загрузки зависимостей (топосорт)
# ============================

def compute_load_order(graph: dict, start: str):
    visited = set()
    in_stack = set()
    order = []

    # stack: (node, expanded?)
    stack = [(start, False)]

    while stack:
        node, expanded = stack.pop()

        if node in in_stack and not expanded:
            print(f"[ЦИКЛ] Обнаружена циклическая зависимость на {node}.")
            return None

        if node in visited:
            continue

        if expanded:
            order.append(node)
            visited.add(node)
            in_stack.discard(node)
            continue

        # Первый заход в узел
        in_stack.add(node)

        stack.append((node, True))  # вернуться после зависимостей

        for dep in graph.get(node, []):
            stack.append((dep, False))

    return list(reversed(order))


# ============================
# ЭТАП 5 — визуализация в Mermaid
# ============================

def _make_mermaid_id(name: str) -> str:
    """
    Превращаем имя пакета в безопасный идентификатор для Mermaid:
    - заменяем все странные символы на _
    - если начинается с цифры, добавляем префикс
    """
    safe = re.sub(r'[^0-9A-Za-z_]', '_', name)
    if not safe:
        safe = "pkg"
    if safe[0].isdigit():
        safe = "pkg_" + safe
    return safe


def graph_to_mermaid(graph: dict, root: str) -> str:
    lines = []
    lines.append("graph TD")

    # Узлы могут быть как ключами графа, так и только зависимостями
    all_nodes = set(graph.keys())
    for deps in graph.values():
        all_nodes.update(deps)

    id_map = {name: _make_mermaid_id(name) for name in all_nodes}

    root_id = id_map.get(root, _make_mermaid_id(root))
    lines.append(f'    {root_id}["{root}"]')

    emitted_edges = set()
    emitted_nodes = {root_id}

    for pkg, deps in graph.items():
        pkg_id = id_map[pkg]
        if pkg_id not in emitted_nodes:
            lines.append(f'    {pkg_id}["{pkg}"]')
            emitted_nodes.add(pkg_id)
        for dep in deps:
            dep_id = id_map[dep]
            if dep_id not in emitted_nodes:
                lines.append(f'    {dep_id}["{dep}"]')
                emitted_nodes.add(dep_id)
            edge_key = (pkg_id, dep_id)
            if edge_key in emitted_edges:
                continue
            emitted_edges.add(edge_key)
            lines.append(f"    {pkg_id} --> {dep_id}")

    # Немного стиля для корневого пакета
    lines.append(f"class {root_id} root;")
    lines.append('classDef root fill:#f9f,stroke:#333,stroke-width:2px;')

    return "\n".join(lines)


# ============================
# MAIN
# ============================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--test-mode", required=True)
    parser.add_argument("--max-depth", required=True)

    args = parser.parse_args()

    # ==== Этап 1: валидация ====
    try:
        package = validate_package(args.package)
        repo = validate_repo(args.repo)
        test_mode = validate_test_mode(args.test_mode)
        depth = validate_depth(args.max_depth)
    except ValueError as e:
        print("[ОШИБКА]", e)
        sys.exit(1)

    print("=== Этап 1: параметры ===")
    print(f"package = {package}")
    print(f"repo = {repo}")
    print(f"test_mode = {test_mode}")
    print(f"max_depth = {depth}")
    print()

    # ==== Этап 3: граф зависимостей ====
    print("=== Этап 3: построение графа зависимостей ===\n")
    graph = build_graph(package, repo, test_mode, depth)

    print("\nГраф зависимостей:")
    for pkg, deps in graph.items():
        print(f"{pkg}: {', '.join(deps) if deps else '(нет)'}")

    # ==== Этап 4: порядок загрузки ====
    print("\n=== Этап 4: Порядок загрузки зависимостей ===")
    load_order = compute_load_order(graph, package)

    if load_order is None:
        print("Корректный порядок загрузки невозможен из-за циклов.")
    else:
        print("Порядок загрузки:")
        for p in load_order:
            print(" →", p)

    # ==== Этап 5: Mermaid ====
    print("\n=== Этап 5: Mermaid-описание графа ===")
    mermaid = graph_to_mermaid(graph, package)
    print(mermaid)


if __name__ == "__main__":
    main()
