"""Combine successful Windows and Linux evidence at the configured coverage floor."""

import argparse
import json
import os
import subprocess
from pathlib import Path

from coverage import Coverage

ROOT = Path(__file__).resolve().parents[1]


def verified_inputs(directory: Path, sha: str) -> list[str]:
    """Require successful platform results from the same exact clean candidate before combining.

    Args:
        directory: Downloaded per-platform artifact directories.
        sha: Exact candidate expected by the current checkout.

    Returns:
        Paths to the retained platform coverage databases.

    Raises:
        ValueError: A platform, SHA, clean checkout, or successful test result is missing.
    """
    files: list[str] = []
    systems: set[str] = set()
    for path in sorted(directory.rglob("manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        host = json.loads((path.parent / "host.json").read_text(encoding="utf-8"))
        commands = manifest.get("commands", [])
        if (
            manifest.get("sha") != sha
            or host.get("sha") != sha
            or manifest.get("status") != ""
            or manifest.get("end_sha") != sha
            or manifest.get("end_status") != ""
            or manifest.get("candidate_unchanged") is not True
            or not manifest.get("tracked_digest")
            or manifest.get("tracked_digest") != manifest.get("end_tracked_digest")
            or not commands
            or any(command["exit_code"] != 0 for command in commands)
            or not any(command["task"] == "test" for command in commands)
            or host.get("system") != manifest.get("system")
        ):
            raise ValueError(f"Incomplete or mismatched platform evidence: {path}")
        system = manifest["system"]
        if system not in {"Windows", "Linux"} or system in systems:
            raise ValueError(f"Unexpected or duplicate native platform: {system}")
        coverage_file = path.parent / ".coverage"
        if not coverage_file.is_file():
            raise ValueError(f"Missing coverage database: {coverage_file}")
        systems.add(system)
        files.append(str(coverage_file))
    if systems != {"Windows", "Linux"}:
        raise ValueError("Both native Windows and Linux results are required")
    return files


def main() -> int:
    """Combine native data and enforce the unchanged repository aggregate policy.

    Returns:
        Zero when combined coverage reaches the configured floor, otherwise one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    inputs = verified_inputs(args.artifacts.resolve(), sha)
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
    result = {
        "sha": sha,
        "platforms": ["Windows", "Linux"],
        "inputs": inputs,
        "coverage_percent": total,
        "required_percent": floor,
        "passed": total >= floor,
    }
    (output / "combined.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
