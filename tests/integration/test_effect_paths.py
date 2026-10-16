"""Verify effect classification against real disposable symlinks and path traversal."""

from pathlib import Path

import pytest

from nplet.adapters.effects import Operation, Policy, classify, target_effect

pytestmark = pytest.mark.integration


def test_symlink_and_traversal_targets(tmp_path: Path) -> None:
    """Alias names cannot conceal protected files or protected instruction destinations."""
    # Build disposable files and retain an outside sentinel for forbidden effects.
    root = tmp_path / "checkout"
    source = root / "src"
    notes = root / "notes"
    source.mkdir(parents=True)
    notes.mkdir()
    sentinel = source / "main.py"
    sentinel.write_text("original", encoding="utf-8")
    policy = Policy(root)
    # A prose alias to source and a source alias to prose are both protected.
    (notes / "plan.md").symlink_to(sentinel)
    ordinary = notes / "ordinary.md"
    ordinary.write_text("prose with illustrative code", encoding="utf-8")
    (source / "alias.md").symlink_to(ordinary)
    assert target_effect("notes/plan.md", root, policy) == "protected"
    assert target_effect("src/alias.md", root, policy) == "protected"
    assert target_effect("notes/../src/main.py", root, policy) == "protected"
    # A mixed edit classifies all endpoints and performs no filesystem mutation.
    result = classify(
        Operation("edit", {"targets": ["notes/new.md", "notes/plan.md"]}, str(root)), policy
    )
    assert result.requires_task
    assert sentinel.read_text(encoding="utf-8") == "original"
    assert not (notes / "new.md").exists()


def test_dangling_and_loop_symlinks(tmp_path: Path) -> None:
    """Future protected targets and unresolved symlink loops cannot qualify as notes."""
    # Resolve dangling aliases through the nonexistent protected destination.
    (tmp_path / "plan.md").symlink_to(tmp_path / "src" / "future.py")
    policy = Policy(tmp_path)
    assert target_effect("plan.md", tmp_path, policy) == "protected"
    # A loop is unknown rather than an exception or a task-free result.
    (tmp_path / "loop.md").symlink_to(tmp_path / "loop.md")
    assert target_effect("loop.md", tmp_path, policy) == "unknown"


def test_hardlink_alias(tmp_path: Path) -> None:
    """Existing hardlink aliases retain the implementation gate."""
    # A hardlink bypasses symlink resolution; conservative link-count checks retain the gate.
    source = tmp_path / "original.py"
    source.write_text("source", encoding="utf-8")
    alias = tmp_path / "plan.md"
    alias.hardlink_to(source)
    assert target_effect("plan.md", tmp_path, Policy(tmp_path)) == "protected"
    assert source.read_text(encoding="utf-8") == "source"
