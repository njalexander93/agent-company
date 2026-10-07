"""Check portable validation evidence and bounded cleanup using disposable directories."""

import json
import sys
from pathlib import Path

import pytest

from scripts import dev
from tests.platform_support import link_directory

pytestmark = pytest.mark.integration


def test_clean_preserves_environments_tasks_and_linked_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove caches while retaining live-data-shaped and externally linked fixtures.

    Args:
        tmp_path: Disposable root, containing no real task data.
        monkeypatch: Scoped developer-command root replacement.

    Raises:
        AssertionError: Cleanup follows a link or removes protected fixture contents.
    """
    root = tmp_path / "checkout"
    outside = tmp_path / "outside"
    for path in (root / ".venv", root / ".task", outside / "__pycache__"):
        path.mkdir(parents=True)
        (path / "keep").write_bytes(b"retain exactly")
    cache = root / "src" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "discard.pyc").write_bytes(b"generated")
    link_directory(root / "src" / "external", outside)
    monkeypatch.setattr(dev, "ROOT", root)
    # Repeated cleanup must remain bounded even with a native junction in the source tree.
    dev.clean()
    dev.clean()
    assert not cache.exists()
    for path in (root / ".venv", root / ".task", outside / "__pycache__"):
        assert (path / "keep").read_bytes() == b"retain exactly"


def test_check_stops_on_failure_and_records_real_subprocess_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capture exact command failure and stop before a later command can execute.

    Args:
        tmp_path: Disposable log directory and subprocess working directory.
        monkeypatch: Scoped command plan and evidence identity replacement.

    Raises:
        AssertionError: Failed validation passes, loses its log or executes subsequent work.
    """
    evidence = tmp_path / "evidence with spaces"
    marker = tmp_path / "must-not-exist"
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "identity", lambda: {"fixture_identity": True})
    monkeypatch.setattr(dev, "CHECKS", ["first", "second"])
    monkeypatch.setattr(
        dev,
        "COMMANDS",
        {
            "first": [[sys.executable, "-c", "print('retained failure'); raise SystemExit(7)"]],
            "second": [
                [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
            ],
        },
    )
    monkeypatch.setattr(sys, "argv", ["dev.py", "check", "--evidence-dir", str(evidence)])
    # Execute a genuine child process; only the selected command plan and identity are fixtures.
    assert dev.main() == 7
    assert not marker.exists()
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert len(manifest["commands"]) == 1
    assert manifest["commands"][0]["exit_code"] == 7
    assert (evidence / manifest["commands"][0]["log"]).read_text() == "retained failure\n"
