"""Check that the configured tracer observes real registration context-manager arcs."""

import ast
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tests.support import ROOT

pytestmark = pytest.mark.integration

REGISTER_CASE = (
    "tests/unit/lifecycle/test_repository_model.py::"
    "test_register_persists_only_explicit_coordinator_startup_packet"
)
SOURCE = Path("src/agent_company/lifecycle/task_workspace.py")


def _binding_arcs() -> set[tuple[int, int]]:
    """Locate the startup binding's entry and exit without fixed line numbers."""
    tree = ast.parse((ROOT / SOURCE).read_text(encoding="utf-8"))
    register = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "register"
    )
    binding = next(
        node
        for node in ast.walk(register)
        if isinstance(node, ast.With)
        and len(node.items) == 1
        and isinstance(node.items[0].context_expr, ast.Call)
        and isinstance(node.items[0].context_expr.func, ast.Attribute)
        and node.items[0].context_expr.func.attr == "child"
        and node.items[0].context_expr.args
        and isinstance(node.items[0].context_expr.args[0], ast.Constant)
        and node.items[0].context_expr.args[0].value == ".bindings"
    )
    following = next(
        node
        for node in register.body
        if isinstance(node, ast.Return) and node.lineno > binding.lineno
    )
    return {(binding.lineno, binding.body[0].lineno), (binding.lineno, following.lineno)}


def _run_register_case(tmp_path: Path, tracer: str | None) -> set[tuple[int, int]]:
    """Run the existing registration contract with an isolated coverage database."""
    output = tmp_path / (tracer or "configured")
    output.mkdir()
    environment = os.environ.copy()
    environment.pop("COVERAGE_CORE", None)
    environment.pop("COVERAGE_PROCESS_CONFIG", None)
    environment["COVERAGE_FILE"] = str(output / ".coverage")
    if tracer is not None:
        environment["COVERAGE_CORE"] = tracer
    junit = output / "tests.xml"
    report = output / "coverage.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            REGISTER_CASE,
            f"--junitxml={junit}",
            "--cov=agent_company",
            f"--cov-config={ROOT / 'pyproject.toml'}",
            f"--cov-report=json:{report}",
            "--cov-fail-under=0",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cases = list(ElementTree.parse(junit).iter("testcase"))
    assert len(cases) == 1
    assert cases[0].attrib["name"] == REGISTER_CASE.split("::", 1)[1]
    assert not list(cases[0].iter("failure"))
    assert not list(cases[0].iter("error"))
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    source = next(path for path in files if path.replace("\\", "/") == SOURCE.as_posix())
    return {tuple(arc) for arc in files[source]["executed_branches"]}


def test_configured_tracer_observes_registration_binding_arcs(tmp_path: Path) -> None:
    """A passing registration assertion cannot conceal missing tracer arcs."""
    policy = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert policy["tool"]["coverage"]["run"]["core"] == "ctrace"
    expected = _binding_arcs()
    assert len(expected) == 2
    configured = _run_register_case(tmp_path, None)
    sysmon = _run_register_case(tmp_path, "sysmon")
    comparison = {
        "expected": sorted(expected),
        "configured": sorted(expected & configured),
        "sysmon": sorted(expected & sysmon),
    }
    (tmp_path / "tracer-comparison.json").write_text(
        json.dumps(comparison, indent=2), encoding="utf-8"
    )
    assert expected <= configured
