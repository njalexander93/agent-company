"""Validate genuine Coverage.py reports against their raw branch database."""

import hashlib
import json
import runpy
from pathlib import Path

import pytest
from coverage import Coverage

from scripts.coverage_evidence import valid_suite
from tests.unit.tooling.test_coverage_evidence_records import TOOLS, record

pytestmark = pytest.mark.integration


def test_translated_report_passes_and_fabricated_line_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A multiline source line is valid; a rehashed invented line is not."""
    directory = record(tmp_path, "Darwin", "unit")
    source = tmp_path / "src/agent_company/sample.py"
    source.write_text(
        "def choose(value):\n"
        "    if (\n"
        "        value\n"
        "    ):\n"
        "        return 1\n"
        "    return 0\n"
        "choose(True)\n"
    )
    with monkeypatch.context() as scoped:
        scoped.chdir(tmp_path)
        coverage = Coverage(
            data_file=str(directory / ".coverage"),
            config_file=str(tmp_path / "pyproject.toml"),
        )
        coverage.start()
        runpy.run_path(str(source))
        coverage.stop()
        coverage.save()
        coverage.json_report(outfile=str(directory / "coverage.json"))
        coverage.xml_report(outfile=str(directory / "coverage.xml"))
        coverage.get_data().close()
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for name in (".coverage", "coverage.json", "coverage.xml"):
        manifest["artifacts_sha256"][name] = hashlib.sha256(
            (directory / name).read_bytes()
        ).hexdigest()
    report_path = directory / "coverage.json"
    report = json.loads(report_path.read_text())
    source_key = next(
        key for key in report["files"] if key.replace("\\", "/") == "src/agent_company/sample.py"
    )
    assert report["files"][source_key]["executed_lines"]
    manifest_path.write_text(json.dumps(manifest))
    assert valid_suite(
        directory,
        sha="candidate",
        tracked_digest="bytes",
        config_digest="config",
        tool_versions=TOOLS,
        source_root=tmp_path,
    )[0:2] == ("Darwin", "unit")

    report["files"][source_key]["executed_lines"].append(999999)
    report_path.write_text(json.dumps(report))
    manifest["artifacts_sha256"]["coverage.json"] = hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="coverage JSON disagrees with source and database"):
        valid_suite(
            directory,
            sha="candidate",
            tracked_digest="bytes",
            config_digest="config",
            tool_versions=TOOLS,
            source_root=tmp_path,
        )
