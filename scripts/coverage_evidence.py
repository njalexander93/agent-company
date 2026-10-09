"""Validate one exact native suite record before combining or quality analysis."""

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast

from coverage import CoverageData
from coverage.exceptions import DataError

SYSTEMS = {"Linux", "Windows", "Darwin"}
SUITES = {
    "test": "test",
    "test-unit": "unit",
    "test-integration": "integration",
    "test-tooling-unit": "tooling-unit",
}
ARTIFACTS = {
    ".coverage",
    "coverage.xml",
    "coverage.json",
    "tests.xml",
    "pytest-evidence.json",
    "instrumentation.json",
}
PROBE_LINES = {1, 2, 3, 4}
PROBE_ARCS = {(2, 3), (2, 4)}
WINDOWS_FILESYSTEM = "tests/integration/lifecycle/test_filesystem.py::"
WINDOWS_UNIT_NODES = (
    "tests/unit/lifecycle/test_windows_contracts.py::test_native_layout_and_declarations",
    "tests/unit/lifecycle/test_windows_contracts.py::test_api_sets_native_signature",
    "tests/unit/lifecycle/test_windows_contracts.py::test_checked_preserves_native_error",
    "tests/unit/lifecycle/test_windows_contracts.py::test_open_handle_uses_non_delete_sharing_and_security",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_rejects_unsafe_native_identity[wrong-type]",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_rejects_unsafe_native_identity[reparse]",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_rejects_unsafe_native_identity[hard-link]",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_rejects_unsafe_native_identity[zero-index]",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_allows_explicit_reparse_inspection",
    "tests/unit/lifecycle/test_windows_contracts.py::test_information_surfaces_native_query_failure",
    "tests/unit/lifecycle/test_windows_contracts.py::test_exact_handle_path_rejects_alias_and_truncation",
    "tests/unit/lifecycle/test_windows_contracts.py::test_directory_init_releases_pins_on_validation_failure",
    "tests/unit/lifecycle/test_windows_contracts.py::test_directory_context_and_repeated_close",
    "tests/unit/lifecycle/test_windows_contracts.py::test_directory_entry_rejects_closed_and_case_alias",
    "tests/unit/lifecycle/test_windows_contracts.py::test_names_exists_and_flush_use_pinned_directory",
    "tests/unit/lifecycle/test_windows_contracts.py::test_child_closes_result_when_private_acl_fails",
    "tests/unit/lifecycle/test_windows_contracts.py::test_file_releases_handle_after_identity_rejection",
    "tests/unit/lifecycle/test_windows_contracts.py::test_read_rejects_declared_oversize_before_handle_transfer",
    "tests/unit/lifecycle/test_windows_contracts.py::test_unlink_and_rmdir_validate_before_deletion",
    "tests/unit/lifecycle/test_windows_contracts.py::test_rename_rejects_existing_target_without_move",
    "tests/unit/lifecycle/test_windows_contracts.py::test_lock_releases_handle_on_body_error",
    "tests/unit/lifecycle/test_windows_contracts.py::test_lock_timeout_keeps_persistent_inode",
    "tests/unit/lifecycle/test_windows_failures.py::test_extended_path_uses_local_namespace",
    "tests/unit/lifecycle/test_windows_failures.py::test_open_handle_surfaces_native_failure_without_accepting_invalid_handle",
    "tests/unit/lifecycle/test_windows_failures.py::test_information_rejects_non_disk_handle",
    "tests/unit/lifecycle/test_windows_junction.py::test_issue_view_rejects_nonjunction_and_closes_handle",
    "tests/unit/lifecycle/test_windows_junction.py::test_issue_view_rejects_foreign_reparse_payload",
    "tests/unit/lifecycle/test_windows_junction.py::test_issue_view_accepts_exact_mount_point_data",
    "tests/unit/lifecycle/test_windows_junction.py::test_issue_view_creation_race_removes_only_temporary_junction",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_rejects_remote_drive_before_query",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_rejects_unc_namespace_before_kernel_query",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_requires_ntfs_acl_and_reparse[NTFS-136-True]",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_requires_ntfs_acl_and_reparse[FAT32-136-False]",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_requires_ntfs_acl_and_reparse[NTFS-128-False]",
    "tests/unit/lifecycle/test_windows_operations.py::test_volume_surfaces_native_information_failure",
    "tests/unit/lifecycle/test_windows_operations.py::test_absolute_pins_each_ancestor_and_releases_after_failure",
    "tests/unit/lifecycle/test_windows_operations.py::test_absolute_retains_chain_and_init_records_identity",
    "tests/unit/lifecycle/test_windows_operations.py::test_absolute_rejects_device_component_before_opening",
    "tests/unit/lifecycle/test_windows_operations.py::test_child_create_preserves_existing_and_checks_private_acl",
    "tests/unit/lifecycle/test_windows_operations.py::test_file_opens_exclusive_private_handle",
    "tests/unit/lifecycle/test_windows_operations.py::test_read_validated_bytes_and_rejects_stream_growth",
    "tests/unit/lifecycle/test_windows_operations.py::test_write_flushes_before_publish_and_cleans_temp",
    "tests/unit/lifecycle/test_windows_operations.py::test_rename_rejects_cross_volume_and_moves_only_after_check",
    "tests/unit/lifecycle/test_windows_operations.py::test_lock_reopens_persistent_inode_and_retries_contention",
    "tests/unit/lifecycle/test_windows_operations.py::test_lock_surfaces_noncontention_error_and_releases_handle",
    "tests/unit/lifecycle/test_windows_operations.py::test_write_releases_temp_after_failed_publication",
    "tests/unit/lifecycle/test_windows_operations.py::test_child_create_surfaces_native_denial_without_opening",
    "tests/unit/lifecycle/test_windows_operations.py::test_read_closes_handle_when_descriptor_transfer_fails",
    "tests/unit/lifecycle/test_windows_operations.py::test_write_validates_existing_destination_before_temporary_creation",
    "tests/unit/lifecycle/test_windows_operations.py::test_unlink_surfaces_delete_denial_after_verified_handle_close",
    "tests/unit/lifecycle/test_windows_operations.py::test_rmdir_surfaces_native_denial_after_child_check",
    "tests/unit/lifecycle/test_windows_security.py::test_sid_text_frees_native_string",
    "tests/unit/lifecycle/test_windows_security.py::test_token_sid_uses_requested_class_and_closes_token",
    "tests/unit/lifecycle/test_windows_security.py::test_private_security_protects_descriptor_and_frees_it",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-False-0-0-0-foreign-None]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[owner-True-0-0-1073741824-user-None]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-True-0-0-1073741824-foreign-UNSAFE_PATH]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-True-0-8-1073741824-foreign-None]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-True-0-0-1-foreign-None]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-True-1-0-1073741824-foreign-None]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[user-True-9-0-0-foreign-UNSAFE_PATH]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_owner_and_ace_policy[foreign-False-0-0-0-foreign-UNSAFE_PATH]",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_rejects_null_dacl",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_reports_native_query_code",
    "tests/unit/lifecycle/test_windows_security.py::test_validate_security_releases_descriptor_when_ace_read_fails",
    "tests/unit/lifecycle/test_windows_security.py::test_token_sid_closes_token_after_information_failure",
    "tests/unit/lifecycle/test_windows_security.py::test_private_security_rejects_descriptor_conversion_failure",
    "tests/unit/lifecycle/test_windows_security.py::test_sid_text_rejects_conversion_failure_without_free",
    "tests/unit/lifecycle/test_windows_security.py::test_token_sid_rejects_token_open_failure_without_close",
)
WINDOWS_UNIT_REASON = "Windows native CreateFileW and NTFS handle APIs are unavailable on POSIX"
APPROVED_NATIVE_SKIPS: dict[str, tuple[set[str], str]] = {
    **{node: ({"Linux", "Darwin"}, WINDOWS_UNIT_REASON) for node in WINDOWS_UNIT_NODES},
    "tests/integration/adapters/test_platform_processes.py::"
    "test_windows_bootstrap_survives_literal_paths_and_json": (
        {"Linux", "Darwin"},
        "Native Windows PowerShell transport; POSIX has shell tests",
    ),
    WINDOWS_FILESYSTEM + "test_windows_case_alias_and_pinned_ancestor": (
        {"Linux", "Darwin"},
        "Requires native Windows NTFS handles and ACLs",
    ),
    WINDOWS_FILESYSTEM + "test_windows_foreign_write_acl_is_rejected": (
        {"Linux", "Darwin"},
        "Requires native Windows security descriptors",
    ),
    WINDOWS_FILESYSTEM + "test_windows_noncanonical_roots_are_rejected": (
        {"Linux", "Darwin"},
        "Requires native Windows path namespaces",
    ),
    WINDOWS_FILESYSTEM + "test_windows_competing_process_cannot_redirect_held_ancestor": (
        {"Linux", "Darwin"},
        "Requires native Windows cross-process sharing",
    ),
    "tests/integration/lifecycle/test_posix_boundaries.py::"
    "test_held_directory_stays_on_original_inode_after_path_replacement": (
        {"Windows"},
        "Requires native POSIX directory descriptors",
    ),
    "tests/integration/lifecycle/test_posix_boundaries.py::"
    "test_group_writable_file_is_rejected_without_replacement": (
        {"Windows"},
        "Requires native POSIX directory descriptors",
    ),
}


