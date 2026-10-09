"""Combine successful Windows and Linux evidence at the configured coverage floor."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path

from coverage import Coverage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import dev  # noqa: E402
from scripts.coverage_evidence import valid_matrix  # noqa: E402


def verified_inputs(directory: Path, sha: str, tracked_digest: str) -> list[str]:
    """Require successful platform results from the same exact clean candidate before combining.

    Args:
        directory: Downloaded per-platform artifact directories.
        sha: Exact candidate expected by the current checkout.
        tracked_digest: Tracked bytes required in both native inputs and the combining checkout.

    Returns:
        Paths to the retained platform coverage databases.

    Raises:
        ValueError: A platform, SHA, clean checkout, or successful test result is missing.
    """
    files: list[str] = []
    systems: dict[str, set[str]] = {}
    for path in sorted(directory.rglob("manifest.json")):
        # The collector manifest is canonical; workflow host.json is supplemental diagnostics.
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError(f"Malformed platform evidence: {path}")
        commands = manifest.get("commands", [])
        if (
            not isinstance(commands, list)
            or not commands
            or any(
                not isinstance(command, dict)
                or type(command.get("exit_code")) is not int
                or command["exit_code"] != 0
                or not isinstance(command.get("task"), str)
                for command in commands
            )
        ):
            raise ValueError(f"Missing, malformed or failed commands: {path}")
        if (
            manifest.get("sha") != sha
            or manifest.get("status") != ""
            or manifest.get("end_sha") != sha
            or manifest.get("end_status") != ""
            or manifest.get("candidate_unchanged") is not True
            or not tracked_digest
            or manifest.get("tracked_digest") != tracked_digest
            or manifest.get("tracked_digest") != manifest.get("end_tracked_digest")
        ):
            raise ValueError(f"Incomplete or mismatched platform evidence: {path}")
        system = manifest["system"]
        suite_tasks = [command["task"] for command in commands if command["task"] in dev.TEST_TASKS]
        if (
            not isinstance(system, str)
            or system not in {"Windows", "Linux"}
            or len(suite_tasks) != 1
        ):
            raise ValueError(f"Unexpected platform or test suite: {path}")
        suites = set(suite_tasks)
        if systems.setdefault(system, set()) & suites:
            raise ValueError(f"Duplicate native test suite: {path}")
        coverage_file = path.parent / ".coverage"
        if not coverage_file.is_file():
            raise ValueError(f"Missing coverage database: {coverage_file}")
        systems[system].update(suites)
        files.append(str(coverage_file))
    if set(systems) != {"Windows", "Linux"} or any(
        suites not in ({"test"}, {"test-unit", "test-integration"}) for suites in systems.values()
    ):
        raise ValueError("Both native Windows and Linux require complete successful test suites")
    return files


def main() -> int:
    """Combine native data and enforce the unchanged repository aggregate policy.

    Returns:
        Zero when unchanged combined coverage reaches the configured floor, otherwise one.

    Raises:
        ValueError: The combining checkout is dirty or native provenance does not match.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    initial = dev.checkout_state()
    if initial["status"]:
        raise ValueError("Combining checkout must be clean")
    records = valid_matrix(
        args.artifacts.resolve(),
        sha=initial["sha"],
        tracked_digest=initial["tracked_digest"],
        config_digest=hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest(),
        tool_versions={
            name: importlib.metadata.version(name) for name in ("coverage", "pytest", "pytest-cov")
        },
        systems={"Windows", "Linux"},
        allow_full=True,
        source_root=ROOT,
    )
    inputs = [str(path) for path in records.values()]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    coverage = Coverage(
        data_file=str(output / ".coverage"), config_file=str(ROOT / "pyproject.toml")
    )
    coverage.combine(inputs, strict=True, keep=True)
    coverage.save()
    total = coverage.report()
    coverage.xml_report(outfile=str(output / "coverage.xml"))
    coverage.json_report(outfile=str(output / "coverage.json"), show_contexts=True)
    floor = coverage.get_option("report:fail_under")
    if not isinstance(floor, (int, float)) or isinstance(floor, bool):
        raise ValueError("Coverage floor must be numeric")
    final = dev.checkout_state()
    unchanged = initial == final
    result = {
        **initial,
        **{"end_" + key: value for key, value in final.items()},
        "candidate_unchanged": unchanged,
        "platforms": ["Windows", "Linux"],
        "inputs": inputs,
        "coverage_percent": total,
        "required_percent": floor,
        "passed": unchanged and total >= floor,
    }
    (output / "combined.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
