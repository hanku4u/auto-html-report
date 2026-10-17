"""The layering rules of ``docs/01-overview.md`` §4, enforced.

**Why this reads source instead of blocking imports.** The obvious implementation -- install an import
hook that refuses ``auto_inform.application``, then import ``auto_inform.domain.block`` -- cannot work.
Importing any submodule executes the package ``__init__`` first, and that module is the composition
root: it legitimately wires every layer together so that ``from auto_inform import Block`` works. Any
import of anything under this package therefore pulls the upper layers in, by design.

So the rules are checked statically, module by module. That is also the more useful check, because the
failure mode being guarded against is a stray ``import`` written into the wrong file, not a load-order
accident.

**Two styles of rule, deliberately.** Most layers use a list of forbidden prefixes, because most of the
world is permitted to them. ``application/`` uses an *allowlist* instead, because its rule is a general
prohibition -- "never a concrete adapter" -- and a denylist cannot express that: a newly added adapter
would simply not be on the list, and would be permitted by default. An earlier version of this audit made
exactly that mistake, and a probe confirmed ``from auto_inform.infrastructure.clock import SystemClock``
passed it silently, even though ``clock.py`` is a file the layout tree already plans.

**Relative imports are resolved, not skipped.** An earlier version read ``ImportFrom.module`` without
``ImportFrom.level``, so ``from ..application.context import ...`` recorded the bare name
``application.context``, matched no forbidden prefix, and passed. A relative import that climbs *past*
the package root is reported outright, because it cannot be resolved to a name any rule could judge.

**Type-only imports are not judged; runtime imports are.** An annotation erased at runtime creates no
dependency, which is the principle rule 1 states for the domain. The difference is that the domain's one
type-only upward import is *enumerated* below, so that exception stays visible instead of accumulating.

Every control at the bottom is a negative control: a probe that a broken audit would have to catch. An
audit that silently stops objecting is worse than no audit.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "auto_inform"

#: The import root: the directory that *contains* the top-level package, and therefore the base a
#: relative import is resolved against. Not the package itself -- ``from . import x`` inside
#: ``auto_inform/domain/`` means ``auto_inform.domain.x``, which only the parent directory can tell us.
IMPORT_ROOT = PACKAGE.parent

#: Nothing in domain/ may touch a layer above it at runtime, except under the documented exception.
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

#: Rule 2 is a general prohibition, so it is enforced as an allowlist: domain, its own layer, the
#: Settings *value* type, and the shared private helpers at the package root. Everything else under
#: ``auto_inform`` is off limits -- in particular every concrete adapter, and rendering -- so a newly
#: added adapter is forbidden by default rather than by somebody remembering to extend a tuple.
#:
#: Note what is *not* here: the bare ``auto_inform``. Adding it to permit ``from auto_inform import
#: _naming`` was tried and reverted, because a prefix entry for the root matches every submodule under
#: it -- so it permitted ``rendering`` and every adapter, which a probe caught immediately. The root is
#: a container rather than a dependency, and ``_read_imports`` therefore does not record it when names
#: are imported from it; the submodule that carries the real dependency is recorded instead.
APPLICATION_ALLOWED = (
    "auto_inform.domain",
    "auto_inform.application",
    "auto_inform.infrastructure.settings",
    "auto_inform._naming",
    "auto_inform._version",
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


@dataclass(frozen=True)
class Rule:
    """One layer's rule, and the style it is expressed in. Exactly one of the two is set."""

    layer: str
    forbidden: tuple[str, ...] = ()
    allowed: tuple[str, ...] | None = None


#: The composition root is deliberately absent: it exists to wire the layers together, and that is the
#: one place where every import is fine.
RULES: dict[str, Rule] = {
    "domain": Rule("domain", forbidden=DOMAIN_FORBIDDEN),
    "application": Rule("application", allowed=APPLICATION_ALLOWED),
    "rendering": Rule("rendering", forbidden=RENDERING_FORBIDDEN),
    "blocks": Rule("blocks", forbidden=PLUGIN_FORBIDDEN),
    "reports": Rule("reports", forbidden=PLUGIN_FORBIDDEN),
}


