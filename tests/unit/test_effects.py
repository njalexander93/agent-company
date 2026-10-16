"""Verify effect policy decisions independently of lifecycle storage and native hooks."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from nplet.adapters.effects import (
    Operation,
    Policy,
    ProviderContract,
    classify,
    shell_effect,
    target_effect,
)

pytestmark = pytest.mark.unit
ROOT = Path(Path.cwd().anchor) / "checkout"
POLICY = Policy(ROOT)


@pytest.mark.parametrize("family", ["read", "admin"])
def test_task_free_contracts(family: str) -> None:
    """Trusted native inspection/admin needs no lifecycle storage or safety flags."""
    # Classify without any lifecycle object, binding, approval or filesystem setup.
    result = classify(Operation(family, {"read_only": False}, str(ROOT)), POLICY)
    assert result.effect == family
    assert not result.requires_task
    assert result.guidance == ""


@pytest.mark.parametrize(
    "path",
    [
        "src/a.py",
        "tests/a.md",
        "scripts/plan.md",
        "docs/runtime/contributor-workflow.md",
        "docs/README.md",
        "docs/framework/baseline.md",
        "AGENTS.md",
        "nested/CLAUDE.md",
        "nested/SKILL.md",
        "nested/CONTEXT.md",
        ".github/workflows/check.yml",
        ".codex/notes.md",
        ".claude/notes.md",
        ".cursor/rules/notes.md",
        "skills/design/notes.md",
        ".task/T/context/note.md",
        "pyproject.toml",
        "poetry.lock",
        "Makefile",
        ".pre-commit-config.yaml",
        "src/../notes/plan.md",
        "../sibling/tests/plan.md",
        "AGenTs.MD",
        "a.py",
    ],
)
def test_protected_targets(path: str) -> None:
    """Implementation, instruction and normative destinations require a task."""
    assert target_effect(path, ROOT, POLICY) == "protected"


@pytest.mark.parametrize(
    "path", ["plan.md", "notes/design.txt", "../vault/plan.rst", "docs/planning.md"]
)
def test_ordinary_note(path: str) -> None:
    """Explicit ordinary prose destinations remain task-free."""
    result = classify(Operation("edit", {"targets": [path]}, str(ROOT)), POLICY)
    assert result.effect == "note"
    assert not result.requires_task


@pytest.mark.parametrize(
    "targets",
    [
        ["notes/plan.md", "src/main.py"],
        ["src/main.py", "notes/plan.md"],
        ["notes/a.md", "AGENTS.md"],
        ["notes/a.md", None, "tests/case.py"],
    ],
)
def test_mixed_edits_and_renames(targets: list[object]) -> None:
    """A protected endpoint gates the entire edit before any partial execution."""
    result = classify(Operation("edit", {"targets": targets}, str(ROOT)), POLICY)
    assert result.effect == "protected"
    assert result.requires_task


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"targets": []},
        {"targets": "plan.md"},
        {"targets": [None]},
        {"targets": [""]},
        {"targets": ["a\x00.md"]},
        {"targets": ["plan.md"], "executable": "false"},
    ],
)
def test_malformed_edits(arguments: dict[str, object]) -> None:
    """Malformed target sets never become task-free notes."""
    result = classify(Operation("edit", arguments, str(ROOT)), POLICY)
    assert result.effect == "unknown"
    assert result.requires_task
    assert "structured" in result.guidance


def test_executable_note_and_registered_loader() -> None:
    """Executable permissions and a registered instruction loader override prose suffixes."""
    # Check explicit permission effects independently from the filename.
    result = classify(
        Operation("edit", {"targets": ["plan.md"], "executable": True}, str(ROOT)), POLICY
    )
    assert result.effect == "protected"
    # A loader-consumed Markdown file is not an ordinary note.
    policy = Policy(ROOT, protected_paths=(ROOT / "notes" / "runtime.md",))
    assert target_effect("notes/runtime.md", ROOT, policy) == "protected"
    assert target_effect("notes/ordinary.md", ROOT, policy) == "note"


@pytest.mark.parametrize(
    "command",
    [
        "pwd",
        "pwd -P",
        "ls -l -a",
        "cat -- src/main.py",
        "head -n 20 src/main.py",
        "tail -c 20 src/main.py",
        "wc -l src/main.py",
        "rg --no-config --files",
        'rg --no-config -n --glob "*.py" pattern src',
        "sed -n '1,20p' src/main.py",
        "git --no-pager --no-optional-locks -c core.fsmonitor=false status --short",
        (
            "git --no-pager --no-optional-locks -c core.fsmonitor=false diff "
            "--no-ext-diff --no-textconv --stat"
        ),
        "git --no-pager --no-optional-locks -c core.fsmonitor=false branch --show-current",
        "git --no-pager --no-optional-locks -c core.fsmonitor=false rev-parse --show-toplevel",
        "cat plan.md | wc -l",
        (
            "pwd && rg --no-config --files; "
            "git --no-pager --no-optional-locks -c core.fsmonitor=false status --short"
        ),
        "cat missing.md || cat fallback.md",
        'cat "a\'b.md"',
    ],
)
def test_supported_shell_inspection(command: str) -> None:
    """Known complete argument forms and fully understood compositions stay task-free."""
    result = shell_effect(command)
    assert result.effect == "read"
    assert not result.requires_task


@pytest.mark.parametrize(
    "command",
    [
        "cat plan.md > src/main.py",
        "rg --pre evil pattern src",
        "rg --hostname-bin evil x",
        "git -c alias.status=evil status",
        "git status --output=src/main.py",
        "git diff --ext-diff",
        "git --no-pager --no-optional-locks -c core.fsmonitor=false diff",
        "git --no-pager --no-optional-locks -c core.fsmonitor=false branch renamed",
        "git --no-pager --no-optional-locks -c core.fsmonitor=false status --output=x",
        "git diff",
        "git diff -- --no-ext-diff --no-textconv",
        "rg pattern -- --no-config",
        "cat *.md",
        "rg --files",
        "git show --textconv",
        "git branch renamed",
        "sed -i 's/x/y/' src/main.py",
        "sed -n '1e evil' plan.md",
        "head -n",
        "head --bad plan.md",
        "cat plan.md && python -c evil",
        "cat plan.md &",
        "cat $(evil)",
        "cat `evil`",
        "cat ${PATH}",
        "env cat plan.md",
        "/bin/cat plan.md",
        "python script.py",
        "make test",
        "pytest",
        "cat <(evil)",
        "cat plan.md >> plan.md",
        "cat plan.md\nrm plan.md",
        'cat "unterminated',
        "",
        "; cat plan.md",
        "cat plan.md;",
    ],
)
def test_opaque_shell_requires_task(command: str) -> None:
    """Flags, wrappers, substitutions and mixed commands cannot inherit a read exemption."""
    result = shell_effect(command)
    assert result.effect == "unknown"
    assert result.requires_task
    assert "Task" in result.guidance


def test_provider_exact_contracts() -> None:
    """Provider contracts use exact identity/operation and expose all edit endpoints."""
    policy = Policy(
        ROOT,
        providers={
            ("issues", "get"): ProviderContract("read"),
            ("issues", "assign"): ProviderContract("admin"),
            ("files", "move"): ProviderContract("edit", ("source", "destination")),
            ("files", "run"): ProviderContract("execute"),
            ("files", "bad-edit"): ProviderContract("edit"),
        },
    )
    # Check read/admin versus mutation contracts independently of operation-name prefixes.
    for name, expected in [("get", "read"), ("assign", "admin"), ("get_extra", "unknown")]:
        result = classify(
            Operation("provider", {"provider": "issues", "operation": name}, str(ROOT)), policy
        )
        assert result.effect == expected
    # Both rename endpoints must be available before a provider mutation is admitted.
    args = {
        "provider": "files",
        "operation": "move",
        "parameters": {"source": "notes/a.md", "destination": "src/a.py"},
    }
    assert classify(Operation("provider", args, str(ROOT)), policy).effect == "protected"
    args["parameters"] = {"source": "notes/a.md", "destination": "notes/b.md"}
    assert classify(Operation("provider", args, str(ROOT)), policy).effect == "note"
    args["parameters"] = {"source": "notes/a.md"}
    assert classify(Operation("provider", args, str(ROOT)), policy).effect == "unknown"
    # An opaque execution contract requires the task even without enumerable targets.
    for name, expected in [("run", "protected"), ("bad-edit", "unknown")]:
        assert (
            classify(
                Operation("provider", {"provider": "files", "operation": name}, str(ROOT)), policy
            ).effect
            == expected
        )


@pytest.mark.parametrize(
    "family,args,cwd",
    [
        ("provider", {"provider": [], "operation": "get"}, "/checkout"),
        ("provider", {"provider": "other", "operation": "read", "read_only": True}, "/checkout"),
        ("unknown", {"read_only": True}, "/checkout"),
        ("read", {}, "relative"),
        ("shell", {}, "/checkout"),
    ],
)
def test_unknown_envelopes(family: str, args: dict[str, object], cwd: str) -> None:
    """Unknown identities, user safety labels and malformed envelopes confer no exemption."""
    result = classify(Operation(family, args, cwd), POLICY)
    assert result.requires_task
    assert result.guidance


def test_path_failures_are_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Filesystem inspection errors and relative bases cannot silently admit notes."""
    assert target_effect("plan.md", Path("relative"), POLICY) == "unknown"
    assert target_effect("plan.md", ROOT, Policy(Path("relative"))) == "unknown"

    # A native stat failure is a rare boundary fault rather than proof of a safe note.
    def failed_stat(self: Path, *, follow_symlinks: bool = True) -> object:
        """Raise the filesystem inspection failure the policy must classify.

        Args:
            self: Target path.
            follow_symlinks: Native stat option, unused by the fault.

        Raises:
            OSError: Simulated failed metadata inspection.
        """
        raise OSError("metadata unavailable")

    monkeypatch.setattr(Path, "stat", failed_stat)
    assert target_effect("plan.md", ROOT, POLICY) == "unknown"


