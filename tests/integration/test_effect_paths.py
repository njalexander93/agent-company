"""Verify effect classification against real disposable symlinks and path traversal."""

from pathlib import Path

import pytest

from nplet.adapters.effects import Operation, Policy, classify, target_effect

pytestmark = pytest.mark.integration


def test_symlink_and_traversal_targets(tmp_path: Path) -> None:
    """Alias names cannot conceal protected files or protected instruction destinations.

    Args:
        tmp_path: Disposable filesystem root for this integration scenario.
    """
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
    """Future protected targets and unresolved symlink loops cannot qualify as notes.

    Args:
        tmp_path: Disposable filesystem root for this integration scenario.
    """
    # Resolve dangling aliases through the nonexistent protected destination.
    (tmp_path / "plan.md").symlink_to(tmp_path / "src" / "future.py")
    policy = Policy(tmp_path)
    assert target_effect("plan.md", tmp_path, policy) == "protected"
    # A loop is unknown rather than an exception or a task-free result.
    (tmp_path / "loop.md").symlink_to(tmp_path / "loop.md")
    assert target_effect("loop.md", tmp_path, policy) == "unknown"


def test_hardlink_alias(tmp_path: Path) -> None:
    """Existing hardlink aliases retain the implementation gate.

    Args:
        tmp_path: Disposable filesystem root for this integration scenario.
    """
    # A hardlink bypasses symlink resolution; conservative link-count checks retain the gate.
    source = tmp_path / "original.py"
    source.write_text("source", encoding="utf-8")
    alias = tmp_path / "plan.md"
    alias.hardlink_to(source)
    assert target_effect("plan.md", tmp_path, Policy(tmp_path)) == "protected"
    assert source.read_text(encoding="utf-8") == "source"


def test_existing_dependency_and_governance_aliases(tmp_path: Path) -> None:
    """Existing dependencies, case aliases and rename endpoints cannot become notes.

    Args:
        tmp_path: Disposable checkout for real file identity and alias inspection.
    """
    # Create conventional dependency files and the canonical governing guide.
    runtime = tmp_path / "docs" / "runtime"
    runtime.mkdir(parents=True)
    canonical = runtime / "development.md"
    canonical.write_text("governance", encoding="utf-8")
    dependency = tmp_path / "requirements-dev.txt"
    dependency.write_text("example==1", encoding="utf-8")
    policy = Policy(tmp_path)
    # A case-insensitive host may map this alias to the same file; all hosts retain the gate.
    alias = runtime / "DEVELOPMENT.md"
    if alias.exists():
        assert alias.samefile(canonical)
    else:
        # Case-sensitive hosts still conservatively protect a separately created variant.
        alias.write_text("case variant", encoding="utf-8")
    for target in (dependency, alias):
        decision = classify(
            Operation("edit", {"targets": ["notes/new.md", str(target)]}, str(tmp_path)), policy
        )
        assert decision.effect == "protected"
    # A prose symlink to a dependency and a renamed dependency retain both endpoint roles.
    (tmp_path / "plan.txt").symlink_to(dependency)
    assert target_effect("plan.txt", tmp_path, policy) == "protected"
    assert classify(
        Operation("edit", {"targets": ["requirements-dev.txt", "notes/old.md"]}, str(tmp_path)),
        policy,
    ).requires_task
    assert canonical.read_text(encoding="utf-8") == "governance"
    assert dependency.read_text(encoding="utf-8") == "example==1"
    assert not (tmp_path / "notes" / "new.md").exists()


def test_checkout_symlink_boundary_and_escape(tmp_path: Path) -> None:
    """Lexical and resolved checkout roots ignore ancestors while retaining escaped roles.

    Args:
        tmp_path: Disposable filesystem for a symlinked checkout and outside source tree.
    """
    # Both the alias and real checkout live under implementation-like ancestor names.
    root = tmp_path / "src" / "checkout"
    notes = root / "notes"
    notes.mkdir(parents=True)
    plan = notes / "plan.md"
    plan.write_text("ordinary prose", encoding="utf-8")
    aliases = tmp_path / "build"
    aliases.mkdir()
    alias_root = aliases / "checkout"
    alias_root.symlink_to(root, target_is_directory=True)
    policy = Policy(alias_root)
    assert target_effect("notes/plan.md", alias_root, policy) == "note"
    assert target_effect("notes/new.md", alias_root, policy) == "note"
    # Resolved outside source roles must survive a harmless-looking alias inside notes.
    outside = tmp_path / "external" / "src"
    outside.mkdir(parents=True)
    source = outside / "instructions.md"
    source.write_text("source input", encoding="utf-8")
    (notes / "alias.md").symlink_to(source)
    assert target_effect("notes/alias.md", alias_root, policy) == "protected"
    assert target_effect(str(source), alias_root, policy) == "protected"
    # A lexical source alias stays protected even when resolving to an ordinary note.
    implementation = root / "src"
    implementation.mkdir()
    (implementation / "alias.md").symlink_to(plan)
    assert target_effect("src/alias.md", alias_root, policy) == "protected"
    assert classify(
        Operation("edit", {"targets": ["notes/new.md", "notes/alias.md"]}, str(alias_root)), policy
    ).requires_task
    assert plan.read_text(encoding="utf-8") == "ordinary prose"
    assert source.read_text(encoding="utf-8") == "source input"
    assert not (notes / "new.md").exists()
