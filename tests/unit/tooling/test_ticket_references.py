"""Check the Linear issue reference guard against disposable Git checkouts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import ticket_references

pytestmark = pytest.mark.unit

# Build identifiers by concatenation so this file never contains a reference itself.
ISSUE = "AGENT" + "-12"
URL = "https://linear" + ".app/acme/issue/" + ISSUE + "/title"


def checkout(root: Path, files: dict[str, bytes]) -> None:
    """Create a Git checkout tracking the supplied files.

    Args:
        root: Directory to initialize.
        files: Relative paths and their bytes.
    """
    root.mkdir()
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for command in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(root), *command], check=True, env=env)


def test_pattern_matches_team_identifiers_and_issue_urls_only() -> None:
    """Match named team keys and issue URLs but not placeholders or provider endpoints."""
    regex = ticket_references.pattern(("AGENT", "OPS"))
    assert regex.search("see " + ISSUE + " for context")
    assert regex.search("OPS" + "-7")
    assert regex.search(URL)
    # Fixture keys, lowercase text, MCP endpoints and the key inside a longer word stay clear.
    for text in ("TEST-30", "agent-12", "https://mcp.linear.app/mcp", "MAGENT" + "-1", "AGENT-x"):
        assert regex.search(text) is None, text


def test_findings_report_tracked_text_lines_and_skip_binary_or_missing(tmp_path: Path) -> None:
    """Report each tracked text line once and ignore binary, missing and untracked files.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
    """
    root = tmp_path / "checkout"
    checkout(
        root,
        {
            "docs/guide.md": ("clean line\nsee " + ISSUE + " here\n" + URL + "\n").encode(),
            "src/clean.py": b"value = 'TEST-1'\n",
            "image.bin": bytes([0xFF, 0xFE, 0x00, 0x80]) + ISSUE.encode(),
            "gone.md": ISSUE.encode(),
        },
    )
    # A tracked file deleted from the worktree and an untracked file are both skipped.
    (root / "gone.md").unlink()
    (root / "untracked.md").write_text(ISSUE + "\n", encoding="utf-8")
    assert ticket_references.findings(root) == [
        "docs/guide.md:2: see " + ISSUE + " here",
        "docs/guide.md:3: " + URL,
    ]


def test_main_prints_findings_and_returns_failure_only_when_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit one with the listed references, zero for a clean checkout.

    Args:
        tmp_path: Disposable directory supplied by pytest for this case.
        capsys: Captures the printed report.
    """
    dirty, clean = tmp_path / "dirty", tmp_path / "clean"
    checkout(dirty, {"note.md": (ISSUE + "\n").encode()})
    checkout(clean, {"note.md": b"TEST-30\n"})
    assert ticket_references.main([str(dirty)]) == 1
    out = capsys.readouterr().out
    assert "note.md:1: " + ISSUE in out
    assert "1 Linear issue reference(s) found" in out
    assert ticket_references.main([str(clean)]) == 0
    assert capsys.readouterr().out == ""


def test_main_defaults_to_this_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without arguments the guard inspects the repository that holds the script.

    Args:
        monkeypatch: Replaces the process arguments and the checkout scan.
    """
    seen: list[Path] = []
    monkeypatch.setattr("sys.argv", ["ticket_references.py"])
    monkeypatch.setattr(
        ticket_references, "findings", lambda root: seen.append(root) or ["x.md:1: ref"]
    )
    assert ticket_references.main() == 1
    assert seen == [ticket_references.ROOT]
