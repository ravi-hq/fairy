#!/usr/bin/env python3
"""Diff the Python SDK's endpoint coverage against docs/openapi.yaml.

Prints missing/extra (method, path) tuples and exits non-zero on drift. Run in
CI to keep clients/python in lockstep with the OpenAPI surface.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
OPENAPI = REPO_ROOT / "docs" / "openapi.yaml"
SDK_RESOURCES = REPO_ROOT / "clients" / "python" / "src" / "aod" / "resources"

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def load_openapi_endpoints() -> set[tuple[str, str]]:
    spec = yaml.safe_load(OPENAPI.read_text())
    out: set[tuple[str, str]] = set()
    for path, methods in spec.get("paths", {}).items():
        for method in methods:
            if method in HTTP_METHODS:
                out.add((method.upper(), path))
    return out


def _path_from_node(node: ast.AST) -> str | None:
    """Recover an HTTP path from a string literal or f-string arg."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{_}")
            else:
                return None
        return "".join(parts)
    return None


def _canonicalize(path: str) -> str:
    """Collapse any `{anything}` to `{_}` so openapi and SDK paths can match."""
    return re.sub(r"\{[^}]+\}", "{_}", path)


def load_sdk_endpoints() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for file in sorted(SDK_RESOURCES.glob("*.py")):
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            method_name = func.attr
            if method_name in HTTP_METHODS and node.args:
                path = _path_from_node(node.args[0])
                if path is not None:
                    out.add((method_name.upper(), path))
            elif method_name == "stream" and len(node.args) >= 2:
                if (
                    isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value.upper() in {m.upper() for m in HTTP_METHODS}
                ):
                    path = _path_from_node(node.args[1])
                    if path is not None:
                        out.add((node.args[0].value.upper(), path))
    return out


def main() -> int:
    spec_endpoints = {(m, _canonicalize(p)) for m, p in load_openapi_endpoints()}
    sdk_endpoints = {(m, _canonicalize(p)) for m, p in load_sdk_endpoints()}

    # /health is exposed as a method on Client, not in resources/. Allowlist it.
    spec_endpoints.discard(("GET", "/health"))

    missing = spec_endpoints - sdk_endpoints
    extra = sdk_endpoints - spec_endpoints

    if not missing and not extra:
        print(f"SDK parity OK ({len(sdk_endpoints)} endpoints covered)")
        return 0

    if missing:
        print("Endpoints in openapi.yaml but NOT in SDK:")
        for method, path in sorted(missing):
            print(f"  - {method} {path}")
    if extra:
        print("Endpoints in SDK but NOT in openapi.yaml:")
        for method, path in sorted(extra):
            print(f"  - {method} {path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
