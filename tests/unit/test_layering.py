"""The layering rules of ``docs/01-overview.md`` §4, enforced.

**Why this reads source instead of blocking imports.** The obvious implementation -- install an import
hook that refuses ``auto_inform.application``, then import ``auto_inform.domain.block`` -- cannot work.
Importing any submodule executes the package ``__init__`` first, and that module is the composition
root: it legitimately wires every layer together so that ``from auto_inform import Block`` works. Any
import of anything under this package therefore pulls the upper layers in, by design.

So the rules are checked statically, module by module. That is also the more useful check, because the
failure mode being guarded against is a stray ``import`` written into the wrong file, not a load-order
accident.

A negative control at the bottom proves the audit still objects when it should: without it, a typo that
made ``violations()`` always return an empty set would leave every test here passing while guarding
nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "auto_inform"

#: Nothing in domain/ may touch a layer above it, except under the documented TYPE_CHECKING exception.
DOMAIN_FORBIDDEN = (
    "auto_inform.application",
    "auto_inform.infrastructure",
    "auto_inform.rendering",
)

#: The exception rule 1 names: a type-only import of the two block contexts, and nothing else.
DOMAIN_TYPE_ONLY_ALLOWED = frozenset(
    {
        "auto_inform.application.context",
        "auto_inform.application.context.ReportContext",
        "auto_inform.application.context.RenderContext",
    }
)

#: application/ may reference the Settings *type* but must never reach for a concrete adapter, and has
#: no business knowing about rendering at all.
APPLICATION_FORBIDDEN = (
    "auto_inform.infrastructure.stores",
    "auto_inform.infrastructure.writers",
    "auto_inform.infrastructure.http_stub",
    "auto_inform.rendering",
)

#: rendering/ sits downstream of domain/ and the standard library only.
RENDERING_FORBIDDEN = (
    "auto_inform.application",
    "auto_inform.infrastructure",
)

#: blocks/ (stage 4) and reports/ are written by block authors: domain and rendering helpers only.
PLUGIN_FORBIDDEN = (
    "auto_inform.application",
    "auto_inform.infrastructure",
)


def _mentions_type_checking(test: ast.expr) -> bool:
    return any(
        (isinstance(node, ast.Name) and node.id == "TYPE_CHECKING")
        or (isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING")
        for node in ast.walk(test)
    )


def _guarded_import_ids(tree: ast.Module) -> set[int]:
    """Ids of import nodes that sit inside an ``if TYPE_CHECKING:`` block."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _mentions_type_checking(node.test):
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    guarded.add(id(child))
    return guarded


