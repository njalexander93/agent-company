"""Exercise installed read-only Git hooks in disposable clones with real locked tools."""

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.platform_support import environment_python, link_directory, unlink_directory
from tests.support import ROOT

pytestmark = pytest.mark.integration


@dataclass
class HookRepo:
    """Hold an isolated clone and environment for real Git hook invocations."""

    root: Path
    env: dict[str, str] = field(repr=False)

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run a bounded command without replacing Git, Poetry or the check tools.

        Args:
            *args: Exact command arguments, passed without shell interpolation.

        Returns:
            Completed process with combined output for assertion diagnostics.
        """
        # Keep every subprocess and cache inside the disposable fixture boundary.
        return subprocess.run(
            args,
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )

    def git(self, *args: str) -> str:
        """Run a Git fixture operation that must succeed.

        Args:
            *args: Git arguments, including clone-local configuration when needed.

        Returns:
            Captured Git output.

        Raises:
            AssertionError: Fixture Git operation fails.
        """
        # Refuse to continue setup after a failed index or repository operation.
        result = self.run("git", *args)
        assert result.returncode == 0, result.stdout
        return result.stdout

    def stage(self, path: str, content: str) -> None:
        """Write and stage exactly one fixture file.

        Args:
            path: Repository-relative path under the disposable clone.
            content: Complete intended staged content.
        """
        # Stage before later working-tree edits to create real partial staging.
        (self.root / path).write_text(content)
        self.git("add", "--", path)

    def snapshot(self) -> tuple[str, dict[str, bytes]]:
        """Capture exact index entries and tracked working-tree bytes.

        Returns:
            Index modes, object IDs and paths, plus all existing tracked file bytes.
        """
        # Ignore mutable index stat-cache metadata but preserve its exact Git content.
        paths = self.git("ls-files", "-z").split("\0")
        return self.git("ls-files", "--stage"), {
            path: (self.root / path).read_bytes()
            for path in paths
            if path and (self.root / path).is_file()
        }


@pytest.fixture
def hook_repo(tmp_path: Path) -> HookRepo:
    """Install the real framework in a disposable clone of a local source snapshot.

    Args:
        tmp_path: Temporary parent for the seed, clone and isolated tool caches.

    Returns:
        Clone with current policy, production sources and an installed Git hook.

    Raises:
        AssertionError: Baseline creation, installation or environment identity fails.
    """
    # Exclude live Git configuration, activated environments and inherited hook bypasses.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "POETRY_", "PRE_COMMIT_"))
        and key not in {"VIRTUAL_ENV", "PYTHONPATH", "SKIP"}
    }
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Hook fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="Hook fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
        PRE_COMMIT_HOME=str(tmp_path / "hook-cache"),
        POETRY_CACHE_DIR=str(tmp_path / "poetry-cache"),
        PATH=os.pathsep.join(
            path
            for path in os.environ["PATH"].split(os.pathsep)
            if Path(path).resolve() != environment_python(Path(sys.prefix)).parent.resolve()
        ),
    )
    # Copy policy and source only; no active task data or live repository metadata enters.
    seed = tmp_path / "seed"
    seed.mkdir()
    for name in (
        "pyproject.toml",
        "poetry.lock",
        "poetry.toml",
        "Makefile",
        ".pre-commit-config.yaml",
    ):
        shutil.copy2(ROOT / name, seed / name)
    shutil.copytree(ROOT / "src", seed / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(
        ROOT / "scripts", seed / "scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    (seed / "tests").mkdir()
    (seed / "tests/hook_probe.py").write_text('"""Supply a staged hook fixture."""\n\nVALUE = 1\n')
    (seed / ".gitignore").write_text(".venv\n.mypy_cache/\n.ruff_cache/\n__pycache__/\n")
    setup = HookRepo(seed, env)
    setup.git("init", "-q")
    setup.git("config", "core.autocrlf", "false")
    setup.git("add", ".")
    setup.git("commit", "-qm", "fixture baseline")
    # A fresh clone has no installed hooks until the explicit install command runs.
    root = tmp_path / "clone"
    setup.git("clone", "--quiet", "--no-local", str(seed), str(root))
    repo = HookRepo(root, env)
    repo.git("config", "core.autocrlf", "false")
    assert not (root / ".git/hooks/pre-commit").exists()
    link_directory(root / ".venv", Path(sys.prefix))
    result = repo.run(str(environment_python(root / ".venv")), "-m", "pre_commit", "install")
    assert result.returncode == 0, result.stdout
    installed = (root / ".git/hooks/pre-commit").read_text()
    assert str(environment_python(root / ".venv")) in installed
    return repo


def test_installed_hook_accepts_clean_staging_and_records_latency(hook_repo: HookRepo) -> None:
    """Accept real commits and measure cold then warm local feedback.

    Args:
        hook_repo: Disposable clone with the installed framework.

    Raises:
        AssertionError: A clean commit fails or hook checks are omitted.
    """
    # Run once with fresh caches and again with the same environment and warmed mypy cache.
    for label, value in (("cold", 2), ("warm", 3)):
        hook_repo.stage(
            "tests/hook_probe.py", f'"""Supply a staged hook fixture."""\n\nVALUE = {value}\n'
        )
        before = hook_repo.snapshot()
        start = time.monotonic()
        result = hook_repo.run("git", "commit", "-m", f"fixture {label}")
        elapsed = time.monotonic() - start
        print(f"Installed hooks {label}: {elapsed:.3f}s")
        assert result.returncode == 0, result.stdout
        assert result.stdout.count("Passed") == 3, result.stdout
        assert hook_repo.snapshot() == before
        assert not hook_repo.git("status", "--porcelain")


@pytest.mark.parametrize(
    ("path", "content", "diagnostic"),
    [
        ("tests/hook_probe.py", '"""Supply a staged hook fixture."""\n\nVALUE=2\n', "ruff-format"),
        ("tests/hook_probe.py", '"""Supply a staged hook fixture."""\n\nimport os\n', "F401"),
        (
            "src/agent_company/hook_probe.py",
            '"""Supply a production type failure."""\n\nVALUE: int = "wrong"\n',
            "[assignment]",
        ),
    ],
    ids=["format", "lint", "type"],
)
def test_installed_hook_rejects_failures_without_editing(
    hook_repo: HookRepo, path: str, content: str, diagnostic: str
) -> None:
    """Reject formatting, lint and type failures without repairing or staging content.

    Args:
        hook_repo: Disposable clone with the installed framework.
        path: Staged fixture path.
        content: Invalid code for one actual tool.
        diagnostic: Tool output proving the intended failure was reached.

    Raises:
        AssertionError: Invalid code commits or any index/worktree content changes.
    """
    # Capture the proposed commit and every tracked working file before invoking Git.
    hook_repo.stage(path, content)
    before = hook_repo.snapshot()
    head = hook_repo.git("rev-parse", "HEAD")
    result = hook_repo.run("git", "commit", "-m", "must fail")
    assert result.returncode != 0, result.stdout
    assert diagnostic in result.stdout
    assert hook_repo.git("rev-parse", "HEAD") == head
    assert hook_repo.snapshot() == before


@pytest.mark.parametrize("valid_index", [True, False])
def test_installed_hook_checks_partial_staging_and_restores_worktree(
    hook_repo: HookRepo, valid_index: bool
) -> None:
    """Check index content while preserving opposite-validity unstaged edits byte for byte.

    Args:
        hook_repo: Disposable clone with the installed framework.
        valid_index: Whether only the staged version satisfies the formatter.

    Raises:
        AssertionError: The wrong version is checked or either version is overwritten.
    """
    # Stage one version and leave the opposite formatter outcome only in the working tree.
    valid = '"""Supply a staged hook fixture."""\n\nVALUE = 2\n'
    invalid = '"""Supply a staged hook fixture."""\n\nVALUE=3\n'
    path = "tests/hook_probe.py"
    staged, unstaged = (valid, invalid) if valid_index else (invalid, valid)
    hook_repo.stage(path, staged)
    (hook_repo.root / path).write_text(unstaged)
    before = hook_repo.snapshot()
    head = hook_repo.git("rev-parse", "HEAD")
    result = hook_repo.run("git", "commit", "-m", "partial staging")
    assert (result.returncode == 0) is valid_index, result.stdout
    assert "Unstaged files detected" in result.stdout
    assert hook_repo.snapshot() == before
    if valid_index:
        assert hook_repo.git("show", f"HEAD:{path}") == staged
    else:
        assert hook_repo.git("rev-parse", "HEAD") == head
        assert "ruff-format" in result.stdout


def test_installed_hook_checks_types_outside_staged_filenames(hook_repo: HookRepo) -> None:
    """Run configured full-source mypy even when only a non-Python file is staged.

    Args:
        hook_repo: Disposable clone with the installed framework.

    Raises:
        AssertionError: Mypy narrows its scope or filenames leak into the Make invocation.
    """
    # Seed an existing type defect through a clone-only fixture bypass before the tested commit.
    path = "src/agent_company/hook_other.py"
    hook_repo.stage(path, '"""Supply an existing type failure."""\n\nVALUE: int = "wrong"\n')
    hook_repo.git(
        "-c", "core.hooksPath=.git/fixture-disabled-hooks", "commit", "-qm", "bad baseline"
    )
    hook_repo.stage("README.md", "A documentation-only fixture change.\n")
    before = hook_repo.snapshot()
    result = hook_repo.run("git", "commit", "-m", "must check configured scope")
    assert result.returncode != 0, result.stdout
    assert "poetry run mypy" in result.stdout
    assert "hook_other.py" in result.stdout and "[assignment]" in result.stdout
    assert result.stdout.count("Skipped") == 2
    assert hook_repo.snapshot() == before


def test_installed_hook_runs_mypy_for_deletion_only_commit(hook_repo: HookRepo) -> None:
    """Retain full-scope type checking when no existing Python file remains staged.

    Args:
        hook_repo: Disposable clone with the installed framework.

    Raises:
        AssertionError: Deletion-only staging skips the configured type checker.
    """
    # Deleted files cannot match the framework's existing-file filter for Ruff.
    hook_repo.git("rm", "tests/hook_probe.py")
    before = hook_repo.snapshot()
    result = hook_repo.run("git", "commit", "-m", "delete fixture")
    assert result.returncode == 0, result.stdout
    assert result.stdout.count("Skipped") == 2
    assert result.stdout.count("Passed") == 1
    assert "Mypy (configured production scope)" in result.stdout
    assert hook_repo.snapshot() == before


def test_installed_hook_fails_when_project_environment_is_absent(hook_repo: HookRepo) -> None:
    """Fail a real commit without creating an environment or losing staged work.

    Args:
        hook_repo: Disposable clone with the installed framework.

    Raises:
        AssertionError: Missing environment bypasses checks or changes staged content.
    """
    # Remove only this clone's environment symlink; preserve the actual development environment.
    hook_repo.stage("tests/hook_probe.py", '"""Supply a staged hook fixture."""\n\nVALUE = 2\n')
    unlink_directory(hook_repo.root / ".venv")
    # Prevent an unrelated globally installed framework from masking the missing clone setup.
    before = hook_repo.snapshot()
    head = hook_repo.git("rev-parse", "HEAD")
    original_path = hook_repo.env["PATH"]
    git = shutil.which("git", path=original_path)
    assert git is not None
    hook_repo.env["PATH"] = os.pathsep.join(
        path
        for path in original_path.split(os.pathsep)
        if shutil.which("pre-commit", path=path) is None
    )
    try:
        result = hook_repo.run(git, "commit", "-m", "missing environment")
    finally:
        hook_repo.env["PATH"] = original_path
    assert result.returncode != 0, result.stdout
    assert "pre-commit" in result.stdout and "not found" in result.stdout
    assert not (hook_repo.root / ".venv").exists()
    assert hook_repo.git("rev-parse", "HEAD") == head
    assert hook_repo.snapshot() == before
