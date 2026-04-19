"""Validate docs/openapi.yaml against the live Django route table.

Fails if:
  - the spec is not valid YAML or missing required OpenAPI fields,
  - any URL declared in `agent_on_demand.urls` is missing from the spec,
  - any (method, path) in the spec has no corresponding Django view.

Run: `uv run python -m scripts.validate_openapi`
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import django
import yaml
from django.urls import URLPattern, URLResolver

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "docs" / "openapi.yaml"

DECORATOR_METHODS = {
    "require_GET": {"GET"},
    "require_POST": {"POST"},
    "require_safe": {"GET", "HEAD"},
}

UUID_CONVERTER = re.compile(r"<uuid:(\w+)>")
GENERIC_CONVERTER = re.compile(r"<(?:\w+:)?(\w+)>")


def _setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(REPO_ROOT / "src"))
    django.setup()


def _django_path_to_openapi(pattern: str) -> str:
    out = UUID_CONVERTER.sub(r"{\1}", pattern)
    out = GENERIC_CONVERTER.sub(r"{\1}", out)
    return "/" + out.lstrip("/")


def _unwrap(view):
    """Walk `__wrapped__` down to the original user-defined function."""
    seen = set()
    while hasattr(view, "__wrapped__") and id(view) not in seen:
        seen.add(id(view))
        view = view.__wrapped__
    return view


def _methods_from_decorators(view) -> set[str]:
    """Read the decorator lines above the view def in the source file."""
    import inspect

    original = _unwrap(view)
    try:
        source, start = inspect.getsourcelines(original)
    except (OSError, TypeError):
        return set()
    try:
        module_src = inspect.getsource(inspect.getmodule(original))
    except (OSError, TypeError):
        return set()

    # `inspect.getsourcelines` on a decorated function starts at the first
    # decorator line, so scan forward from `start` collecting decorators until
    # the `def` line.
    lines = module_src.splitlines()
    methods: set[str] = set()
    for idx in range(start - 1, len(lines)):
        line = lines[idx].strip()
        if not line.startswith("@"):
            break
        for name, mset in DECORATOR_METHODS.items():
            if line.startswith(f"@{name}"):
                methods |= mset
        m = re.match(r"@require_http_methods\(\[([^\]]+)\]\)", line)
        if m:
            methods |= {
                v.strip().strip("\"'").upper() for v in m.group(1).split(",") if v.strip()
            }
    return methods


def _methods_from_body(view) -> set[str]:
    import inspect

    original = _unwrap(view)
    try:
        src = inspect.getsource(original)
    except (OSError, TypeError):
        return set()
    return {
        m.group(1).upper()
        for m in re.finditer(r'request\.method\s*(?:==|!=)\s*["\'](\w+)["\']', src)
    }


def _methods_for_view(view) -> set[str]:
    methods = _methods_from_decorators(view)
    if methods:
        return methods
    body = _methods_from_body(view)
    if body:
        return body
    # Fall back to GET if we truly can't tell; better to error loudly on mismatch
    # than to silently claim every method.
    return {"GET"}


def _collect_routes(patterns, prefix: str = "") -> list[tuple[str, set[str]]]:
    routes: list[tuple[str, set[str]]] = []
    for entry in patterns:
        if isinstance(entry, URLResolver):
            routes.extend(_collect_routes(entry.url_patterns, prefix + str(entry.pattern)))
        elif isinstance(entry, URLPattern):
            full = prefix + str(entry.pattern)
            path = _django_path_to_openapi(full)
            methods = _methods_for_view(entry.callback)
            routes.append((path, methods))
    return routes


def _load_spec() -> dict:
    try:
        with open(SPEC_PATH) as f:
            spec = yaml.safe_load(f)
    except FileNotFoundError:
        sys.exit(f"openapi spec not found at {SPEC_PATH}")
    except yaml.YAMLError as e:
        sys.exit(f"openapi spec is not valid YAML: {e}")

    if not isinstance(spec, dict):
        sys.exit("openapi spec root must be a mapping")
    for field in ("openapi", "info", "paths"):
        if field not in spec:
            sys.exit(f"openapi spec is missing required field: {field}")
    if not isinstance(spec["paths"], dict):
        sys.exit("openapi `paths` must be a mapping")
    return spec


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate docs/openapi.yaml against Django routes.")
    parser.add_argument("--export", metavar="PATH", help="Write openapi.yaml as JSON to PATH on success.")
    args = parser.parse_args()

    _setup_django()
    from agent_on_demand.urls import urlpatterns

    code_routes = _collect_routes(urlpatterns)
    code_by_path: dict[str, set[str]] = {}
    for path, methods in code_routes:
        code_by_path.setdefault(path, set()).update(methods)

    spec = _load_spec()
    spec_paths = spec["paths"]

    errors: list[str] = []
    http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}

    spec_by_path: dict[str, set[str]] = {}
    for path, entry in spec_paths.items():
        if not isinstance(entry, dict):
            errors.append(f"spec path {path!r} is not a mapping")
            continue
        methods = {m.upper() for m in entry.keys() if m.lower() in http_methods}
        spec_by_path[path] = methods

    for path, methods in code_by_path.items():
        spec_methods = spec_by_path.get(path)
        if spec_methods is None:
            errors.append(f"route {path!r} is in urls.py but missing from openapi.yaml")
            continue
        missing = methods - spec_methods
        if missing:
            errors.append(
                f"route {path!r} in urls.py serves {sorted(methods)} "
                f"but openapi.yaml only documents {sorted(spec_methods)} "
                f"(missing: {sorted(missing)})"
            )

    for path, methods in spec_by_path.items():
        code_methods = code_by_path.get(path)
        if code_methods is None:
            errors.append(f"spec documents path {path!r} but no such route in urls.py")
            continue
        extra = methods - code_methods
        if extra:
            errors.append(
                f"spec documents {sorted(extra)} on {path!r} "
                f"but urls.py only serves {sorted(code_methods)}"
            )

    if errors:
        print("openapi.yaml is out of sync with agent_on_demand.urls:\n", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"openapi.yaml OK ({len(code_by_path)} paths verified)")

    if args.export:
        export_path = Path(args.export)
        with open(SPEC_PATH) as f:
            spec_data = yaml.safe_load(f)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with open(export_path, "w") as f:
            json.dump(spec_data, f, indent=2)
        print(f"exported to {export_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
