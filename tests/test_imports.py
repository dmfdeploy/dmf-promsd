"""Which dmf_promsd modules may import which."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parent.parent / "src" / "dmf_promsd"
MODULES = sorted(path.stem for path in SOURCE.glob("*.py"))
assert MODULES, f"no modules found in {SOURCE}"

# What each module may import from the package, not what it imports today.
# "__init__" is the package itself.
MAY_IMPORT: dict[str, set[str]] = {
    "__init__": set(),
    "settings": set(),
    "observer": {"observer_metadata", "settings"},
    "observer_metadata": set(),
    "sd": set(),
    "netbox": set(),
    "cache": {"netbox", "sd"},
    "main": {"__init__", "cache", "netbox", "observer", "sd", "settings"},
}


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = ".".join(filter(None, ("dmf_promsd" if node.level else "", node.module)))
            names.update(f"{module}.{alias.name}" for alias in node.names)
    found: set[str] = set()
    for name in names:
        package, _, rest = name.partition(".")
        if package == "dmf_promsd":
            module = rest.split(".")[0]
            found.add(module if module in MODULES else "__init__")
    return found


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_only_what_it_may(module):
    assert module in MAY_IMPORT, f"{module}.py is missing from MAY_IMPORT"
    assert _imported_modules(SOURCE / f"{module}.py") <= MAY_IMPORT[module]
