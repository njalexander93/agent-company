"""Check quality decisions with disposable evidence and independent expected gaps."""

import hashlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

import pytest
from coverage import CoverageData

from scripts import test_quality as quality

pytestmark = pytest.mark.unit


def test_source_functions_finds_methods_and_nested_calls(tmp_path: Path) -> None:
    """Keep nested function ownership distinct from its caller."""
    source = tmp_path / "sample.py"
    source.write_text(
        "class Store:\n"
        "    def run(self):\n"
        "        def inner():\n"
        "            return 1\n"
        "        return inner()\n"
    )
    assert quality.source_functions(source) == [
        ("Store.run", 2, 5),
        ("Store.run.inner", 3, 4),
    ]


def test_native_applicability_is_exact() -> None:
    """Treat native-only modules as applicable on their real hosts."""
    assert quality.applicable("src/agent_company/lifecycle/_filesystem_windows.py") == {"Windows"}
    assert quality.applicable("src/agent_company/lifecycle/_filesystem_posix.py") == {
        "Linux",
        "Darwin",
    }
    assert quality.applicable("src/agent_company/lifecycle/task_workspace.py") == {
        "Linux",
        "Windows",
        "Darwin",
    }


def test_function_gap_repair_is_measured_by_unit_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An omitted line and arc fail until unit execution reaches both."""
    monkeypatch.setattr(quality, "ROOT", tmp_path)
    source = tmp_path / "src/agent_company/logic.py"
    source.parent.mkdir(parents=True)
    source.write_text("def choose(value):\n    if value:\n        return 1\n    return 0\n")
    (source.parent / "__init__.py").write_text("")
    (tmp_path / "scripts").mkdir()
    records: dict[tuple[str, str], Path] = {}
    for system in quality.SYSTEMS:
        directory = tmp_path / "evidence" / system / "unit"
        directory.mkdir(parents=True)
        (directory / "coverage.json").write_text(
            json.dumps(
                {
                    "files": {
                        "src/agent_company/logic.py": {
                            "executed_lines": [1, 2, 3],
                            "missing_lines": [4],
                            "executed_branches": [[2, 3]],
                            "missing_branches": [[2, 4]],
                        }
                    }
                }
            )
        )
        records[(system, "unit")] = directory / ".coverage"
    gaps = quality.function_gaps(records, {system: {} for system in quality.SYSTEMS}, [])
    assert len(gaps) == 1
    assert all(gap["missing_lines"] == [4] and gap["missing_arcs"] == [[2, 4]] for gap in gaps)
    path = records[("Linux", "unit")].parent / "coverage.json"
    report = json.loads(path.read_text())
    row = report["files"]["src/agent_company/logic.py"]
    row["executed_lines"].append(4)
    row["missing_lines"] = []
    row["executed_branches"].append([2, 4])
    row["missing_branches"] = []
    path.write_text(json.dumps(report))
    # One applicable native path can cover a portable branch absent on peers.
    assert quality.function_gaps(records, {system: {} for system in quality.SYSTEMS}, []) == []


def test_function_gap_flags_unmeasured_and_unmapped_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source function cannot disappear from the unit denominator."""
    monkeypatch.setattr(quality, "ROOT", tmp_path)
    source = tmp_path / "src/agent_company/logic.py"
    source.parent.mkdir(parents=True)
    source.write_text("def choose():\n    return 1\n")
    (tmp_path / "scripts").mkdir()
    records: dict[tuple[str, str], Path] = {}
    for system in quality.SYSTEMS:
        directory = tmp_path / "evidence" / system / "unit"
        directory.mkdir(parents=True)
        (directory / "coverage.json").write_text(json.dumps({"files": {}}))
        records[(system, "unit")] = directory / ".coverage"
    gaps = quality.function_gaps(records, {system: {} for system in quality.SYSTEMS}, [])
    assert len(gaps) == 1
    assert gaps[0]["error"] == "missing source report"
    assert gaps[0]["missing_on"] == sorted(quality.SYSTEMS)

    for system in quality.SYSTEMS:
        path = records[(system, "unit")].parent / "coverage.json"
        path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/agent_company/logic.py": {
                            "executed_lines": [],
                            "missing_lines": [],
                            "executed_branches": [],
                            "missing_branches": [],
                        }
                    }
                }
            )
        )
    gaps = quality.function_gaps(records, {system: {} for system in quality.SYSTEMS}, [])
    assert len(gaps) == 1
    assert gaps[0]["error"] == "no executable mapping"


