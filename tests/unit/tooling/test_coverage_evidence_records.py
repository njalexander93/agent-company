"""Reject fabricated completeness in disposable native coverage records."""

import json
from pathlib import Path

import pytest
from coverage import CoverageData

from scripts.coverage_evidence import ARTIFACTS, digest, valid_matrix, valid_suite

pytestmark = pytest.mark.unit
TOOLS = {"coverage": "7.16.2", "pytest": "9.1.1", "pytest-cov": "7.1.0"}


def record(root: Path, system: str, suite: str) -> Path:
    """Create a complete fixture record with an actual branch database."""
    directory = root / system / suite
    directory.mkdir(parents=True)
    data = CoverageData(basename=str(directory / ".coverage"))
    data.add_arcs({"src/agent_company/sample.py": [(-1, 1), (1, -1)]})
    data.write()
    data.close()
    (directory / "coverage.json").write_text(
        json.dumps(
            {
                "meta": {"branch_coverage": True, "version": "7.16.2"},
                "files": {
                    "src/agent_company/sample.py": {
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
        '<coverage lines-valid="1" lines-covered="1" branches-valid="0"'
        ' branches-covered="0"><packages><package><classes><class name="sample"/></classes>'
        "</package></packages></coverage>"
    )
    (directory / "tests.xml").write_text(
        '<testsuites><testsuite tests="1"><testcase name="test_sample"/></testsuite></testsuites>'
    )
    (directory / "instrumentation.json").write_text(
        json.dumps(
            {
                "passed": True,
                "instrumented": True,
                "branch_data": True,
                "test_exit_code": 0,
                "config_sha256": "config",
                "expected_lines": [1, 2, 3, 4],
                "child_lines": [1, 2, 3, 4],
                "expected_arcs": [[2, 3], [2, 4]],
                "child_arcs": [[2, 3], [2, 4]],
            }
        )
    )
    (directory / "instrumentation.log").write_text("child-only coverage: passed\n")
    command_log = f"test-{suite}-0.log"
    (directory / command_log).write_text("1 passed\n")
    nodeid = f"tests/{suite}/test_sample.py::test_sample"
    (directory / "pytest-evidence.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "exit_code": 0,
                "collection_errors": [],
                "collected": [{"nodeid": nodeid, "suite_markers": [suite], "skip_reasons": []}],
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
    manifest = {
        "sha": "candidate",
        "end_sha": "candidate",
        "system": system,
        "status": "",
        "end_status": "",
        "tracked_digest": "bytes",
        "end_tracked_digest": "bytes",
        "candidate_unchanged": True,
        "config_sha256": "config",
        "tool_versions": TOOLS,
        "commands": [{"task": f"test-{suite}", "exit_code": 0, "seconds": 1.0, "log": command_log}],
        "artifacts_sha256": {
            name: digest(directory / name)
            for name in ARTIFACTS | {"instrumentation.log", command_log}
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


def check(directory: Path) -> tuple[str, str, Path]:
    """Validate one fixture with its exact expected candidate identities."""
    return valid_suite(
        directory,
        sha="candidate",
        tracked_digest="bytes",
        config_digest="config",
        tool_versions=TOOLS,
    )


@pytest.mark.parametrize(
    "defect",
    [
        "missing-database",
        "corrupt-database",
        "no-branch",
        "missing-json",
        "wrong-sha",
        "wrong-tool",
        "wrong-config",
        "failed-suite",
        "dirty",
        "failed-smoke",
        "missing-arc",
        "zero-tests",
        "duplicate-node",
        "wrong-marker",
        "unknown-skip",
        "failed-test",
        "collection-error",
    ],
)
def test_native_record_rejects_invalid_evidence(tmp_path: Path, defect: str) -> None:
    """Detect each material evidence defect, including data corruption behind a valid hash."""
    directory = record(tmp_path, "Linux", "unit")
    assert check(directory) == ("Linux", "unit", directory / ".coverage")
    manifest = json.loads((directory / "manifest.json").read_text())
    if defect in {"missing-database", "corrupt-database", "no-branch"}:
        database = directory / ".coverage"
        if defect == "missing-database":
            database.unlink()
        elif defect == "corrupt-database":
            database.write_bytes(b"corrupt")
        else:
            database.unlink()
            data = CoverageData(basename=str(database))
            data.add_lines({"src/agent_company/sample.py": {1}})
            data.write()
            data.close()
    elif defect == "missing-json":
        (directory / "coverage.json").unlink()
    elif defect in {"wrong-sha", "wrong-tool", "wrong-config", "failed-suite", "dirty"}:
        key, value = {
            "wrong-sha": ("sha", "other"),
            "wrong-tool": ("tool_versions", {}),
            "wrong-config": ("config_sha256", "other"),
            "failed-suite": ("commands", [{"task": "test-unit", "exit_code": 1}]),
            "dirty": ("status", " M source.py"),
        }[defect]
        manifest[key] = value
    else:
        name = (
            "instrumentation.json"
            if defect in {"failed-smoke", "missing-arc"}
            else "pytest-evidence.json"
        )
        path = directory / name
        content = json.loads(path.read_text())
        nodeid = "tests/unit/test_sample.py::test_sample"
        if defect == "failed-smoke":
            content["passed"] = False
        elif defect == "missing-arc":
            content["child_arcs"] = []
        elif defect == "zero-tests":
            content["collected"] = []
        elif defect == "duplicate-node":
            content["collected"] *= 2
        elif defect == "wrong-marker":
            content["collected"][0]["suite_markers"] = ["integration"]
        elif defect == "unknown-skip":
            content["reports"][nodeid] = [{"phase": "setup", "outcome": "skipped"}]
        elif defect == "failed-test":
            content["reports"][nodeid] = [{"phase": "call", "outcome": "failed"}]
        elif defect == "collection-error":
            content["collection_errors"] = ["bad import"]
        path.write_text(json.dumps(content))
        manifest["artifacts_sha256"][name] = digest(path)
    if (directory / ".coverage").is_file():
        manifest["artifacts_sha256"][".coverage"] = digest(directory / ".coverage")
    (directory / "manifest.json").write_text(json.dumps(manifest))
    expected = {
        "missing-database": "missing or changed artifact",
        "corrupt-database": "Malformed native evidence",
        "no-branch": "missing branch database",
        "missing-json": "missing or changed artifact",
        "wrong-sha": "stale candidate SHA",
        "wrong-tool": "test tool versions mismatch",
        "wrong-config": "coverage configuration mismatch",
        "failed-suite": "failed or malformed suite command",
        "dirty": "candidate was not clean",
        "failed-smoke": "child instrumentation proof failed",
        "missing-arc": "child instrumentation proof failed",
        "zero-tests": "zero or malformed collection",
        "duplicate-node": "duplicate or missing outcome IDs",
        "wrong-marker": "unmarked or misclassified test",
        "unknown-skip": "unapproved skip",
        "failed-test": "duplicate or incomplete phases",
        "collection-error": "collection errors",
    }[defect]
    with pytest.raises(ValueError, match=expected):
        check(directory)


def test_matrix_requires_six_unique_native_suite_records(tmp_path: Path) -> None:
    """Require Darwin independently of the unchanged Linux/Windows aggregate."""
    for system in ("Linux", "Windows", "Darwin"):
        for suite in ("unit", "integration"):
            record(tmp_path, system, suite)
    matrix = valid_matrix(
        tmp_path,
        sha="candidate",
        tracked_digest="bytes",
        config_digest="config",
        tool_versions=TOOLS,
    )
    assert set(matrix) == {
        (system, suite)
        for system in ("Linux", "Windows", "Darwin")
        for suite in ("unit", "integration")
    }
    missing = tmp_path / "Darwin" / "unit" / "manifest.json"
    original = missing.read_bytes()
    missing.unlink()
    with pytest.raises(ValueError, match="Incomplete native matrix"):
        valid_matrix(
            tmp_path,
            sha="candidate",
            tracked_digest="bytes",
            config_digest="config",
            tool_versions=TOOLS,
        )
    missing.write_bytes(original)
    assert set(
        valid_matrix(
            tmp_path,
            sha="candidate",
            tracked_digest="bytes",
            config_digest="config",
            tool_versions=TOOLS,
        )
    ) == set(matrix)


def test_subtest_calls_are_distinct_but_duplicate_main_call_is_rejected(tmp_path: Path) -> None:
    """Keep unittest subtest results without accepting repeated parent outcomes."""
    directory = record(tmp_path, "Darwin", "integration")
    path = directory / "pytest-evidence.json"
    content = json.loads(path.read_text())
    nodeid = content["collected"][0]["nodeid"]
    content["reports"][nodeid].insert(
        1,
        {"phase": "call", "outcome": "passed", "subtest_index": 0, "subtest": "case (value=1)"},
    )
    path.write_text(json.dumps(content))
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts_sha256"]["pytest-evidence.json"] = digest(path)
    manifest_path.write_text(json.dumps(manifest))
    assert check(directory)[0:2] == ("Darwin", "integration")
    content["reports"][nodeid].insert(3, {"phase": "call", "outcome": "passed"})
    path.write_text(json.dumps(content))
    manifest["artifacts_sha256"]["pytest-evidence.json"] = digest(path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="duplicate or incomplete phases"):
        check(directory)


def test_posix_only_skip_is_approved_on_windows_with_exact_reason(tmp_path: Path) -> None:
    """Keep POSIX native coverage required on POSIX and a narrow Windows skip."""
    directory = record(tmp_path, "Windows", "integration")
    path = directory / "pytest-evidence.json"
    content = json.loads(path.read_text())
    skipped = (
        "tests/integration/lifecycle/test_posix_boundaries.py::"
        "test_group_writable_file_is_rejected_without_replacement"
    )
    normal = content["collected"][0]["nodeid"]
    content["collected"].insert(
        0,
        {
            "nodeid": skipped,
            "suite_markers": ["integration"],
            "skip_reasons": ["Requires native POSIX directory descriptors"],
        },
    )
    content["reports"][skipped] = [{"phase": "setup", "outcome": "skipped"}]
    path.write_text(json.dumps(content))
    (directory / "tests.xml").write_text(
        '<testsuites><testsuite tests="2"><testcase name="skip"><skipped/></testcase>'
        '<testcase name="test_sample"/></testsuite></testsuites>'
    )
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for name in ("pytest-evidence.json", "tests.xml"):
        manifest["artifacts_sha256"][name] = digest(directory / name)
    manifest_path.write_text(json.dumps(manifest))
    assert normal in content["reports"]
    assert check(directory)[0:2] == ("Windows", "integration")
    content["collected"][0]["skip_reasons"] = ["generic skip"]
    path.write_text(json.dumps(content))
    manifest["artifacts_sha256"]["pytest-evidence.json"] = digest(path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unapproved skip"):
        check(directory)
    content["collected"][0]["skip_reasons"] = ["Requires native POSIX directory descriptors"]
    path.write_text(json.dumps(content))
    manifest["artifacts_sha256"]["pytest-evidence.json"] = digest(path)
    manifest_path.write_text(json.dumps(manifest))
    assert check(directory)[0:2] == ("Windows", "integration")


def test_windows_unit_skip_uses_exact_parameter_and_native_applicability(tmp_path: Path) -> None:
    """A reviewed parameter case may skip on POSIX but no other case or native Windows."""
    directory = record(tmp_path, "Linux", "unit")
    path = directory / "pytest-evidence.json"
    content = json.loads(path.read_text())
    skipped = (
        "tests/unit/lifecycle/test_windows_contracts.py::"
        "test_information_rejects_unsafe_native_identity[wrong-type]"
    )
    reason = "Windows native CreateFileW and NTFS handle APIs are unavailable on POSIX"
    content["collected"].append(
        {"nodeid": skipped, "suite_markers": ["unit"], "skip_reasons": [reason]}
    )
    content["reports"][skipped] = [{"phase": "setup", "outcome": "skipped"}]
    path.write_text(json.dumps(content))
    (directory / "tests.xml").write_text(
        '<testsuites><testsuite tests="2"><testcase name="test_sample"/>'
        '<testcase name="skip"><skipped/></testcase></testsuite></testsuites>'
    )
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())

    def save() -> None:
        manifest["artifacts_sha256"]["pytest-evidence.json"] = digest(path)
        manifest["artifacts_sha256"]["tests.xml"] = digest(directory / "tests.xml")
        manifest_path.write_text(json.dumps(manifest))

    save()
    assert check(directory)[0:2] == ("Linux", "unit")
    content["collected"][-1]["nodeid"] = skipped.replace("wrong-type", "unreviewed-case")
    content["reports"][content["collected"][-1]["nodeid"]] = content["reports"].pop(skipped)
    path.write_text(json.dumps(content))
    save()
    with pytest.raises(ValueError, match="unapproved skip"):
        check(directory)
    content["collected"][-1]["nodeid"] = skipped
    content["reports"][skipped] = content["reports"].pop(
        skipped.replace("wrong-type", "unreviewed-case")
    )
    path.write_text(json.dumps(content))
    manifest["system"] = "Windows"
    save()
    with pytest.raises(ValueError, match="unapproved skip"):
        check(directory)


@pytest.mark.parametrize(
    "defect,reason",
    [
        ("manifest-array", "manifest must be an object"),
        ("empty-command", "missing or malformed suite commands"),
        ("duplicate-suite-command", "exactly one suite command"),
        ("wrong-directory", "suite directory disagrees"),
        ("wrong-system", "unknown native system"),
        ("artifact-index", "missing or unexpected artifact receipt"),
        ("branch-json", "missing branch JSON or source rows"),
        ("unmapped-file", "measured file missing from JSON"),
        ("line-disagreement", "JSON lines disagree with database"),
        ("arc-disagreement", "JSON branches disagree with database"),
    ],
)
def test_native_record_rejects_precise_receipt_corruption(
    tmp_path: Path, defect: str, reason: str
) -> None:
    """A valid file digest cannot excuse a malformed command or inconsistent coverage."""
    directory = record(tmp_path, "Linux", "unit")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if defect == "manifest-array":
        manifest_path.write_text("[]")
    elif defect == "empty-command":
        manifest["commands"] = []
    elif defect == "duplicate-suite-command":
        manifest["commands"] *= 2
    elif defect == "wrong-directory":
        destination = directory.parent / "integration"
        directory.rename(destination)
        directory = destination
        manifest_path = directory / "manifest.json"
    elif defect == "wrong-system":
        manifest["system"] = "Other"
    elif defect == "artifact-index":
        manifest["artifacts_sha256"].pop("tests.xml")
    else:
        report_path = directory / "coverage.json"
        report = json.loads(report_path.read_text())
        source = "src/agent_company/sample.py"
        if defect == "branch-json":
            report["meta"]["branch_coverage"] = False
        elif defect == "unmapped-file":
            report["files"]["src/agent_company/other.py"] = report["files"].pop(source)
        elif defect == "line-disagreement":
            report["files"][source]["executed_lines"] = [2]
        else:
            report["files"][source]["executed_branches"] = [[1, 2]]
        report_path.write_text(json.dumps(report))
        manifest["artifacts_sha256"]["coverage.json"] = digest(report_path)
    if defect != "manifest-array":
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=reason):
        check(directory)


@pytest.mark.parametrize(
    "defect,reason",
    [
        ("source-digest", "source bytes changed or mismatch"),
        ("wrong-command-order", "unexpected suite command sequence"),
        ("invalid-xml", "invalid or empty coverage XML"),
        ("xml-totals", "XML totals disagree with JSON"),
        ("empty-junit", "empty JUnit results"),
        ("failed-outcomes", "failed or malformed pytest outcome record"),
        ("junit-count", "JUnit outcomes disagree with collection"),
        ("missing-phases", "missing test outcome"),
        ("expected-failure", "failed or expected-failure outcome"),
        ("missing-teardown", "missing teardown outcome"),
        ("no-call", "no executed outcome"),
        ("all-skipped", "all tests skipped"),
    ],
)
def test_native_record_rejects_inconsistent_outcome_and_xml(
    tmp_path: Path, defect: str, reason: str
) -> None:
    """Successful command logs cannot hide failed or absent test execution."""
    directory = record(tmp_path, "Linux", "unit")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    changed: str | None = None
    if defect == "source-digest":
        manifest["end_tracked_digest"] = "different"
    elif defect == "wrong-command-order":
        manifest["commands"].append(
            {"task": "lint", "exit_code": 0, "seconds": 0.1, "log": "lint-0.log"}
        )
    elif defect in {"invalid-xml", "xml-totals"}:
        changed = "coverage.xml"
        path = directory / changed
        xml = path.read_text()
        path.write_text(
            "<other/>"
            if defect == "invalid-xml"
            else xml.replace('lines-covered="1"', 'lines-covered="0"')
        )
    elif defect == "empty-junit":
        changed = "tests.xml"
        (directory / changed).write_text('<testsuite tests="0"/>')
    else:
        changed = "pytest-evidence.json"
        path = directory / changed
        outcomes = json.loads(path.read_text())
        nodeid = outcomes["collected"][0]["nodeid"]
        if defect == "failed-outcomes":
            outcomes["exit_code"] = 1
        elif defect == "junit-count":
            (directory / "tests.xml").write_text(
                '<testsuite tests="2"><testcase name="test_sample"/></testsuite>'
            )
            manifest["artifacts_sha256"]["tests.xml"] = digest(directory / "tests.xml")
        elif defect == "missing-phases":
            outcomes["reports"][nodeid] = []
        elif defect == "expected-failure":
            outcomes["reports"][nodeid][1]["wasxfail"] = "expected"
        elif defect == "missing-teardown":
            outcomes["reports"][nodeid].pop()
        elif defect == "no-call":
            outcomes["reports"][nodeid].pop(1)
        else:
            skipped = (
                "tests/unit/lifecycle/test_windows_contracts.py::"
                "test_native_layout_and_declarations"
            )
            outcomes["collected"][0]["nodeid"] = skipped
            outcomes["collected"][0]["skip_reasons"] = [
                "Windows native CreateFileW and NTFS handle APIs are unavailable on POSIX"
            ]
            outcomes["reports"] = {skipped: [{"phase": "setup", "outcome": "skipped"}]}
            (directory / "tests.xml").write_text(
                '<testsuite tests="1"><testcase name="skip"><skipped/></testcase></testsuite>'
            )
            manifest["artifacts_sha256"]["tests.xml"] = digest(directory / "tests.xml")
        path.write_text(json.dumps(outcomes))
    if changed:
        manifest["artifacts_sha256"][changed] = digest(directory / changed)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=reason):
        check(directory)


def test_full_suite_requires_executed_unit_and_integration_nodes(tmp_path: Path) -> None:
    """A full receipt needs both suite markers and actual calls from both suites."""
    directory = record(tmp_path, "Linux", "test")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    (directory / "test-test-0.log").rename(directory / "test-0.log")
    manifest["commands"][0]["task"] = "test"
    manifest["commands"][0]["log"] = "test-0.log"
    manifest["artifacts_sha256"].pop("test-test-0.log")
    manifest["artifacts_sha256"]["test-0.log"] = digest(directory / "test-0.log")
    outcomes_path = directory / "pytest-evidence.json"
    outcomes = json.loads(outcomes_path.read_text())
    phases = outcomes["reports"].pop(outcomes["collected"][0]["nodeid"])
    unit = "tests/unit/test_sample.py::test_unit"
    integration = "tests/integration/test_sample.py::test_integration"
    outcomes["collected"] = [
        {"nodeid": unit, "suite_markers": ["unit"], "skip_reasons": []},
        {"nodeid": integration, "suite_markers": ["integration"], "skip_reasons": []},
    ]
    outcomes["reports"] = {unit: phases, integration: phases}
    junit_path = directory / "tests.xml"
    junit_path.write_text(
        '<testsuite tests="2"><testcase name="test_unit"/>'
        '<testcase name="test_integration"/></testsuite>'
    )

    def save() -> None:
        outcomes_path.write_text(json.dumps(outcomes))
        for name in ("pytest-evidence.json", "tests.xml"):
            manifest["artifacts_sha256"][name] = digest(directory / name)
        manifest_path.write_text(json.dumps(manifest))

    save()
    assert check(directory)[0:2] == ("Linux", "test")
    outcomes["collected"][1]["suite_markers"] = []
    save()
    with pytest.raises(ValueError, match="unmarked or misclassified test"):
        check(directory)
    outcomes["collected"].pop()
    outcomes["reports"].pop(integration)
    junit_path.write_text('<testsuite tests="1"><testcase name="test_unit"/></testsuite>')
    save()
    with pytest.raises(ValueError, match="full suite did not execute both"):
        check(directory)
