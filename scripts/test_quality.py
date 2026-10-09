"""Run named, fail-closed checks over one exact native test candidate."""

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import dev  # noqa: E402
from scripts.coverage_evidence import normalized_report_files, valid_suite  # noqa: E402

LINT_RULES = "F631,PT010,PT011,PT012,PT026,PT030"
SYSTEMS = ("Linux", "Windows", "Darwin")


def result(name: str, started: float, status: str, detail: Any) -> dict[str, Any]:
    """Give every check one stable, timed result, including blocked checks."""
    return {
        "name": name,
        "status": status,
        "seconds": round(time.monotonic() - started, 3),
        "detail": detail,
    }


def source_functions(path: Path) -> list[tuple[str, int, int]]:
    """List nested and top-level function spans with stable qualified names."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, parents: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = ".".join((*parents, child.name))
                found.append((qualified, child.lineno, child.end_lineno or child.lineno))
                visit(child, (*parents, child.name))
            elif isinstance(child, ast.ClassDef):
                visit(child, (*parents, child.name))
            else:
                visit(child, parents)

    visit(tree, ())
    return found


def applicable(path: str) -> set[str]:
    """Assign native-only modules to the OS that can import them."""
    if path.endswith("_windows.py"):
        return {"Windows"}
    if path.endswith("_posix.py"):
        return {"Linux", "Darwin"}
    return set(SYSTEMS)


def exception_records(path: Path) -> list[dict[str, Any]]:
    """Load only complete, current, independently reviewed exact-path exceptions."""
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("exception ledger must be a list")
    ids: set[str] = set()
    fields = {
        "id",
        "symbol",
        "source_path",
        "source_sha256",
        "platforms",
        "missing_lines",
        "missing_arcs",
        "exception_paths",
        "reason",
        "compensating_case_ids",
        "reviewer",
        "reviewed_at",
        "expires_at",
    }
    for record in records:
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError("exception record has missing or unexpected fields")
        if not isinstance(record["id"], str) or not record["id"] or record["id"] in ids:
            raise ValueError("duplicate or blank exception ID")
        ids.add(record["id"])
        if not isinstance(record["source_path"], str):
            raise ValueError(f"invalid exception source path: {record['id']}")
        source = ROOT / record["source_path"]
        if (
            not source.is_file()
            or not str(source.resolve()).startswith(str(ROOT.resolve()) + os.sep)
            or hashlib.sha256(source.read_bytes()).hexdigest() != record["source_sha256"]
        ):
            raise ValueError(f"stale or unsafe exception source: {record['id']}")
        if (
            not isinstance(record["symbol"], str)
            or not record["symbol"]
            or not isinstance(record["platforms"], list)
            or not record["platforms"]
            or not all(isinstance(system, str) for system in record["platforms"])
            or not set(record["platforms"]) <= set(SYSTEMS)
            or len(record["platforms"]) != len(set(record["platforms"]))
            or not isinstance(record["missing_lines"], list)
            or not all(type(n) is int and n > 0 for n in record["missing_lines"])
            or not isinstance(record["missing_arcs"], list)
            or not all(
                isinstance(a, list) and len(a) == 2 and all(type(n) is int for n in a)
                for a in record["missing_arcs"]
            )
            or not isinstance(record["exception_paths"], list)
            or not all(isinstance(item, str) and item for item in record["exception_paths"])
            or not isinstance(record["compensating_case_ids"], list)
            or not isinstance(record["reason"], str)
            or not record["reason"].strip()
            or not isinstance(record["reviewer"], str)
            or not record["reviewer"].strip()
            or not record["compensating_case_ids"]
        ):
            raise ValueError(f"incomplete exception: {record['id']}")
        try:
            reviewed = date.fromisoformat(record["reviewed_at"])
            if reviewed > date.today():
                raise ValueError(f"future exception review: {record['id']}")
            if (
                record["expires_at"] is not None
                and date.fromisoformat(record["expires_at"]) < date.today()
            ):
                raise ValueError(f"expired exception: {record['id']}")
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid exception review date: {record['id']}") from error
        for case in record["compensating_case_ids"]:
            if not isinstance(case, str) or "::" not in case:
                raise ValueError(f"missing compensating case: {record['id']}")
            case_path = (ROOT / case.split("::", 1)[0]).resolve()
            if (
                not str(case_path).startswith(str(ROOT.resolve()) + os.sep)
                or not case_path.is_file()
                or not case_path.relative_to(ROOT.resolve())
                .as_posix()
                .startswith(("tests/unit/", "tests/integration/"))
            ):
                raise ValueError(f"missing compensating case: {record['id']}")
    return records


def valid_tooling(root: Path, identity: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Reuse the strict native receipt validator for separate tooling unit records."""
    reports: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    config = hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()
    tools = {
        name: importlib.metadata.version(name) for name in ("coverage", "pytest", "pytest-cov")
    }
    for system in SYSTEMS:
        directory = root / system / "tooling-unit"
        try:
            observed, suite, _ = valid_suite(
                directory,
                sha=identity["sha"],
                tracked_digest=identity["tracked_digest"],
                config_digest=config,
                tool_versions=tools,
                source_root=ROOT,
            )
            if observed != system or suite != "tooling-unit":
                raise ValueError(f"misplaced tooling unit record: {system}")
            reports[system] = normalized_report_files(
                json.loads((directory / "coverage.json").read_text(encoding="utf-8"))
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            errors.append(f"{system}/tooling-unit: {error}")
    extra = [
        p
        for p in root.rglob("manifest.json")
        if p.parent not in {root / system / "tooling-unit" for system in SYSTEMS}
    ]
    errors.extend(f"unexpected tooling unit record: {path.parent}" for path in extra)
    if errors:
        raise ValueError("; ".join(errors))
    return reports


def inspect_native(root: Path, identity: dict[str, str]) -> dict[tuple[str, str], Path]:
    """Validate every expected native record and report independent defects together."""
    records: dict[tuple[str, str], Path] = {}
    errors: list[str] = []
    expected = {root / system / suite for system in SYSTEMS for suite in ("unit", "integration")}
    config = hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()
    tools = {
        name: importlib.metadata.version(name) for name in ("coverage", "pytest", "pytest-cov")
    }
    for directory in sorted(expected):
        try:
            system, suite, database = valid_suite(
                directory,
                sha=identity["sha"],
                tracked_digest=identity["tracked_digest"],
                config_digest=config,
                tool_versions=tools,
                source_root=ROOT,
            )
            if directory != root / system / suite:
                raise ValueError("native record has wrong platform or suite location")
            records[(system, suite)] = database
        except (ValueError, OSError, KeyError, TypeError) as error:
            errors.append(f"{directory.relative_to(root)}: {error}")
    for extra in sorted(
        path.parent for path in root.rglob("manifest.json") if path.parent not in expected
    ):
        errors.append(f"unexpected native record: {extra.relative_to(root)}")
    if errors:
        raise ValueError("; ".join(errors))
    return records


def validate_compensating_cases(
    records: dict[tuple[str, str], Path], exceptions: list[dict[str, Any]]
) -> None:
    """Require every cited exact case to execute on each claimed native platform."""
    if not exceptions:
        return
    outcomes: dict[tuple[str, str], dict[str, Any]] = {}
    for system in SYSTEMS:
        for suite in ("unit", "integration"):
            path = records[(system, suite)].parent / "pytest-evidence.json"
            outcomes[(system, suite)] = json.loads(path.read_text(encoding="utf-8"))
    for exception in exceptions:
        for system in exception["platforms"]:
            for case in exception["compensating_case_ids"]:
                suite = "unit" if case.startswith("tests/unit/") else "integration"
                record = outcomes[(system, suite)]
                collected = {item["nodeid"] for item in record["collected"]}
                phases = record["reports"].get(case)
                if (
                    case not in collected
                    or not isinstance(phases, list)
                    or not any(
                        phase.get("phase") == "call" and phase.get("outcome") == "passed"
                        for phase in phases
                    )
                    or any(phase.get("outcome") != "passed" for phase in phases)
                ):
                    raise ValueError(
                        f"compensating case did not execute: {exception['id']} {system} {case}"
                    )


def function_gaps(
    records: dict[tuple[str, str], Path],
    tooling: dict[str, dict[str, Any]],
    exceptions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare every source function with native unit executable lines and arcs."""
    validate_compensating_cases(records, exceptions)
    used: set[str] = set()
    production = {
        system: normalized_report_files(
            json.loads(
                (records[(system, "unit")].parent / "coverage.json").read_text(encoding="utf-8")
            )
        )
        for system in SYSTEMS
    }
    gaps: list[dict[str, Any]] = []
    sources = sorted((ROOT / "src/agent_company").rglob("*.py"))
    sources += sorted((ROOT / "scripts").glob("*.py"))
    for source in sources:
        if source.name == "__init__.py":
            continue
        relative = source.relative_to(ROOT).as_posix()
        functions = source_functions(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        module = relative.removesuffix(".py").removeprefix("src/").replace("/", ".")
        systems = sorted(applicable(relative))
        reports = tooling if relative.startswith("scripts/") else production
        for symbol, first, last in functions:
            full_symbol = f"{module}:{symbol}"
            nested = [
                (a, b) for other, a, b in functions if other != symbol and first <= a <= b <= last
            ]
            matching = [
                exception
                for exception in exceptions
                if exception["source_path"] == relative
                and exception["source_sha256"] == digest
                and exception["symbol"] == full_symbol
            ]
            for exception in matching:
                if not set(exception["platforms"]) <= set(systems):
                    raise ValueError(f"exception claims inapplicable platform: {exception['id']}")
            for system in systems:
                row = reports[system].get(relative)
                if row is None:
                    gaps.append(
                        {
                            "systems": [system],
                            "source_path": relative,
                            "symbol": full_symbol,
                            "error": "missing source report",
                            "missing_on": [system],
                        }
                    )
                    continue
                executable = set(row["executed_lines"] + row["missing_lines"])
                executed = set(row["executed_lines"])
                possible_arcs = {
                    tuple(arc) for arc in row["executed_branches"] + row["missing_branches"]
                }
                executed_arcs = {tuple(arc) for arc in row["executed_branches"]}
                own = {
                    line
                    for line in executable
                    if first <= line <= last and not any(a <= line <= b for a, b in nested)
                }
                lines = own - executed
                arcs = {arc for arc in possible_arcs - executed_arcs if arc[0] in own}
                approved_lines: set[int] = set()
                approved_arcs: set[tuple[int, int]] = set()
                for exception in matching:
                    if system not in exception["platforms"]:
                        continue
                    claimed_lines = set(exception["missing_lines"])
                    claimed_arcs = {tuple(arc) for arc in exception["missing_arcs"]}
                    if not claimed_lines <= lines or not claimed_arcs <= arcs:
                        raise ValueError(
                            f"exception claims nonmissing path: {exception['id']} {system}"
                        )
                    if not claimed_lines and not claimed_arcs and not exception["exception_paths"]:
                        raise ValueError(f"empty exception: {exception['id']}")
                    used.add(exception["id"])
                    approved_lines.update(claimed_lines)
                    approved_arcs.update(claimed_arcs)
                if not own or lines - approved_lines or arcs - approved_arcs:
                    gaps.append(
                        {
                            "systems": [system],
                            "source_path": relative,
                            "symbol": full_symbol,
                            "missing_lines": sorted(lines - approved_lines),
                            "missing_arcs": sorted([list(arc) for arc in arcs - approved_arcs]),
                            "error": "no executable mapping" if not own else "unreviewed unit gap",
                        }
                    )
    unused = {record["id"] for record in exceptions} - used
    if unused:
        raise ValueError(f"unused or mismatched exceptions: {sorted(unused)}")
    return gaps


def run_lint(output: Path) -> dict[str, Any]:
    """Run the six selected test rules without auto-fixing test assertions."""
    started = time.monotonic()
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--no-fix",
        "--select",
        LINT_RULES,
        "--output-format",
        "json",
        "tests",
    ]
    try:
        process = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return result("focused_lint", started, "failed", str(error))
    (output / "lint.stdout.json").write_text(process.stdout, encoding="utf-8")
    (output / "lint.stderr.log").write_text(process.stderr, encoding="utf-8")
    try:
        findings = json.loads(process.stdout)
        if not isinstance(findings, list):
            raise ValueError("Ruff output was not a list")
    except (json.JSONDecodeError, ValueError):
        return result(
            "focused_lint",
            started,
            "failed",
            {"exit_code": process.returncode, "error": "invalid Ruff output"},
        )
    return result(
        "focused_lint",
        started,
        "passed" if process.returncode == 0 and not findings else "failed",
        {"exit_code": process.returncode, "findings": findings, "rules": LINT_RULES},
    )


def probe_receipt(
    path: Path,
    junit_path: Path,
    selectors: list[str],
    *,
    failure: str | None,
    failure_type: str | None = None,
) -> tuple[bool, str]:
    """Require exact selected collection and the intended call assertion outcome."""
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        items = receipt["collected"]
        reports = receipt["reports"]
        ids = [item["nodeid"] for item in items]

        def selected(node: str) -> bool:
            """Match an exact node or its selected parameter cases."""
            return any(
                node == selector or node.startswith(selector + "[") for selector in selectors
            )

        if (
            receipt.get("collection_errors")
            or not ids
            or len(ids) != len(set(ids))
            or set(reports) != set(ids)
            or not all(selected(node) for node in ids)
            or not all(
                any(node == selector or node.startswith(selector + "[") for node in ids)
                for selector in selectors
            )
        ):
            return False, "selected collection changed or failed"
        failed = []
        for node, phases in reports.items():
            if not isinstance(phases, list) or not phases:
                return False, f"missing phases: {node}"
            calls = [phase for phase in phases if phase.get("phase") == "call"]
            if len(calls) != 1 or any(
                phase.get("outcome") != "passed" for phase in phases if phase.get("phase") != "call"
            ):
                return False, f"setup or teardown failed: {node}"
            if calls[0].get("outcome") == "failed":
                failed.append(node)
            elif calls[0].get("outcome") != "passed":
                return False, f"unexecuted call: {node}"
        junit = ET.parse(junit_path).getroot()
        failures = junit.findall(".//failure")
        if failure is None:
            return (
                not failed and not failures and receipt["exit_code"] == 0,
                "baseline had failed assertions" if failed or failures else "baseline passed",
            )
        if failure_type is None:
            return False, "mutant expected failure type is missing"
        if (
            not failed
            or len(failures) != len(failed)
            or receipt["exit_code"] != 1
            or any(
                not item.get("message", "").startswith(failure_type + ":")
                or failure not in (item.get("message", "") + (item.text or ""))
                for item in failures
            )
        ):
            junit_messages = [item.get("message") for item in failures]
            return False, (
                "mutant did not fail through the selected assertion: "
                f"failed={failed}, junit={junit_messages}"
            )
        return True, f"intended assertion failed in {len(failed)} selected cases"
    except (OSError, KeyError, TypeError, ValueError, ET.ParseError, json.JSONDecodeError) as error:
        return False, f"malformed probe receipt: {error}"


def run_probes(spec: Path, output: Path) -> dict[str, Any]:
    """Kill three selected faults only through intended assertions in isolated copies."""
    started = time.monotonic()
    cases = json.loads(spec.read_text(encoding="utf-8"))
    kinds = {"wrong_result", "missing_exception", "omitted_effect"}
    if (
        not isinstance(cases, list)
        or len(cases) != 3
        or any(not isinstance(case, dict) for case in cases)
        or {case.get("kind") for case in cases} != kinds
    ):
        raise ValueError("probe spec requires exactly three named fault kinds")
    findings: list[dict[str, Any]] = []
    for case in cases:
        name = case["kind"]
        source = ROOT / case["source_path"]
        tests = case["test_nodeids"]
        old = case["old"]
        new = case["new"]
        if (
            not source.is_file()
            or not str(source.resolve()).startswith(str(ROOT.resolve()) + os.sep)
            or not isinstance(tests, list)
            or not tests
            or not all(
                isinstance(t, str)
                and "::" in t
                and (ROOT / t.split("::", 1)[0])
                .resolve()
                .is_relative_to((ROOT / "tests").resolve())
                and (ROOT / t.split("::", 1)[0]).is_file()
                for t in tests
            )
            or not isinstance(old, str)
            or not old
            or not isinstance(new, str)
            or old == new
            or hashlib.sha256(source.read_bytes()).hexdigest() != case["source_sha256"]
        ):
            findings.append(
                {"kind": name, "status": "failed", "error": "stale or malformed target"}
            )
            continue
        original = source.read_text(encoding="utf-8")
        if original.count(old) != 1:
            findings.append(
                {"kind": name, "status": "failed", "error": "mutation target is not unique"}
            )
            continue
        with tempfile.TemporaryDirectory(prefix="quality-probe-") as temporary:
            copy = Path(temporary) / "candidate"
            shutil.copytree(
                ROOT,
                copy,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    ".task",
                    ".pytest_cache",
                    ".mypy_cache",
                    ".ruff_cache",
                    "__pycache__",
                    ".coverage*",
                ),
            )
            target = copy / case["source_path"]
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=short",
                "-p",
                "scripts.pytest_evidence",
                *tests,
            ]
            environment = os.environ.copy()
            environment["COVERAGE_FILE"] = str(copy / ".coverage.probe")
            environment["AGENT_COMPANY_PYTEST_EVIDENCE"] = str(copy / "baseline.json")
            try:
                baseline = subprocess.run(
                    [*command, f"--junitxml={copy / 'baseline.xml'}"],
                    cwd=copy,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=case.get("timeout_seconds", 30),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                findings.append({"kind": name, "status": "failed", "error": "baseline timed out"})
                continue
            (output / f"{name}-baseline.log").write_text(
                baseline.stdout + baseline.stderr, encoding="utf-8"
            )
            baseline_valid, baseline_reason = probe_receipt(
                copy / "baseline.json", copy / "baseline.xml", tests, failure=None
            )
            if baseline.returncode != 0 or not baseline_valid:
                findings.append(
                    {
                        "kind": name,
                        "status": "failed",
                        "error": baseline_reason,
                        "exit_code": baseline.returncode,
                    }
                )
                continue
            target.write_text(original.replace(old, new), encoding="utf-8")
            environment["AGENT_COMPANY_PYTEST_EVIDENCE"] = str(copy / "mutant.json")
            try:
                mutant = subprocess.run(
                    [*command, f"--junitxml={copy / 'mutant.xml'}"],
                    cwd=copy,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=case.get("timeout_seconds", 30),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                findings.append({"kind": name, "status": "failed", "error": "mutant timed out"})
                continue
            log = mutant.stdout + mutant.stderr
            (output / f"{name}-mutant.log").write_text(log, encoding="utf-8")
            expected = case["expected_failure"]
            killed, reason = probe_receipt(
                copy / "mutant.json",
                copy / "mutant.xml",
                tests,
                failure=expected,
                failure_type=case["expected_failure_type"],
            )
            findings.append(
                {
                    "kind": name,
                    "status": "passed" if killed else "failed",
                    "baseline_exit": baseline.returncode,
                    "mutant_exit": mutant.returncode,
                    "expected_failure": expected,
                    "reason": reason,
                }
            )
    return result(
        "assertion_probes",
        started,
        "passed" if all(f["status"] == "passed" for f in findings) else "failed",
        findings,
    )


def advisory_reports(
    records: dict[tuple[str, str], Path] | None, base: str | None
) -> dict[str, Any]:
    """Retain native suite totals and optional changed lines without another pass threshold."""
    advisory: dict[str, Any] = {
        "order_replay": {"status": "not_run", "reason": "On-demand selected node order check"}
    }
    if records is None:
        advisory["suite_trends"] = {"status": "unavailable", "reason": "Native evidence invalid"}
    else:
        advisory["suite_trends"] = {
            f"{system}/{suite}": json.loads(
                (database.parent / "coverage.json").read_text(encoding="utf-8")
            )["totals"]
            for (system, suite), database in records.items()
        }
    if base is None:
        advisory["changed_code"] = {
            "status": "unavailable",
            "reason": "No pinned merge base supplied",
        }
    else:
        try:
            process = subprocess.run(
                ["git", "diff", "--unified=0", f"{base}...HEAD", "--", "src", "scripts"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if process.returncode:
                advisory["changed_code"] = {"status": "unavailable", "reason": process.stderr}
            else:
                advisory["changed_code"] = {
                    "status": "available",
                    "base": base,
                    "diff": process.stdout,
                    "limit": "Review executable-line and moved-code mapping manually",
                }
        except (OSError, subprocess.TimeoutExpired) as error:
            advisory["changed_code"] = {"status": "unavailable", "reason": str(error)}
    return advisory


def run_aggregate(records: dict[tuple[str, str], Path], output: Path) -> dict[str, Any]:
    """Run the unchanged Linux/Windows floor with a bounded fresh input copy."""
    started = time.monotonic()
    aggregate_inputs = output / "aggregate-inputs"
    combined = output / "combined"
    for path in (aggregate_inputs, combined):
        if path.exists():
            shutil.rmtree(path)
    try:
        for system in ("Linux", "Windows"):
            for suite in ("unit", "integration"):
                shutil.copytree(records[(system, suite)].parent, aggregate_inputs / system / suite)
        command = [
            sys.executable,
            str(ROOT / "scripts/combine_coverage.py"),
            str(aggregate_inputs),
            "--output-dir",
            str(combined),
        ]
        process = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
        )
        (output / "aggregate.log").write_text(process.stdout + process.stderr, encoding="utf-8")
        report = combined / "combined.json"
        detail: Any = (
            json.loads(report.read_text(encoding="utf-8"))
            if report.is_file()
            else {"error": process.stderr}
        )
        return result(
            "aggregate_80",
            started,
            "passed" if process.returncode == 0 and detail.get("passed") is True else "failed",
            detail,
        )
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        (output / "aggregate.log").write_text(str(error), encoding="utf-8")
        return result("aggregate_80", started, "failed", str(error))


def main() -> int:
    """Run independent controls and retain named successes, failures and blocked work."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tooling-dir", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path, default=ROOT / "scripts/quality_exceptions.json")
    parser.add_argument("--probes", type=Path, default=ROOT / "scripts/quality_probes.json")
    parser.add_argument("--base", help="Pinned merge base for advisory changed-code diff")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("quality output must be outside the candidate checkout")
    output.mkdir(parents=True, exist_ok=True)
    (output / "quality.json").unlink(missing_ok=True)
    (output / "function-gaps.json").unlink(missing_ok=True)
    for stale in ("combined", "aggregate-inputs"):
        if (output / stale).exists():
            shutil.rmtree(output / stale)
    results: list[dict[str, Any]] = []
    analysis_started = time.monotonic()
    identity = dev.checkout_state()
    started = time.monotonic()
    if identity["status"]:
        results.append(
            result(
                "candidate_identity",
                started,
                "failed",
                "checkout is dirty; exact candidate evidence requires a clean commit",
            )
        )
    else:
        results.append(result("candidate_identity", started, "passed", identity))
    records: dict[tuple[str, str], Path] | None = None
    started = time.monotonic()
    try:
        records = inspect_native(args.artifacts.resolve(), identity)
        results.append(
            result(
                "native_evidence",
                started,
                "passed",
                {f"{s}/{u}": str(p) for (s, u), p in records.items()},
            )
        )
    except (ValueError, OSError, KeyError) as error:
        results.append(result("native_evidence", started, "failed", str(error)))
    started = time.monotonic()
    tooling: dict[str, dict[str, Any]] | None = None
    try:
        tooling = valid_tooling(args.tooling_dir.resolve(), identity)
        results.append(result("tooling_unit_evidence", started, "passed", sorted(tooling)))
    except (ValueError, OSError, KeyError, TypeError) as error:
        results.append(result("tooling_unit_evidence", started, "failed", str(error)))
    started = time.monotonic()
    if records is None:
        results.append(result("aggregate_80", started, "blocked", "native evidence invalid"))
        results.append(
            result("function_obligations", started, "blocked", "native evidence invalid")
        )
    else:
        results.append(run_aggregate(records, output))
        started = time.monotonic()
        try:
            if tooling is None:
                results.append(
                    result(
                        "function_obligations", started, "blocked", "tooling unit evidence invalid"
                    )
                )
            else:
                gaps = function_gaps(records, tooling, exception_records(args.exceptions))
                (output / "function-gaps.json").write_text(
                    json.dumps(gaps, indent=2) + "\n", encoding="utf-8"
                )
                results.append(
                    result(
                        "function_obligations",
                        started,
                        "failed" if gaps else "passed",
                        {"gap_count": len(gaps), "artifact": "function-gaps.json"},
                    )
                )
        except (ValueError, OSError, KeyError, SyntaxError) as error:
            results.append(result("function_obligations", started, "failed", str(error)))
    results.append(run_lint(output))
    started = time.monotonic()
    try:
        results.append(run_probes(args.probes, output))
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        results.append(result("assertion_probes", started, "failed", str(error)))
    duration = time.monotonic() - analysis_started
    results.append(
        {
            "name": "analysis_budget",
            "status": "passed" if duration <= 180 else "failed",
            "seconds": round(duration, 3),
            "detail": {"limit_seconds": 180},
        }
    )
    try:
        advisory = advisory_reports(records, args.base)
    except (OSError, ValueError, KeyError, TypeError) as error:
        advisory = {"status": "unavailable", "reason": str(error)}
    report = {
        "schema": 1,
        "candidate": identity,
        "results": results,
        "advisory": advisory,
        "passed": all(row["status"] == "passed" for row in results),
    }
    (output / "quality.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in results:
        detail = row["detail"] if row["status"] != "passed" else "OK"
        print(f"{row['name']}: {row['status']} ({row['seconds']}s) - {detail}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