def digest(path: Path) -> str:
    """Return the SHA-256 digest of retained bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_suite(
    directory: Path,
    *,
    sha: str,
    tracked_digest: str,
    config_digest: str,
    tool_versions: dict[str, str],
    require_clean: bool = True,
) -> tuple[str, str, Path]:
    """Reject stale, partial, failed, uninstrumented or malformed native evidence."""
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        commands = manifest["commands"]
        if (
            not isinstance(commands, list)
            or not commands
            or any(not isinstance(row, dict) for row in commands)
        ):
            raise ValueError("missing or malformed suite commands")
        test_commands = [row for row in commands if row.get("task") in SUITES]
        if len(test_commands) != 1:
            raise ValueError("exactly one suite command is required")
        command = test_commands[0]
        suite = SUITES[command["task"]]
        tasks = [row.get("task") for row in commands]
        full_checks = [
            "validate-config",
            "validate-config",
            "format-check",
            "lint",
            "type-check",
            "test",
        ]
        if (
            suite != "test"
            and len(commands) != 1
            or suite == "test"
            and tasks not in (["test"], full_checks)
        ):
            raise ValueError("unexpected suite command sequence")
        logs: set[str] = set()
        counts: dict[str, int] = {}
        for row in commands:
            task = row["task"]
            log = f"{task}-{counts.get(task, 0)}.log"
            counts[task] = counts.get(task, 0) + 1
            if (
                row.get("log") != log
                or type(row.get("exit_code")) is not int
                or row["exit_code"] != 0
                or type(row.get("seconds")) not in {int, float}
                or not math.isfinite(row["seconds"])
                or row["seconds"] < 0
            ):
                raise ValueError("failed or malformed suite command")
            logs.add(log)
        system = manifest["system"]
        if system not in SYSTEMS:
            raise ValueError("unknown native system")
        if suite != "test" and directory.name != suite:
            raise ValueError("suite directory disagrees with command")
        if manifest.get("sha") != sha or manifest.get("end_sha") != sha:
            raise ValueError("stale candidate SHA")
        if (
            not tracked_digest
            or manifest.get("tracked_digest") != tracked_digest
            or manifest.get("end_tracked_digest") != tracked_digest
            or manifest.get("candidate_unchanged") is not True
        ):
            raise ValueError("source bytes changed or mismatch")
        if require_clean and (manifest.get("status") != "" or manifest.get("end_status") != ""):
            raise ValueError("candidate was not clean")
        if manifest.get("config_sha256") != config_digest:
            raise ValueError("coverage configuration mismatch")
        if manifest.get("tool_versions") != tool_versions:
            raise ValueError("test tool versions mismatch")
        artifacts = manifest.get("artifacts_sha256")
        tooling = suite == "tooling-unit"
        expected_artifacts = (ARTIFACTS - {"instrumentation.json"} if tooling else ARTIFACTS) | logs
        if not tooling:
            expected_artifacts.add("instrumentation.log")
        if not isinstance(artifacts, dict) or set(artifacts) != expected_artifacts:
            raise ValueError("missing or unexpected artifact receipt")
        for name in expected_artifacts:
            path = directory / name
            if not path.is_file() or digest(path) != artifacts[name]:
                raise ValueError(f"missing or changed artifact: {name}")
        if not tooling:
            smoke = json.loads((directory / "instrumentation.json").read_text(encoding="utf-8"))
            if (
                smoke.get("passed") is not True
                or smoke.get("instrumented") is not True
                or smoke.get("branch_data") is not True
                or type(smoke.get("test_exit_code")) is not int
                or smoke["test_exit_code"] != 0
                or smoke.get("config_sha256") != config_digest
                or set(smoke.get("expected_lines", [])) != PROBE_LINES
                or {tuple(arc) for arc in smoke.get("expected_arcs", [])} != PROBE_ARCS
                or not PROBE_LINES <= set(smoke["child_lines"])
                or not PROBE_ARCS <= {tuple(arc) for arc in smoke["child_arcs"]}
            ):
                raise ValueError("child instrumentation proof failed")
        data = CoverageData(basename=str(directory / ".coverage"))
        try:
            data.read()
            if not data.has_arcs() or not data.measured_files():
                raise ValueError("missing branch database or measured source")
            report = json.loads((directory / "coverage.json").read_text(encoding="utf-8"))
            if (
                report.get("meta", {}).get("branch_coverage") is not True
                or report["meta"].get("version") != tool_versions["coverage"]
                or not report.get("files")
            ):
                raise ValueError("missing branch JSON or source rows")
            for source in data.measured_files():
                normalized = source.replace("\\", "/")
                row = report["files"].get(normalized)
                if row is None:
                    raise ValueError(f"measured file missing from JSON: {source}")
                executed = set(row["executed_lines"])
                executable = executed | set(row["missing_lines"])
                raw_lines = set(data.lines(source) or [])
                if executed != raw_lines & executable:
                    raise ValueError(f"JSON lines disagree with database: {source}")
                raw_arcs = set(data.arcs(source) or [])
                reported_arcs = {tuple(arc) for arc in row["executed_branches"]}
                if not reported_arcs <= raw_arcs:
                    raise ValueError(f"JSON branches disagree with database: {source}")
        finally:
            data.close()
        xml_root = ET.parse(directory / "coverage.xml").getroot()
        if xml_root.tag != "coverage" or not xml_root.findall("packages/package/classes/class"):
            raise ValueError("invalid or empty coverage XML")
        totals = report["totals"]
        if (
            int(xml_root.attrib["lines-valid"]) != totals["num_statements"]
            or int(xml_root.attrib["lines-covered"]) != totals["covered_lines"]
            or int(xml_root.attrib["branches-valid"]) != totals["num_branches"]
            or int(xml_root.attrib["branches-covered"]) != totals["covered_branches"]
        ):
            raise ValueError("XML totals disagree with JSON")
        junit = ET.parse(directory / "tests.xml").getroot()
        cases = junit.findall(".//testcase")
        if junit.tag not in {"testsuite", "testsuites"} or not cases:
            raise ValueError("empty JUnit results")
        outcomes = json.loads((directory / "pytest-evidence.json").read_text(encoding="utf-8"))
        if outcomes.get("schema") != 1 or outcomes.get("exit_code") != 0:
            raise ValueError("failed or malformed pytest outcome record")
        collected = outcomes.get("collected")
        reports = outcomes.get("reports")
        if not isinstance(collected, list) or not collected or not isinstance(reports, dict):
            raise ValueError("zero or malformed collection")
        ids = [item["nodeid"] for item in collected]
        if len(ids) != len(set(ids)) or set(reports) != set(ids):
            raise ValueError("duplicate or missing outcome IDs")
        suites = [junit] if junit.tag == "testsuite" else junit.findall("testsuite")
        if (
            sum(int(row.attrib["tests"]) for row in suites) != len(cases)
            or len(cases) < len(ids)
            or any(
                int(row.attrib.get(key, "0")) for row in suites for key in ("errors", "failures")
            )
        ):
            raise ValueError("JUnit outcomes disagree with collection")
        if outcomes.get("collection_errors"):
            raise ValueError("collection errors")
        executed_cases = 0
        full_executed: set[str] = set()
        for item in collected:
            nodeid = item["nodeid"]
            suite_markers = item.get("suite_markers")
            if suite == "test":
                marker = (
                    suite_markers[0] if isinstance(suite_markers, list) and suite_markers else None
                )
            else:
                marker = "unit" if tooling else suite
            prefix = "tests/unit/tooling/" if tooling else f"tests/{marker}/"
            if suite == "test" and marker not in {"unit", "integration"}:
                raise ValueError(f"unmarked or misclassified test: {nodeid}")
            if item.get("suite_markers") != [marker] or not nodeid.startswith(prefix):
                raise ValueError(f"unmarked or misclassified test: {nodeid}")
            phases = reports[nodeid]
            if not isinstance(phases, list) or not phases:
                raise ValueError(f"missing test outcome: {nodeid}")
            phase_names = [phase["phase"] for phase in phases]
            primary = [phase["phase"] for phase in phases if "subtest_index" not in phase]
            subtests = [phase for phase in phases if "subtest_index" in phase]
            if (
                len(primary) != len(set(primary))
                or phase_names[0] != "setup"
                or any(phase["phase"] != "call" for phase in subtests)
                or [phase["subtest_index"] for phase in subtests] != list(range(len(subtests)))
            ):
                raise ValueError(f"duplicate or incomplete phases: {nodeid}")
            if any(phase.get("outcome") == "failed" or "wasxfail" in phase for phase in phases):
                raise ValueError(f"failed or expected-failure outcome: {nodeid}")
            if any(phase.get("outcome") == "skipped" for phase in phases):
                approved = APPROVED_NATIVE_SKIPS.get(nodeid)
                if (
                    not approved
                    or system not in approved[0]
                    or item.get("skip_reasons") != [approved[1]]
                ):
                    raise ValueError(f"unapproved skip: {nodeid}")
            elif any(
                phase.get("phase") == "call" and phase.get("outcome") == "passed"
                for phase in phases
            ):
                if "teardown" not in phase_names:
                    raise ValueError(f"missing teardown outcome: {nodeid}")
                executed_cases += 1
                if suite == "test":
                    full_executed.add(cast(str, marker))
            else:
                raise ValueError(f"no executed outcome: {nodeid}")
        if executed_cases == 0:
            raise ValueError("all tests skipped")
        if suite == "test" and full_executed != {"unit", "integration"}:
            raise ValueError("full suite did not execute both unit and integration tests")
        return system, suite, directory / ".coverage"
    except (
        OSError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        DataError,
        json.JSONDecodeError,
        ET.ParseError,
    ) as error:
        raise ValueError(f"Malformed native evidence: {directory}: {error}") from error


def valid_matrix(
    root: Path,
    *,
    sha: str,
    tracked_digest: str,
    config_digest: str,
    tool_versions: dict[str, str],
    systems: set[str] = SYSTEMS,
    allow_full: bool = False,
) -> dict[tuple[str, str], Path]:
    """Require exact split records, or allow one full suite per OS for legacy combine."""
    records: dict[tuple[str, str], Path] = {}
    for path in sorted(root.rglob("manifest.json")):
        system, suite, database = valid_suite(
            path.parent,
            sha=sha,
            tracked_digest=tracked_digest,
            config_digest=config_digest,
            tool_versions=tool_versions,
        )
        key = (system, suite)
        if key in records:
            raise ValueError(f"Duplicate native suite: {system}/{suite}")
        records[key] = database
    expected = {(system, suite) for system in systems for suite in ("unit", "integration")}
    if allow_full:
        complete = (
            all(
                {suite for observed, suite in records if observed == system}
                in ({"test"}, {"unit", "integration"})
                for system in systems
            )
            and {system for system, _ in records} == systems
        )
    else:
        complete = set(records) == expected
    if not complete:
        raise ValueError(
            f"Incomplete native matrix: missing={sorted(expected - set(records))}; "
            f"unexpected={sorted(set(records) - expected)}"
        )
    return records