@dataclass(frozen=True)
class ModuleImports:
    """What one source file imports, split by how it imports it."""

    runtime: set[str]
    type_only: set[str]
    #: Imports that cannot be judged by name -- currently only relative imports climbing past the
    #: import root. Reported as violations whatever the rule style, since no rule can allow or deny
    #: something it cannot name.
    unjudgeable: set[str] = field(default_factory=set)


def _matches(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def _package_parts(path: Path, import_root: Path) -> tuple[str, ...] | None:
    """The dotted package a file belongs to, or ``None`` when it is not under ``import_root``."""
    try:
        parts = path.resolve().relative_to(import_root.resolve()).with_suffix("").parts
    except ValueError:
        return None
    return parts[:-1]


def _escaping_relative_import(
    node: ast.ImportFrom, package_parts: tuple[str, ...] | None
) -> str | None:
    """A description of a relative import that climbs above the import root, if it does.

    ``from ...something import x`` inside ``auto_inform/domain/`` reaches past the top-level package,
    which Python refuses at runtime. The audit refuses it too, rather than resolving it to a bare name
    that matches no rule and therefore passes silently -- which is exactly the failure mode this audit
    was rewritten to eliminate.
    """
    if node.level == 0 or package_parts is None:
        return None
    if len(package_parts) - (node.level - 1) >= 1:
        return None
    return f"relative import beyond the import root: {'.' * node.level}{node.module or ''}"


def _resolve_relative(node: ast.ImportFrom, package_parts: tuple[str, ...] | None) -> str | None:
    """The absolute module name a relative import refers to, or the raw name if it cannot be resolved.

    ``level`` counts packages upward from the file's own package: 1 is the current package, 2 its
    parent, and so on. A file that is not under the import root has no package identity to count from --
    which is why the controls below build a minimal package tree rather than using a loose file.
    """
    if node.level == 0 or package_parts is None:
        return node.module

    base_parts = package_parts[: len(package_parts) - (node.level - 1)]
    base = ".".join(base_parts)
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base or None


def _direct_import_ids(statements: list[ast.stmt]) -> set[int]:
    """Ids of import statements directly in ``statements``, not inside a function or class body.

    Deliberately shallow. An import nested inside a function *defined within* a ``TYPE_CHECKING`` block
    is not a type annotation, so it must not be excused as one -- treating it as runtime is the
    conservative choice, and an earlier version of this audit excused it.
    """
    ids: set[int] = set()
    for statement in statements:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            ids.add(id(statement))
        elif isinstance(statement, ast.If):
            ids |= _direct_import_ids(statement.body)
            ids |= _direct_import_ids(statement.orelse)
    return ids


def _mentions_type_checking(test: ast.expr) -> bool:
    return any(
        (isinstance(node, ast.Name) and node.id == "TYPE_CHECKING")
        or (isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING")
        for node in ast.walk(test)
    )


def _guarded_import_ids(tree: ast.Module) -> set[int]:
    """Ids of import nodes that are type-only: directly inside an ``if TYPE_CHECKING:`` block."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _mentions_type_checking(node.test):
            guarded |= _direct_import_ids(node.body)
    return guarded


def _read_imports(path: Path, *, import_root: Path = IMPORT_ROOT) -> ModuleImports:
    """Everything one source file imports. Parses the file once.

    ``from x import y`` records both ``x`` and ``x.y``, so a rule can name either the module or the
    individual name it cares about.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    guarded = _guarded_import_ids(tree)
    package_parts = _package_parts(path, import_root)

    runtime: set[str] = set()
    type_only: set[str] = set()
    unjudgeable: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        target = type_only if id(node) in guarded else runtime
        if isinstance(node, ast.Import):
            target.update(alias.name for alias in node.names)
            continue

        resolved = _resolve_relative(node, package_parts)
        if resolved:
            # The package root itself is a container, not a dependency: ``from auto_inform import
            # _naming`` depends on ``auto_inform._naming``, which is recorded just below. Recording the
            # bare root as well would make any allowlist entry for it match every submodule by prefix,
            # which is the opposite of what an allowlist is for. A bare ``import auto_inform`` is still
            # recorded, and still a violation for a layer that may not import the composition root.
            if resolved != "auto_inform":
                target.add(resolved)
            target.update(f"{resolved}.{alias.name}" for alias in node.names)

        escape = _escaping_relative_import(node, package_parts)
        if escape:
            unjudgeable.add(escape)

    return ModuleImports(runtime=runtime, type_only=type_only, unjudgeable=unjudgeable)


def violations(
    path: Path,
    *,
    forbidden: tuple[str, ...] = (),
    allowed: tuple[str, ...] | None = None,
    import_root: Path = IMPORT_ROOT,
) -> set[str]:
    """This package's own modules that ``path`` imports and the rule does not permit.

    Only imports *of this package* are considered: importing ``jinja2`` or ``logging`` is a rule for a
    different document, and flagging them here would make the audit noise. Type-only imports are not
    judged at all, for the reason in the module docstring.
    """
    imports = _read_imports(path, import_root=import_root)
    ours = {
        module
        for module in imports.runtime
        if module == "auto_inform" or module.startswith("auto_inform.")
    }

    if allowed is not None:
        judged = {module for module in ours if not _matches(module, allowed)}
    else:
        judged = {module for module in ours if _matches(module, forbidden)}

    return judged | imports.unjudgeable


def _layer_files(layer: str) -> list[Path]:
    directory = PACKAGE / layer
    return sorted(directory.glob("*.py")) if directory.is_dir() else []


LAYERED_MODULES: list[tuple[Rule, Path]] = [
    (RULES[layer], path) for layer in RULES for path in _layer_files(layer)
]


@pytest.mark.parametrize(
    ("rule", "path"),
    LAYERED_MODULES,
    ids=[f"{rule.layer}/{path.name}" for rule, path in LAYERED_MODULES],
)
def test_a_module_should_obey_its_layers_dependency_rule(rule: Rule, path: Path) -> None:
    # given every module in a layer with a documented dependency rule
    # when its runtime imports are checked against that rule
    # then none breaks it. The layering is what keeps the domain testable without a network and the
    # application logic unaware of which database it is talking to
    found = violations(path, forbidden=rule.forbidden, allowed=rule.allowed)
    assert found == set(), f"{rule.layer}/{path.name} imports {sorted(found)}"


def test_every_rule_should_declare_exactly_one_style() -> None:
    # given the rule table
    # when each rule is inspected
    # then it carries either a denylist or an allowlist, never neither and never both. A rule with
    # neither is a rule that permits everything, and it would pass the module test above while enforcing
    # nothing -- the failure class this file exists to eliminate
    for name, rule in RULES.items():
        has_forbidden = bool(rule.forbidden)
        has_allowed = rule.allowed is not None
        assert has_forbidden != has_allowed, f"{name} declares neither or both rule styles"


def test_the_application_rule_should_still_be_an_allowlist() -> None:
    # given rule 2, the one rule expressed as a general prohibition
    rule = RULES["application"]

    # then it is still an allowlist, matching the permitted surface the document names. Switching it to
    # the dataclass default -- no forbidden prefixes, no allowlist -- would silently de-enforce the rule
    assert rule.allowed == APPLICATION_ALLOWED
    assert rule.forbidden == ()


def test_domain_should_import_upper_layers_only_for_the_documented_exception() -> None:
    # given the one exception rule 1 allows: block.py naming the two context types for its signatures
    allowed: set[str] = set()
    for path in _layer_files("domain"):
        allowed |= {
            module for module in _read_imports(path).type_only if _matches(module, DOMAIN_FORBIDDEN)
        }

    # when every type-only upward import across the domain layer is collected
    # then it is exactly the documented set. A new one should fail this test and force the document to
    # be updated, because the exception is meant to be bounded and visible, not to accumulate
    assert allowed, "the exception should still exist; if it does not, delete it from docs/01 §4"
    unexpected = allowed - DOMAIN_TYPE_ONLY_ALLOWED
    assert not unexpected, f"undeclared type-only exception(s) in domain/: {sorted(unexpected)}"


def test_the_composition_root_should_import_the_layers_it_wires() -> None:
    # given the package root, which the parametrised test above deliberately exempts
    runtime = _read_imports(PACKAGE / "__init__.py").runtime

    # when its imports are inspected
    # then it does reach into the layers, which is the point of a composition root -- and this assertion
    # documents that exemption rather than leaving it to look like an oversight
    assert any(module.startswith("auto_inform.application") for module in runtime)
    assert any(module.startswith("auto_inform.domain") for module in runtime)
    assert any(module.startswith("auto_inform.rendering") for module in runtime)


# --- controls ------------------------------------------------------------------
#
# Controls are built as a minimal package tree rather than loose files, because a file with no package
# identity has nothing for a relative import to resolve against -- and building the tree is also what
# lets the audit's relative-import support be exercised at all.


def _control_module(tmp_path: Path, layer: str, body: str) -> tuple[Path, Path]:
    """Write a module into ``<tmp>/src/auto_inform/<layer>/`` and return (path, import_root)."""
    import_root = tmp_path / "src"
    directory = import_root / "auto_inform" / layer
    directory.mkdir(parents=True, exist_ok=True)
    (import_root / "auto_inform" / "__init__.py").touch()
    path = directory / "control_module.py"
    path.write_text(body, encoding="utf-8")
    return path, import_root


def test_the_layering_audit_should_flag_a_forbidden_absolute_import(tmp_path: Path) -> None:
    # given a module that breaks the rule in the most ordinary way possible
    path, root = _control_module(
        tmp_path,
        "domain",
        "from auto_inform.infrastructure.stores.sqlite_store import SqliteEventStore\n",
    )

    # when the audit runs over it
    # then it objects. Without this, a bug that made violations() always return an empty set would let
    # every rule pass while enforcing nothing
    assert violations(path, forbidden=DOMAIN_FORBIDDEN, import_root=root) == {
        "auto_inform.infrastructure.stores.sqlite_store",
        "auto_inform.infrastructure.stores.sqlite_store.SqliteEventStore",
    }


def test_the_layering_audit_should_flag_a_forbidden_relative_import(tmp_path: Path) -> None:
    # given the same violation, written with the relative-import syntax a contributor reaching for a
    # parent package would naturally use
    path, root = _control_module(
        tmp_path, "domain", "from ..application.context import ReportContext\n"
    )

    # when the audit runs over it
    # then it objects just as loudly. An earlier version read the module name without the level, recorded
    # "application.context", matched no prefix, and passed -- so the guarantee was bypassable by two dots
    assert violations(path, forbidden=DOMAIN_FORBIDDEN, import_root=root) == {
        "auto_inform.application.context",
        "auto_inform.application.context.ReportContext",
    }


def test_the_layering_audit_should_flag_a_relative_import_that_names_no_module(
    tmp_path: Path,
) -> None:
    # given a relative import of a whole package rather than a name from one
    path, root = _control_module(tmp_path, "domain", "from ..application import context\n")

    # when the audit runs over it
    # then the resolved package itself is checked, not just the name imported from it
    assert "auto_inform.application" in violations(
        path, forbidden=DOMAIN_FORBIDDEN, import_root=root
    )


def test_the_layering_audit_should_flag_a_relative_import_that_escapes_the_package(
    tmp_path: Path,
) -> None:
    # given a relative import climbing past the top-level package, which Python refuses at runtime
    path, root = _control_module(tmp_path, "domain", "from ...elsewhere import Thing\n")

    # when the audit runs over it
    # then it objects rather than resolving to a bare name that matches no rule. This was a residual hole
    # in the first fix: the two-dot form was caught, the beyond-the-root form was not, and both are the
    # same class of silent bypass
    found = violations(path, forbidden=DOMAIN_FORBIDDEN, import_root=root)
    assert found != set()
    assert any("beyond the import root" in entry for entry in found)


def test_the_layering_audit_should_not_flag_a_legal_relative_sibling_import(tmp_path: Path) -> None:
    # given a relative import of a sibling inside the same layer, which is perfectly legal
    path, root = _control_module(tmp_path, "domain", "from .errors import AutoInformError\n")

    # when the audit runs over it
    # then it is content, and the module is named correctly -- resolving relatives must not turn every
    # one of them into a false positive
    assert violations(path, forbidden=DOMAIN_FORBIDDEN, import_root=root) == set()


def test_the_layering_audit_should_not_flag_a_type_only_import(tmp_path: Path) -> None:
    # given a module that names an upper-layer type the documented way
    path, root = _control_module(
        tmp_path,
        "domain",
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from auto_inform.application.context import ReportContext\n",
    )

    # when the audit runs over it
    # then it is content: a type annotation is erased at runtime and creates no dependency
    assert violations(path, forbidden=DOMAIN_FORBIDDEN, import_root=root) == set()


def test_the_layering_audit_should_flag_an_upward_import_hidden_in_a_type_checking_block(
    tmp_path: Path,
) -> None:
    # given an upward import tucked inside a function body within an `if TYPE_CHECKING:` block, where
    # nothing about it is a type annotation
    path, root = _control_module(
        tmp_path,
        "domain",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    def deferred():\n"
        "        from auto_inform.infrastructure.clock import SystemClock\n",
    )

    # when the audit runs over it
    # then it objects: only imports *directly* in the guarded block are excused, so a runtime import
    # cannot hide behind the guard
    assert "auto_inform.infrastructure.clock" in violations(
        path, forbidden=DOMAIN_FORBIDDEN, import_root=root
    )


def test_the_layering_audit_should_flag_an_adapter_that_is_not_on_any_denylist(
    tmp_path: Path,
) -> None:
    # given an application module importing an adapter that does not exist yet, and that no denylist
    # would have named -- the layout tree already plans infrastructure/clock.py
    path, root = _control_module(
        tmp_path, "application", "from auto_inform.infrastructure.clock import SystemClock\n"
    )

    # when the application rule is applied, taken from RULES rather than from the tuple, so that a
    # mis-edit which dropped the allowlist would fail here too
    rule = RULES["application"]

    # then it objects, because that rule is an allowlist: a newly added adapter is forbidden by default
    # rather than permitted until somebody remembers to extend a tuple
    assert "auto_inform.infrastructure.clock" in violations(
        path, allowed=rule.allowed, import_root=root
    )


def test_the_layering_audit_should_permit_the_documented_way_to_reach_a_root_helper(
    tmp_path: Path,
) -> None:
    # given the two natural ways to reach a private helper at the package root, which rule 2 permits
    # stage 6's filename helper will need
    for body in (
        "from auto_inform import _naming\n",
        "from auto_inform._naming import slug\n",
    ):
        path, root = _control_module(tmp_path, "application", body)

        # when the application rule is applied
        # then it is permitted, so the document's grant is usable rather than merely stated
        assert violations(path, allowed=APPLICATION_ALLOWED, import_root=root) == set(), body


def test_the_layering_audit_should_still_forbid_reaching_rendering_through_the_root(
    tmp_path: Path,
) -> None:
    # given the indirection form used to reach something that is *not* permitted
    path, root = _control_module(tmp_path, "application", "from auto_inform import rendering\n")

    # when the application rule is applied
    # then it objects: permitting the bare root opens no hole, because the submodule is recorded
    # alongside it
    assert "auto_inform.rendering" in violations(
        path, allowed=APPLICATION_ALLOWED, import_root=root
    )