def _imported_modules(path: Path) -> tuple[set[str], set[str]]:
    """The ``(runtime, type_only)`` modules imported by one source file.

    ``from x import y`` records both ``x`` and ``x.y``, so a rule can name either the module or the
    individual name it cares about.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    guarded = _guarded_import_ids(tree)
    runtime: set[str] = set()
    type_only: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        target = type_only if id(node) in guarded else runtime
        if isinstance(node, ast.Import):
            target.update(alias.name for alias in node.names)
        elif node.module:
            target.add(node.module)
            target.update(f"{node.module}.{alias.name}" for alias in node.names)
    return runtime, type_only


def violations(path: Path, forbidden: tuple[str, ...]) -> set[str]:
    """Every runtime import in ``path`` that matches a forbidden prefix."""
    runtime, _ = _imported_modules(path)
    return {
        module
        for module in runtime
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden)
    }


def _layer_files(layer: str) -> list[Path]:
    directory = PACKAGE / layer
    return sorted(directory.glob("*.py")) if directory.is_dir() else []


#: Every layer with a rule, plus the files that must obey it. The composition root is deliberately
#: absent: it exists to wire the layers together, and that is the one place where every import is fine.
LAYERED_MODULES: list[tuple[str, Path, tuple[str, ...]]] = [
    *[("domain", path, DOMAIN_FORBIDDEN) for path in _layer_files("domain")],
    *[("application", path, APPLICATION_FORBIDDEN) for path in _layer_files("application")],
    *[("rendering", path, RENDERING_FORBIDDEN) for path in _layer_files("rendering")],
    *[("blocks", path, PLUGIN_FORBIDDEN) for path in _layer_files("blocks")],
    *[("reports", path, PLUGIN_FORBIDDEN) for path in _layer_files("reports")],
]


@pytest.mark.parametrize(
    ("layer", "path", "forbidden"),
    LAYERED_MODULES,
    ids=[f"{layer}/{path.name}" for layer, path, _ in LAYERED_MODULES],
)
def test_a_module_should_not_import_from_a_layer_above_it(
    layer: str, path: Path, forbidden: tuple[str, ...]
) -> None:
    # given every module in a layer with a documented dependency rule
    # when its runtime imports are checked against that rule
    # then none reaches upward. The layering is what keeps the domain testable without a network and
    # the application logic unaware of which database it is talking to
    assert violations(path, forbidden) == set(), (
        f"{layer}/{path.name} imports {sorted(violations(path, forbidden))}"
    )


def test_domain_should_import_upper_layers_only_for_the_documented_exception() -> None:
    # given the one exception rule 1 allows: block.py naming the two context types for its signatures
    allowed: set[str] = set()

    for path in _layer_files("domain"):
        _, type_only = _imported_modules(path)
        for module in type_only:
            if any(module.startswith(prefix) for prefix in DOMAIN_FORBIDDEN):
                allowed.add(module)

    # when every type-only upward import across the domain layer is collected
    # then it is exactly the documented set. A new one should fail this test and force the document to
    # be updated, because the exception is meant to be bounded and visible, not to accumulate
    assert allowed, "the exception should still exist; if it does not, delete it from docs/01 §4"
    unexpected = allowed - DOMAIN_TYPE_ONLY_ALLOWED
    assert not unexpected, f"undeclared type-only exception(s) in domain/: {sorted(unexpected)}"


def test_the_composition_root_should_import_the_layers_it_wires() -> None:
    # given the package root, which the parametrised test above deliberately exempts
    runtime, _ = _imported_modules(PACKAGE / "__init__.py")

    # when its imports are inspected
    # then it does reach into the layers, which is the point of a composition root -- and this assertion
    # documents that exemption rather than leaving it to look like an oversight
    assert any(module.startswith("auto_inform.application") for module in runtime)
    assert any(module.startswith("auto_inform.domain") for module in runtime)
    assert any(module.startswith("auto_inform.rendering") for module in runtime)


# --- negative control ---------------------------------------------------------


def test_the_layering_audit_should_flag_a_forbidden_import(tmp_path: Path) -> None:
    # given a module that breaks the rule in the most ordinary way possible
    offending = tmp_path / "domain_module.py"
    offending.write_text(
        "from auto_inform.infrastructure.stores.postgres_store import PostgresEventStore\n",
        encoding="utf-8",
    )

    # when the audit runs over it
    # then it objects. Without this, a bug that made violations() always return an empty set would let
    # every test above pass while enforcing nothing
    assert violations(offending, DOMAIN_FORBIDDEN) == {
        "auto_inform.infrastructure.stores.postgres_store",
        "auto_inform.infrastructure.stores.postgres_store.PostgresEventStore",
    }


def test_the_layering_audit_should_not_flag_a_type_only_import(tmp_path: Path) -> None:
    # given a module that names an upper-layer type the documented way
    tolerated = tmp_path / "domain_module.py"
    tolerated.write_text(
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from auto_inform.application.context import ReportContext\n",
        encoding="utf-8",
    )

    # when the audit runs over it
    # then it is content: a type annotation is erased at runtime and creates no dependency
    assert violations(tolerated, DOMAIN_FORBIDDEN) == set()