def test_function_exception_requires_exact_executed_case_and_missing_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review record cannot cite a nonexistent node or a covered source line."""
    monkeypatch.setattr(quality, "ROOT", tmp_path)
    source = tmp_path / "src/agent_company/logic.py"
    source.parent.mkdir(parents=True)
    source.write_text("def choose():\n    return 1\n")
    (tmp_path / "scripts").mkdir()
    case = "tests/integration/test_logic.py::test_choose"
    records: dict[tuple[str, str], Path] = {}
    for system in quality.SYSTEMS:
        for suite in ("unit", "integration"):
            directory = tmp_path / "evidence" / system / suite
            directory.mkdir(parents=True)
            (directory / "pytest-evidence.json").write_text(
                json.dumps(
                    {
                        "collected": [{"nodeid": case}] if suite == "integration" else [],
                        "reports": {case: [{"phase": "call", "outcome": "passed"}]}
                        if suite == "integration"
                        else {},
                    }
                )
            )
            records[(system, suite)] = directory / ".coverage"
        unit = records[(system, "unit")].parent
        (unit / "coverage.json").write_text(
            json.dumps(
                {
                    "files": {
                        "src/agent_company/logic.py": {
                            "executed_lines": [1],
                            "missing_lines": [2],
                            "executed_branches": [],
                            "missing_branches": [],
                        },
                    }
                }
            )
        )
    exception = {
        "id": "EX-1",
        "source_path": "src/agent_company/logic.py",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "symbol": "agent_company.logic:choose",
        "platforms": list(quality.SYSTEMS),
        "missing_lines": [2],
        "missing_arcs": [],
        "exception_paths": [],
        "compensating_case_ids": [case],
    }
    tooling = {system: {} for system in quality.SYSTEMS}
    assert quality.function_gaps(records, tooling, [exception]) == []
    invalid_case = {**exception, "compensating_case_ids": [case + "_missing"]}
    with pytest.raises(ValueError, match="did not execute"):
        quality.function_gaps(records, tooling, [invalid_case])
    invalid_line = {**exception, "missing_lines": [3]}
    with pytest.raises(ValueError, match="nonmissing path"):
        quality.function_gaps(records, tooling, [invalid_line])
    invalid_symbol = {**exception, "symbol": "agent_company.logic:other"}
    with pytest.raises(ValueError, match="unused or mismatched"):
        quality.function_gaps(records, tooling, [invalid_symbol])
    empty = {**exception, "missing_lines": [], "missing_arcs": [], "exception_paths": []}
    with pytest.raises(ValueError, match="empty exception"):
        quality.function_gaps(records, tooling, [empty])


def test_exception_ledger_rejects_stale_or_unreviewed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception needs attributable review and the exact current source."""
    monkeypatch.setattr(quality, "ROOT", tmp_path)
    source = tmp_path / "src/agent_company/logic.py"
    source.parent.mkdir(parents=True)
    source.write_text("def choose():\n    return 1\n")
    case = tmp_path / "tests/integration/test_logic.py"
    case.parent.mkdir(parents=True)
    case.write_text("def test_choose(): pass\n")
    record = {
        "id": "EX-1",
        "symbol": "choose",
        "source_path": "src/agent_company/logic.py",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "platforms": ["Linux"],
        "missing_lines": [2],
        "missing_arcs": [],
        "exception_paths": [],
        "reason": "Native boundary is inaccessible in this unit",
        "compensating_case_ids": ["tests/integration/test_logic.py::test_choose"],
        "reviewer": "independent reviewer",
        "reviewed_at": "2026-10-08",
        "expires_at": None,
    }
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps([record]))
    assert quality.exception_records(ledger) == [record]
    ledger.write_text(json.dumps([record, record]))
    with pytest.raises(ValueError, match="duplicate or blank exception ID"):
        quality.exception_records(ledger)
    ledger.write_text(json.dumps({"exceptions": [record]}))
    with pytest.raises(ValueError, match="must be a list"):
        quality.exception_records(ledger)
    ledger.write_text(json.dumps([{**record, "unreviewed_extra": True}]))
    with pytest.raises(ValueError, match="missing or unexpected fields"):
        quality.exception_records(ledger)
    record["reviewed_at"] = "2999-01-01"
    ledger.write_text(json.dumps([record]))
    with pytest.raises(ValueError, match="invalid exception review date"):
        quality.exception_records(ledger)
    record["reviewed_at"] = "2026-10-08"
    record["expires_at"] = "2020-01-01"
    ledger.write_text(json.dumps([record]))
    with pytest.raises(ValueError, match="invalid exception review date"):
        quality.exception_records(ledger)
    record["expires_at"] = None
    record["source_path"] = 123
    ledger.write_text(json.dumps([record]))
    with pytest.raises(ValueError, match="invalid exception source path"):
        quality.exception_records(ledger)
    record["source_path"] = "src/agent_company/logic.py"
    record["compensating_case_ids"] = ["not-a-node"]
    ledger.write_text(json.dumps([record]))
    with pytest.raises(ValueError, match="missing compensating case"):
        quality.exception_records(ledger)
    record["compensating_case_ids"] = ["tests/integration/test_logic.py::test_choose"]
    record["reviewer"] = ""
    ledger.write_text(json.dumps([record]))
    with pytest.raises(ValueError, match="incomplete exception"):
        quality.exception_records(ledger)
    record["reviewer"] = "independent reviewer"
    outside = tmp_path.parent / "outside_case.py"
    outside.write_text("def test_outside(): pass\n")
    record["compensating_case_ids"] = ["../outside_case.py::test_outside"]
    ledger.write_text(json.dumps([record]))
    with pytest.raises(ValueError, match="missing compensating case"):
        quality.exception_records(ledger)
    record["compensating_case_ids"] = ["tests/integration/test_logic.py::test_choose"]
    source.write_text("def choose():\n    return 2\n")
    ledger.write_text(json.dumps([record]))
    with pytest.raises(ValueError, match="stale or unsafe"):
        quality.exception_records(ledger)