def test_malformed_normalized_operation() -> None:
    """Malformed normalized envelopes require the unknown-effect route."""
    # Native JSON can violate annotations; runtime guards still reject those shapes.
    assert classify(Operation("read", None, str(ROOT)), POLICY).code == "MALFORMED_OPERATION"
    assert classify(Operation("read", {}, None), POLICY).code == "MALFORMED_OPERATION"
    assert classify(Operation("read", {}, "/bad\x00path"), POLICY).requires_task
    assert shell_effect(None).requires_task


@pytest.mark.parametrize(
    "links,mode,expected", [(1, 0o644, "note"), (2, 0o644, "protected"), (1, 0o755, "protected")]
)
def test_existing_metadata(
    links: int, mode: int, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing ordinary files stay free; hardlinks and execute permissions retain the gate."""

    # Model only the native metadata boundary; real aliases have separate integration tests.
    def metadata(self: Path, *, follow_symlinks: bool = True) -> SimpleNamespace:
        """Return deterministic metadata for the target under inspection.

        Args:
            self: Inspected path.
            follow_symlinks: Native stat option.

        Returns:
            Native link-count and permission fields used by the policy.
        """
        return SimpleNamespace(st_nlink=links, st_mode=mode)

    monkeypatch.setattr(Path, "stat", metadata)
    assert target_effect("ordinary.md", ROOT, POLICY) == expected


@pytest.mark.parametrize(
    "arguments",
    [
        "status --short -- notes/",
        "status --short -- --output=report.txt",
        "ls-files -- notes/ --ext-diff",
        "diff --no-ext-diff --no-textconv -- notes/ --ext-diff",
        "log --no-ext-diff --no-textconv -- notes/",
        "show --no-ext-diff --no-textconv -- notes/",
    ],
)
def test_git_filename_separator(arguments: str) -> None:
    """A Git filename separator ends option validation for recognized read commands."""
    # Native paths after the separator are filenames, even when they resemble options.
    prefix = "git --no-pager --no-optional-locks -c core.fsmonitor=false "
    result = shell_effect(prefix + arguments)
    assert result.effect == "read"
    assert not result.requires_task


@pytest.mark.parametrize(
    "arguments",
    [
        "status --output=report.txt -- notes/",
        "diff --ext-diff --no-ext-diff --no-textconv -- notes/",
        "diff -- --no-ext-diff --no-textconv",
        "log --no-ext-diff -- --no-textconv",
        "show --no-textconv -- --no-ext-diff",
        "add -- notes/",
        "reset -- notes/",
        "branch --show-current -- notes/",
    ],
)
def test_git_separator_preserves_gate(arguments: str) -> None:
    """Filename tokens cannot supply missing safety flags or admit unsupported operations."""
    # Validate option evidence before the separator and keep mutation subcommands opaque.
    prefix = "git --no-pager --no-optional-locks -c core.fsmonitor=false "
    result = shell_effect(prefix + arguments)
    assert result.effect == "unknown"
    assert result.requires_task
    assert result.guidance
