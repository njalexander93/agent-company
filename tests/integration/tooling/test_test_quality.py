"""Exercise the quality entry point across independent missing inputs."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

from scripts import test_quality as quality
from tests.support import ROOT

pytestmark = pytest.mark.integration


def test_quality_retains_multiple_failures_and_independent_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid native evidence blocks its dependents but lint and probes still run.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        monkeypatch: Pytest fixture that restores patched dependencies after this case.
    """
    # Supply separate malformed native and tooling evidence trees.
    inputs = tmp_path / "native"
    tooling = tmp_path / "tooling"
    output = tmp_path / "out"
    inputs.mkdir()
    tooling.mkdir()
    monkeypatch.setattr(
        quality.dev,
        "checkout_state",
        lambda: {
            "sha": "candidate",
            "status": "",
            "tracked_digest": "bytes",
        },
    )
    monkeypatch.setattr(
        quality,
        "run_lint",
        lambda _output: {
            "name": "focused_lint",
            "status": "failed",
            "seconds": 0.01,
            "detail": "intentional lint defect",
        },
    )
    monkeypatch.setattr(
        quality,
        "run_probes",
        lambda _spec, _output: {
            "name": "assertion_probes",
            "status": "passed",
            "seconds": 0.01,
            "detail": ["three intended assertion failures"],
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "test_quality.py",
            str(inputs),
            "--tooling-dir",
            str(tooling),
            "--output-dir",
            str(output),
        ],
    )
    # Inspect the final named results for every independent check.
    assert quality.main() == 1
    report = json.loads((output / "quality.json").read_text())
    assert report["passed"] is False
    assert [(row["name"], row["status"]) for row in report["results"]] == [
        ("candidate_identity", "passed"),
        ("native_evidence", "failed"),
        ("tooling_unit_evidence", "failed"),
        ("aggregate_80", "blocked"),
        ("function_obligations", "blocked"),
        ("focused_lint", "failed"),
        ("assertion_probes", "passed"),
        ("analysis_budget", "passed"),
    ]
    native = next(row for row in report["results"] if row["name"] == "native_evidence")
    assert str(Path("Linux") / "unit") in native["detail"]
    assert str(Path("Windows") / "integration") in native["detail"]


@pytest.mark.parametrize("defect", ["survivor", "syntax", "stale"])
def test_assertion_probe_rejects_unintended_outcomes_and_accepts_repair(
    tmp_path: Path, defect: str
) -> None:
    """Only a selected assertion failure counts; a repaired spec passes again.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        defect: Parameterized defect injected into this candidate.
    """
    # Load the selected fault probes into a disposable candidate.
    cases = json.loads((ROOT / "scripts/quality_probes.json").read_text())
    # Write every selected probe case into the disposable mutation specification.
    for case in cases:
        case["source_sha256"] = hashlib.sha256(
            (ROOT / case["source_path"]).read_bytes()
        ).hexdigest()
    spec = tmp_path / "probes.json"
    spec.write_text(json.dumps(cases))
    # Reject wrong failures and accept the repaired intended assertion.
    assert quality.run_probes(spec, tmp_path)["status"] == "passed"
    first = cases[0]
    # Turn the mutation into a survivor to prove lack of detection fails the gate.
    if defect == "survivor":
        first["new"] = "return hashlib.sha256(data).hexdigest()  # harmless change"
    # Inject invalid Python so a syntax error cannot masquerade as an assertion failure.
    elif defect == "syntax":
        first["new"] = "return ("
    else:
        # Use a stale replacement target to prove mutation setup must match source.
        first["old"] = "a stale mutation target"
    spec.write_text(json.dumps(cases))
    result = quality.run_probes(spec, tmp_path)
    assert result["status"] == "failed"
    assert result["detail"][0]["status"] == "failed"
    cases = json.loads((ROOT / "scripts/quality_probes.json").read_text())
    # Run every probe baseline before accepting mutation outcomes.
    for case in cases:
        case["source_sha256"] = hashlib.sha256(
            (ROOT / case["source_path"]).read_bytes()
        ).hexdigest()
    spec.write_text(json.dumps(cases))
    assert quality.run_probes(spec, tmp_path)["status"] == "passed"


def test_probe_timeout_is_failed_and_does_not_stop_peer_probes(tmp_path: Path) -> None:
    """A baseline deadline fails only its case; other selected faults still run.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
    """
    # Force one selected assertion probe to exceed its time limit.
    cases = json.loads((ROOT / "scripts/quality_probes.json").read_text())
    # Check each reported probe names the expected controlled assertion failure.
    for case in cases:
        case["source_sha256"] = hashlib.sha256(
            (ROOT / case["source_path"]).read_bytes()
        ).hexdigest()
    cases[0]["timeout_seconds"] = 0.001
    spec = tmp_path / "probes.json"
    spec.write_text(json.dumps(cases))
    failed = quality.run_probes(spec, tmp_path)
    # Require a failed timeout result while peer probes continue.
    assert failed["status"] == "failed"
    assert failed["detail"][0]["error"] == "baseline timed out"
    assert [item["status"] for item in failed["detail"][1:]] == ["passed", "passed"]
    cases[0]["timeout_seconds"] = 20
    spec.write_text(json.dumps(cases))
    assert quality.run_probes(spec, tmp_path)["status"] == "passed"