def test_tooling_receipt_detects_a_changed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changed tooling report bytes invalidate the native receipt."""
    root = tmp_path / "records"
    monkeypatch.setattr(quality, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.run]\nbranch = true\n")
    config = hashlib.sha256((tmp_path / "pyproject.toml").read_bytes()).hexdigest()
    versions = {
        name: importlib.metadata.version(name) for name in ("coverage", "pytest", "pytest-cov")
    }
    identity = {"sha": "candidate", "tracked_digest": "tracked"}
    for system in quality.SYSTEMS:
        directory = root / system / "tooling-unit"
        directory.mkdir(parents=True)
        database = CoverageData(basename=str(directory / ".coverage"))
        database.add_arcs({"scripts/sample.py": [(-1, 1), (1, -1)]})
        database.write()
        database.close()
        (directory / "coverage.json").write_text(
            json.dumps(
                {
                    "meta": {"branch_coverage": True, "version": versions["coverage"]},
                    "files": {
                        "scripts/sample.py": {
                            "executed_lines": [1],
                            "missing_lines": [],
                            "executed_branches": [],
                        }
                    },
                    "totals": {
                        "num_statements": 1,
                        "covered_lines": 1,
                        "num_branches": 0,
                        "covered_branches": 0,
                    },
                }
            )
        )
        (directory / "coverage.xml").write_text(
            '<coverage lines-valid="1" lines-covered="1" branches-valid="0" '
            'branches-covered="0"><packages><package><classes><class name="sample"/>'
            "</classes></package></packages></coverage>"
        )
        (directory / "tests.xml").write_text(
            '<testsuites><testsuite tests="1"><testcase name="test_sample"/>'
            "</testsuite></testsuites>"
        )
        nodeid = "tests/unit/tooling/test_sample.py::test_sample"
        (directory / "pytest-evidence.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "exit_code": 0,
                    "collection_errors": [],
                    "collected": [
                        {
                            "nodeid": nodeid,
                            "suite_markers": ["unit"],
                            "skip_reasons": [],
                        }
                    ],
                    "reports": {
                        nodeid: [
                            {"phase": "setup", "outcome": "passed"},
                            {"phase": "call", "outcome": "passed"},
                            {"phase": "teardown", "outcome": "passed"},
                        ]
                    },
                }
            )
        )
        (directory / "test-tooling-unit-0.log").write_text("1 passed\n")
        names = (
            ".coverage",
            "coverage.json",
            "coverage.xml",
            "tests.xml",
            "pytest-evidence.json",
            "test-tooling-unit-0.log",
        )
        manifest = {
            "system": system,
            "sha": "candidate",
            "end_sha": "candidate",
            "status": "",
            "end_status": "",
            "tracked_digest": "tracked",
            "end_tracked_digest": "tracked",
            "candidate_unchanged": True,
            "config_sha256": config,
            "tool_versions": versions,
            "commands": [
                {
                    "task": "test-tooling-unit",
                    "exit_code": 0,
                    "seconds": 0.1,
                    "log": "test-tooling-unit-0.log",
                }
            ],
            "artifacts_sha256": {
                name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names
            },
        }
        (directory / "manifest.json").write_text(json.dumps(manifest))
    assert set(quality.valid_tooling(root, identity)) == set(quality.SYSTEMS)
    windows = root / "Windows/tooling-unit"
    report_path = windows / "coverage.json"
    original_report = report_path.read_text()
    report_path.write_text("{}")
    with pytest.raises(ValueError, match="missing or changed artifact"):
        quality.valid_tooling(root, identity)

    report_path.write_text(original_report)
    outcomes_path = windows / "pytest-evidence.json"
    outcomes = json.loads(outcomes_path.read_text())
    nodeid = outcomes["collected"][0]["nodeid"]
    outcomes["collected"][0]["suite_markers"] = []
    outcomes_path.write_text(json.dumps(outcomes))
    manifest_path = windows / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts_sha256"]["pytest-evidence.json"] = hashlib.sha256(
        outcomes_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unmarked or misclassified test"):
        quality.valid_tooling(root, identity)

    outcomes["collected"][0]["suite_markers"] = ["unit"]
    outcomes["reports"][nodeid][1]["outcome"] = "skipped"
    outcomes_path.write_text(json.dumps(outcomes))
    manifest["artifacts_sha256"]["pytest-evidence.json"] = hashlib.sha256(
        outcomes_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unapproved skip"):
        quality.valid_tooling(root, identity)
    (root / "extra").mkdir()
    (root / "extra/manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="unexpected tooling unit record"):
        quality.valid_tooling(root, identity)
    (root / "extra/manifest.json").unlink()
    monkeypatch.setattr(
        quality,
        "valid_suite",
        lambda directory, **_kwargs: ("Wrong", "tooling-unit", directory / ".coverage"),
    )
    with pytest.raises(ValueError, match="misplaced tooling unit record"):
        quality.valid_tooling(root, identity)


def test_native_inspection_collects_all_defects_before_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failed native receipt cannot hide a later failed native receipt."""
    monkeypatch.setattr(quality, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.coverage.run]\nbranch = true\n")
    native = tmp_path / "native"
    identity = {"sha": "candidate", "tracked_digest": "bytes"}
    visited: list[str] = []
    defects = {"Darwin/unit", "Windows/integration"}

    def validate(directory: Path, **_kwargs: object) -> tuple[str, str, Path]:
        key = directory.relative_to(native).as_posix()
        visited.append(key)
        if key in defects:
            raise ValueError("invalid receipt")
        return directory.parent.name, directory.name, directory / ".coverage"

    monkeypatch.setattr(quality, "valid_suite", validate)
    with pytest.raises(ValueError, match="invalid receipt") as error:
        quality.inspect_native(native, identity)
    assert len(visited) == 6
    assert "Darwin/unit" in str(error.value)
    assert "Windows/integration" in str(error.value)
    defects.clear()
    records = quality.inspect_native(native, identity)
    assert len(records) == 6
    assert records[("Linux", "unit")] == native / "Linux/unit/.coverage"
    extra = native / "extra"
    extra.mkdir(parents=True)
    (extra / "manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="unexpected native record"):
        quality.inspect_native(native, identity)
    (extra / "manifest.json").unlink()

    def misplaced(directory: Path, **_kwargs: object) -> tuple[str, str, Path]:
        system, suite, database = validate(directory)
        return ("Wrong" if system == "Linux" and suite == "unit" else system), suite, database

    monkeypatch.setattr(quality, "valid_suite", misplaced)
    with pytest.raises(ValueError, match="wrong platform or suite location"):
        quality.inspect_native(native, identity)


def test_probe_receipt_requires_selected_call_assertion(tmp_path: Path) -> None:
    """A selected failure passes; unrelated or collection failures do not."""
    node = "tests/unit/test_contract.py::test_result"
    receipt = tmp_path / "outcomes.json"
    junit = tmp_path / "tests.xml"
    data = {
        "exit_code": 0,
        "collection_errors": [],
        "collected": [{"nodeid": node}],
        "reports": {
            node: [
                {"phase": "setup", "outcome": "passed"},
                {"phase": "call", "outcome": "passed"},
                {"phase": "teardown", "outcome": "passed"},
            ]
        },
    }
    receipt.write_text(json.dumps(data))
    junit.write_text(
        '<testsuites><testsuite tests="1"><testcase name="test_result"/></testsuite></testsuites>'
    )
    assert quality.probe_receipt(receipt, junit, [node], failure=None)[0]
    data["exit_code"] = 1
    data["reports"][node][1]["outcome"] = "failed"
    receipt.write_text(json.dumps(data))
    junit.write_text(
        '<testsuites><testsuite tests="1"><testcase name="test_result">'
        '<failure message="AssertionError: assert 2 == 1"/></testcase>'
        "</testsuite></testsuites>"
    )
    assert quality.probe_receipt(
        receipt, junit, [node], failure="assert 2 == 1", failure_type="AssertionError"
    )[0]
    assert not quality.probe_receipt(receipt, junit, [node], failure="assert 2 == 1")[0]
    assert not quality.probe_receipt(
        receipt, junit, [node], failure="assert effect happened", failure_type="AssertionError"
    )[0]
    assert not quality.probe_receipt(
        receipt, junit, [node], failure="assert 2 == 1", failure_type="RuntimeError"
    )[0]
    assert not quality.probe_receipt(
        receipt,
        junit,
        ["tests/unit/test_contract.py::test_other"],
        failure="assert 2 == 1",
        failure_type="AssertionError",
    )[0]
    data["collection_errors"] = ["SyntaxError"]
    receipt.write_text(json.dumps(data))
    assert not quality.probe_receipt(
        receipt, junit, [node], failure="assert 2 == 1", failure_type="AssertionError"
    )[0]
    receipt.write_text("not json")
    assert quality.probe_receipt(
        receipt, junit, [node], failure="assert 2 == 1", failure_type="AssertionError"
    )[1].startswith("malformed probe receipt")
    data["collection_errors"] = []
    data["reports"][node][0]["outcome"] = "failed"
    receipt.write_text(json.dumps(data))
    assert not quality.probe_receipt(
        receipt, junit, [node], failure="assert 2 == 1", failure_type="AssertionError"
    )[0]
    data["reports"][node][0]["outcome"] = "passed"
    data["reports"][node][1]["outcome"] = "skipped"
    receipt.write_text(json.dumps(data))
    assert not quality.probe_receipt(
        receipt, junit, [node], failure="assert 2 == 1", failure_type="AssertionError"
    )[0]
    data["reports"][node] = []
    receipt.write_text(json.dumps(data))
    assert quality.probe_receipt(
        receipt, junit, [node], failure="assert 2 == 1", failure_type="AssertionError"
    )[1].startswith("missing phases")


def test_probe_rejects_test_selector_outside_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A curated probe cannot execute a selector through path traversal."""
    root = tmp_path / "candidate"
    source = root / "src/module.py"
    source.parent.mkdir(parents=True)
    source.write_text("def result():\n    return 1\n")
    (tmp_path / "outside.py").write_text("def test_outside(): pass\n")
    monkeypatch.setattr(quality, "ROOT", root)
    cases = [
        {
            "kind": kind,
            "source_path": "src/module.py",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "test_nodeids": ["../outside.py::test_outside"],
            "old": "return 1",
            "new": "return 2",
            "expected_failure": "assert",
            "expected_failure_type": "AssertionError",
        }
        for kind in ("wrong_result", "missing_exception", "omitted_effect")
    ]
    spec = tmp_path / "probes.json"
    spec.write_text(json.dumps(cases))
    output = tmp_path / "out"
    output.mkdir()
    result = quality.run_probes(spec, output)
    assert result["status"] == "failed"
    assert all(item["error"] == "stale or malformed target" for item in result["detail"])
    assert list(output.iterdir()) == []
    spec.write_text("[]")
    with pytest.raises(ValueError, match="exactly three named fault kinds"):
        quality.run_probes(spec, output)

    test_source = root / "tests/unit/test_contract.py"
    test_source.parent.mkdir(parents=True)
    test_source.write_text("def test_contract(): pass\n")
    source.write_text("def result():\n    return 1\n    return 1\n")
    for case in cases:
        case["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        case["test_nodeids"] = ["tests/unit/test_contract.py::test_contract"]
    spec.write_text(json.dumps(cases))
    result = quality.run_probes(spec, output)
    assert result["status"] == "failed"
    assert all(item["error"] == "mutation target is not unique" for item in result["detail"])


def test_probe_runner_requires_intended_assertions_after_baselines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run three isolated baseline/mutant receipts through the probe controller."""
    root = tmp_path / "candidate"
    source = root / "src/module.py"
    source.parent.mkdir(parents=True)
    source.write_text("WRONG = 1\nMISSING = 1\nEFFECT = 1\n")
    test_source = root / "tests/unit/test_contract.py"
    test_source.parent.mkdir(parents=True)
    test_source.write_text("def test_contract(): pass\n")
    monkeypatch.setattr(quality, "ROOT", root)
    node = "tests/unit/test_contract.py::test_contract"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    cases = [
        {
            "kind": kind,
            "source_path": "src/module.py",
            "source_sha256": digest,
            "test_nodeids": [node],
            "old": f"{token} = 1",
            "new": f"{token} = 2",
            "expected_failure": "assert observed",
            "expected_failure_type": "AssertionError",
        }
        for kind, token in (
            ("wrong_result", "WRONG"),
            ("missing_exception", "MISSING"),
            ("omitted_effect", "EFFECT"),
        )
    ]
    spec = tmp_path / "probes.json"
    spec.write_text(json.dumps(cases))
    calls: list[str] = []
    fault_mode = [False]

    class Completed:
        def __init__(self, code: int) -> None:
            self.returncode = code
            self.stdout = "pytest output\n"
            self.stderr = ""

    def run(command: list[str], **kwargs: object) -> Completed:
        attempt = len(calls)
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        receipt = Path(environment["AGENT_COMPANY_PYTEST_EVIDENCE"])
        mutant = receipt.name == "mutant.json"
        calls.append(receipt.name)
        if fault_mode[0] and attempt in {0, 3}:
            raise subprocess.TimeoutExpired("pytest", 30)
        checkout = kwargs["cwd"]
        assert isinstance(checkout, Path)
        target = checkout / "src/module.py"
        assert target.read_text().count(" = 2") == (1 if mutant else 0)
        receipt.write_text(
            json.dumps(
                {
                    "exit_code": 1 if mutant else 0,
                    "collection_errors": [],
                    "collected": [{"nodeid": node}],
                    "reports": {
                        node: [
                            {"phase": "setup", "outcome": "passed"},
                            {"phase": "call", "outcome": "failed" if mutant else "passed"},
                            {"phase": "teardown", "outcome": "passed"},
                        ]
                    },
                }
            )
        )
        junit = Path(
            next(item.split("=", 1)[1] for item in command if item.startswith("--junitxml="))
        )
        failure = '<failure message="AssertionError: assert observed"/>' if mutant else ""
        junit.write_text(
            f'<testsuite><testcase name="test_contract">{failure}</testcase></testsuite>'
        )
        return Completed(1 if mutant or fault_mode[0] and attempt == 1 else 0)

    monkeypatch.setattr(quality.subprocess, "run", run)
    output = tmp_path / "out"
    output.mkdir()
    result = quality.run_probes(spec, output)
    assert result["status"] == "passed"
    assert calls == ["baseline.json", "mutant.json"] * 3
    assert all((output / f"{case['kind']}-mutant.log").is_file() for case in cases)
    calls.clear()
    fault_mode[0] = True
    failed = quality.run_probes(spec, output)
    assert failed["status"] == "failed"
    assert [item["error"] for item in failed["detail"]] == [
        "baseline timed out",
        "baseline passed",
        "mutant timed out",
    ]
    assert calls == ["baseline.json", "baseline.json", "baseline.json", "mutant.json"]


def test_aggregate_timeout_clears_stale_result_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prior passing combined report cannot rescue a timed-out current run."""
    records: dict[tuple[str, str], Path] = {}
    for system in ("Linux", "Windows"):
        for suite in ("unit", "integration"):
            directory = tmp_path / "inputs" / system / suite
            directory.mkdir(parents=True)
            (directory / "manifest.json").write_text("{}")
            records[(system, suite)] = directory / ".coverage"
    output = tmp_path / "out"
    (output / "combined").mkdir(parents=True)
    (output / "combined/combined.json").write_text('{"passed": true}')

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("combine_coverage.py", 60)

    monkeypatch.setattr(quality.subprocess, "run", timeout)
    result = quality.run_aggregate(records, output)
    assert result["status"] == "failed"
    assert not (output / "combined/combined.json").exists()
    assert "timed out" in (output / "aggregate.log").read_text()

    class Completed:
        returncode = 0
        stdout = "combined\n"
        stderr = ""

    def combine(command: list[str], **_kwargs: object) -> Completed:
        destination = Path(command[command.index("--output-dir") + 1])
        destination.mkdir(parents=True)
        (destination / "combined.json").write_text('{"passed": true}')
        return Completed()

    monkeypatch.setattr(quality.subprocess, "run", combine)
    assert quality.run_aggregate(records, output)["status"] == "passed"
    assert "combined" in (output / "aggregate.log").read_text()


def test_focused_lint_reports_findings_and_repairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruff findings remain named failures; clean JSON clears that same check."""

    class Completed:
        def __init__(self, code: int, output: str) -> None:
            self.returncode = code
            self.stdout = output
            self.stderr = ""

    calls: list[list[str]] = []

    def finding(command: list[str], **_kwargs: object) -> Completed:
        calls.append(command)
        return Completed(1, '[{"code":"PT011","message":"broad exception"}]')

    monkeypatch.setattr(quality.subprocess, "run", finding)
    failed = quality.run_lint(tmp_path)
    assert failed["status"] == "failed"
    assert failed["detail"]["findings"][0]["code"] == "PT011"
    assert calls[0][calls[0].index("--select") + 1] == quality.LINT_RULES
    monkeypatch.setattr(quality.subprocess, "run", lambda *_args, **_kwargs: Completed(0, "[]"))
    assert quality.run_lint(tmp_path)["status"] == "passed"

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("ruff", 20)

    monkeypatch.setattr(quality.subprocess, "run", timeout)
    assert quality.run_lint(tmp_path)["status"] == "failed"
    monkeypatch.setattr(quality.subprocess, "run", lambda *_args, **_kwargs: Completed(1, "oops"))
    malformed = quality.run_lint(tmp_path)
    assert malformed["status"] == "failed"
    assert malformed["detail"]["error"] == "invalid Ruff output"
    monkeypatch.setattr(quality.subprocess, "run", lambda *_args, **_kwargs: Completed(0, "{}"))
    assert quality.run_lint(tmp_path)["detail"]["error"] == "invalid Ruff output"


def test_advisory_reports_show_native_totals_and_diff_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unavailable diff does not erase verified native suite totals."""
    unavailable = quality.advisory_reports(None, None)
    assert unavailable["suite_trends"]["status"] == "unavailable"
    assert unavailable["changed_code"]["status"] == "unavailable"

    directory = tmp_path / "Linux/unit"
    directory.mkdir(parents=True)
    (directory / "coverage.json").write_text(json.dumps({"totals": {"covered_lines": 42}}))

    class Completed:
        returncode = 1
        stderr = "bad base"
        stdout = ""

    monkeypatch.setattr(quality.subprocess, "run", lambda *_args, **_kwargs: Completed())
    advisory = quality.advisory_reports({("Linux", "unit"): directory / ".coverage"}, "base")
    assert advisory["suite_trends"]["Linux/unit"] == {"covered_lines": 42}
    assert advisory["changed_code"] == {"status": "unavailable", "reason": "bad base"}

    class Success:
        returncode = 0
        stderr = ""
        stdout = "diff --git a/src/agent_company/a.py b/src/agent_company/a.py\n"

    monkeypatch.setattr(quality.subprocess, "run", lambda *_args, **_kwargs: Success())
    advisory = quality.advisory_reports({("Linux", "unit"): directory / ".coverage"}, "base")
    assert advisory["changed_code"]["status"] == "available"
    assert advisory["changed_code"]["base"] == "base"
    assert "diff --git" in advisory["changed_code"]["diff"]

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("git diff", 10)

    monkeypatch.setattr(quality.subprocess, "run", timeout)
    advisory = quality.advisory_reports({("Linux", "unit"): directory / ".coverage"}, "base")
    assert advisory["changed_code"]["status"] == "unavailable"
    assert "timed out" in advisory["changed_code"]["reason"]


def test_main_continues_independent_checks_after_evidence_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing native records cannot suppress lint, probes, or the final summary."""
    output = tmp_path / "quality"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "test_quality.py",
            str(tmp_path / "native"),
            "--tooling-dir",
            str(tmp_path / "tooling"),
            "--output-dir",
            str(output),
        ],
    )
    monkeypatch.setattr(
        quality.dev,
        "checkout_state",
        lambda: {"sha": "candidate", "tracked_digest": "bytes", "status": ""},
    )

    def missing_native(*_args: object) -> None:
        raise ValueError("native missing")

    def missing_tooling(*_args: object) -> None:
        raise ValueError("tooling missing")

    monkeypatch.setattr(quality, "inspect_native", missing_native)
    monkeypatch.setattr(quality, "valid_tooling", missing_tooling)
    calls: list[str] = []

    def lint(_output: Path) -> dict[str, object]:
        calls.append("lint")
        return {"name": "focused_lint", "status": "passed", "seconds": 0.0, "detail": []}

    def probes(_spec: Path, _output: Path) -> dict[str, object]:
        calls.append("probes")
        return {"name": "assertion_probes", "status": "passed", "seconds": 0.0, "detail": []}

    monkeypatch.setattr(quality, "run_lint", lint)
    monkeypatch.setattr(quality, "run_probes", probes)
    monkeypatch.setattr(quality, "advisory_reports", lambda *_args: {})
    assert quality.main() == 1
    report = json.loads((output / "quality.json").read_text())
    status = {row["name"]: row["status"] for row in report["results"]}
    assert status["candidate_identity"] == "passed"
    assert status["native_evidence"] == "failed"
    assert status["tooling_unit_evidence"] == "failed"
    assert status["aggregate_80"] == status["function_obligations"] == "blocked"
    assert status["focused_lint"] == status["assertion_probes"] == "passed"
    assert calls == ["lint", "probes"]
    assert report["passed"] is False

    records = {("Linux", "unit"): tmp_path / "native/Linux/unit/.coverage"}
    monkeypatch.setattr(quality, "inspect_native", lambda *_args: records)
    monkeypatch.setattr(quality, "valid_tooling", lambda *_args: {"Linux": {}})
    monkeypatch.setattr(
        quality,
        "run_aggregate",
        lambda *_args: {"name": "aggregate_80", "status": "passed", "seconds": 0.0, "detail": {}},
    )
    monkeypatch.setattr(quality, "exception_records", lambda *_args: [])
    monkeypatch.setattr(quality, "function_gaps", lambda *_args: [])
    assert quality.main() == 0
    report = json.loads((output / "quality.json").read_text())
    assert report["passed"] is True
    assert json.loads((output / "function-gaps.json").read_text()) == []
    assert calls == ["lint", "probes", "lint", "probes"]

    monkeypatch.setattr(quality, "function_gaps", lambda *_args: [{"symbol": "missing"}])
    assert quality.main() == 1
    report = json.loads((output / "quality.json").read_text())
    status = {row["name"]: row["status"] for row in report["results"]}
    assert status["function_obligations"] == "failed"
    assert json.loads((output / "function-gaps.json").read_text()) == [{"symbol": "missing"}]

    monkeypatch.setattr(quality, "valid_tooling", missing_tooling)
    assert quality.main() == 1
    report = json.loads((output / "quality.json").read_text())
    status = {row["name"]: row["status"] for row in report["results"]}
    assert status["aggregate_80"] == "passed"
    assert status["function_obligations"] == "blocked"
    assert not (output / "function-gaps.json").exists()
    assert calls == ["lint", "probes"] * 4

    (output / "combined").mkdir()
    (output / "aggregate-inputs").mkdir()
    monkeypatch.setattr(
        quality.dev,
        "checkout_state",
        lambda: {"sha": "candidate", "tracked_digest": "bytes", "status": " M file.py"},
    )
    monkeypatch.setattr(quality, "valid_tooling", lambda *_args: {"Linux": {}})

    def invalid_ledger(*_args: object) -> None:
        raise ValueError("ledger stale")

    def invalid_probe(*_args: object) -> None:
        raise ValueError("probe stale")

    def invalid_advisory(*_args: object) -> None:
        raise ValueError("base unavailable")

    monkeypatch.setattr(quality, "exception_records", invalid_ledger)
    monkeypatch.setattr(quality, "run_probes", invalid_probe)
    monkeypatch.setattr(quality, "advisory_reports", invalid_advisory)
    assert quality.main() == 1
    report = json.loads((output / "quality.json").read_text())
    status = {row["name"]: row["status"] for row in report["results"]}
    assert status["candidate_identity"] == "failed"
    assert status["function_obligations"] == "failed"
    assert status["assertion_probes"] == "failed"
    assert report["advisory"]["status"] == "unavailable"
    assert not (output / "combined").exists()
    assert not (output / "aggregate-inputs").exists()


def test_main_rejects_output_inside_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quality run cannot overwrite source or test files in its checkout."""
    monkeypatch.setattr(quality, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "test_quality.py",
            str(tmp_path / "native"),
            "--tooling-dir",
            str(tmp_path / "tooling"),
            "--output-dir",
            str(tmp_path / "source/output"),
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        quality.main()
    assert not (tmp_path / "source/output").exists()
