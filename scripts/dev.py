"""Run the same contributor checks on native Windows, Linux and macOS without Make."""

import argparse
import codecs
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
COMMANDS: dict[str, list[list[str]]] = {
    "install": [["poetry", "sync"]],
    "validate-config": [
        ["poetry", "check", "--lock"],
        ["poetry", "run", "pre-commit", "validate-config"],
    ],
    "format": [["poetry", "run", "ruff", "format", "src", "tests", "scripts"]],
    "format-check": [
        ["poetry", "run", "ruff", "format", "--check", "--no-cache", "src", "tests", "scripts"]
    ],
    "lint": [
        ["poetry", "run", "ruff", "check", "--no-fix", "--no-cache", "src", "tests", "scripts"]
    ],
    "type-check": [["poetry", "run", "mypy"]],
    "test": [["poetry", "run", "pytest", "--cov", "--cov-report=term-missing"]],
    "test-unit": [["poetry", "run", "pytest", "tests/unit"]],
    "test-integration": [["poetry", "run", "pytest", "tests/integration"]],
    "test-tooling": [["poetry", "run", "pytest", "tests/integration/tooling"]],
    "test-tooling-unit": [["poetry", "run", "pytest", "tests/unit/tooling"]],
    "build": [["poetry", "build", "--format", "wheel"]],
}
TEST_TASKS = {"test", "test-unit", "test-integration"}
CHECKS = ["validate-config", "format-check", "lint", "type-check", "test"]


def clean() -> None:
    """Remove only generated outputs, preserving environments and task data."""
    # Restrict cleanup to the same named outputs and source/test caches on every OS.
    paths = [
        ROOT / name
        for name in (
            "build",
            "dist",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            "htmlcov",
            ".coverage",
            "coverage.xml",
            "coverage.json",
        )
    ]
    paths.extend(ROOT.glob(".coverage.*"))
    for name in ("src", "tests", "scripts"):
        tree = ROOT / name
        if tree.is_symlink() or tree.is_junction():
            continue
        for parent, directories, _ in os.walk(tree, followlinks=False):
            for directory in list(directories):
                path = Path(parent) / directory
                if path.is_symlink() or path.is_junction():
                    directories.remove(directory)
                elif directory == "__pycache__":
                    paths.append(path)
                    directories.remove(directory)
    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def checkout_state() -> dict[str, str]:
    """Fingerprint tracked bytes and Git state without following tracked symbolic links.

    Returns:
        Commit, working-tree status and deterministic tracked-content digest.
    """
    digest = hashlib.sha256()
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
    for name in sorted(path for path in paths if path):
        path = ROOT / os.fsdecode(name)
        digest.update(name + b"\0")
        if path.is_symlink():
            data = b"link:" + os.fsencode(os.readlink(path))
        elif path.is_file():
            data = b"file:" + path.read_bytes()
        else:
            data = b"missing"
        digest.update(hashlib.sha256(data).digest())
    return {
        "sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True),
        "tracked_digest": digest.hexdigest(),
    }


def identity() -> dict[str, object]:
    """Record the actual checkout and runtime without inferring other platform support.

    Returns:
        Exact Git revision, working-tree status, Python and operating-system identity.
    """
    # Keep evidence attributable to the executing OS, interpreter and exact source state.
    result: dict[str, object] = {
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "launcher_python": sys.version,
        "launcher_executable": sys.executable,
        "config_sha256": hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest(),
        **checkout_state(),
    }
    if sys.platform == "linux":
        result["distribution"] = platform.freedesktop_os_release()
    result["tool_python"] = json.loads(
        subprocess.check_output(
            [
                "poetry",
                "run",
                "python",
                "-c",
                "import importlib.metadata,json,sys; print(json.dumps({'version': sys.version,"
                " 'executable': sys.executable, 'tool_versions': {name:"
                " importlib.metadata.version(name) for name in"
                " ('coverage', 'pytest', 'pytest-cov')}}))",
            ],
            cwd=ROOT,
            text=True,
        )
    )
    result["tool_versions"] = cast(dict[str, object], result["tool_python"])["tool_versions"]
    return result


def run_command(command: list[str], environment: dict[str, str], log: Path | None = None) -> int:
    """Stream real child output to the console and an optional flushed evidence log.

    Args:
        command: Exact subprocess arguments, never shell-expanded.
        environment: Explicit child environment.
        log: Optional persistent partial log for failure or cancellation diagnosis.

    Returns:
        Child exit code, or 127 if the executable cannot start.
    """
    # Unbuffered Python children expose progress while a test is still running.
    environment = {**environment, "PYTHONUNBUFFERED": "1"}
    try:
        if log is None:
            return subprocess.run(command, cwd=ROOT, env=environment).returncode
        with (
            log.open("wb") as stream,
            subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            ) as process,
        ):
            assert process.stdout is not None
            # Normalize child newlines before the console applies its native translation.
            # The incremental decoder also handles CRLF split across pipe reads.
            decoder = io.IncrementalNewlineDecoder(
                codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True
            )
            while chunk := os.read(process.stdout.fileno(), 65536):
                stream.write(chunk)
                stream.flush()
                sys.stdout.write(decoder.decode(chunk))
                sys.stdout.flush()
            sys.stdout.write(decoder.decode(b"", final=True))
            sys.stdout.flush()
            return process.wait()
    except OSError as error:
        print(error, file=sys.stderr, flush=True)
        if log:
            with log.open("ab") as stream:
                stream.write((str(error) + "\n").encode("utf-8"))
        return 127


def main() -> int:
    """Execute one named task, optionally saving command logs and an OS evidence manifest.

    Returns:
        The first failing command's exit code, or zero after all selected commands pass.
    """
    # A portable Python entry point replaces shell-specific help, cleanup and pipelines.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "task",
        nargs="?",
        default="help",
        choices=[*COMMANDS, "help", "clean", "check", "check-local", "ci"],
    )
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument(
        "--platform-coverage",
        action="store_true",
        help="Collect one OS for the combined gate; requires evidence output.",
    )
    args = parser.parse_args()
    if args.task == "check-local":
        args.platform_coverage = True
        args.evidence_dir = args.evidence_dir or ROOT / ".coverage.local"
    if args.platform_coverage and (
        not args.evidence_dir or args.task not in {"check", "check-local", "ci", *TEST_TASKS}
    ):
        parser.error("--platform-coverage requires a check or test task and --evidence-dir")
    if args.task == "help":
        print("Tasks: " + ", ".join([*COMMANDS, "clean", "check", "check-local", "ci"]))
        return 0
    if args.task == "clean":
        clean()
        return 0
    tasks = CHECKS if args.task in {"check", "check-local", "ci"} else [args.task]
    if args.task == "build":
        tasks = ["validate-config", "build"]
    # Evidence is opt-in and captures failures as well as successful commands.
    evidence = args.evidence_dir.resolve() if args.evidence_dir else None
    manifest = identity() if evidence else {}
    results: list[dict[str, object]] = []
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
        for name in (
            "manifest.json",
            ".coverage",
            "coverage.xml",
            "coverage.json",
            "tests.xml",
            "pytest-evidence.json",
            "instrumentation.json",
            "instrumentation.log",
        ):
            (evidence / name).unlink(missing_ok=True)
        for shard in evidence.glob(".coverage.*"):
            if shard.is_file() or shard.is_symlink():
                shard.unlink()
    environment = os.environ.copy()
    if args.platform_coverage:
        print(
            "Platform checks only; combined native Windows + Linux coverage gate remains pending."
        )
        environment["AGENT_COMPANY_COVERAGE_CONTEXT"] = (
            platform.system() + "-" + platform.python_version() + "-" + args.task
        )
        manifest["coverage_gate"] = "pending native Windows + Linux combination"
    if evidence:
        environment["COVERAGE_FILE"] = str(evidence / ".coverage")

    def artifact_hashes(directory: Path) -> dict[str, str]:
        """Hash complete artifacts after the producing command exits."""
        return {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in {
                ".coverage",
                "coverage.xml",
                "coverage.json",
                "tests.xml",
                "pytest-evidence.json",
                "instrumentation.json",
                "instrumentation.log",
            }
            | {cast(str, result["log"]) for result in results}
            if (directory / name).is_file()
        }

    for task in tasks:
        for number, command in enumerate(COMMANDS[task]):
            command = list(command)
            smoke_code = 0
            if args.platform_coverage and task in TEST_TASKS:
                assert evidence is not None
                tool_python = manifest.get("tool_python")
                smoke_python = (
                    str(tool_python["executable"])
                    if isinstance(tool_python, dict)
                    and isinstance(tool_python.get("executable"), str)
                    else sys.executable
                )
                smoke = [
                    smoke_python,
                    str(Path(__file__).with_name("coverage_smoke.py")),
                    "--output",
                    str(evidence / "instrumentation.json"),
                ]
                smoke_code = run_command(smoke, environment, evidence / "instrumentation.log")
                manifest["instrumentation_exit_code"] = smoke_code
                if task != "test":
                    command.extend(["--cov", "--cov-report=term-missing"])
                command.append("--cov-fail-under=0")
            if task in {"test-tooling", "test-tooling-unit"}:
                command.extend(["--cov=scripts", "--cov-report=term-missing", "--cov-fail-under=0"])
            if evidence and task in {*TEST_TASKS, "test-tooling", "test-tooling-unit"}:
                command.extend(["-p", "scripts.pytest_evidence", "--durations=20"])
                environment["AGENT_COMPANY_PYTEST_EVIDENCE"] = str(
                    evidence / "pytest-evidence.json"
                )
                command.append(f"--junitxml={evidence / 'tests.xml'}")
                if task in {"test", "test-tooling", "test-tooling-unit"} or args.platform_coverage:
                    command.append(f"--cov-report=xml:{evidence / 'coverage.xml'}")
                    command.append(f"--cov-report=json:{evidence / 'coverage.json'}")
            print(" ".join(command), flush=True)
            started = time.monotonic()
            log = f"{task}-{number}.log"
            code = run_command(command, environment, evidence / log if evidence else None)
            if evidence:
                results.append(
                    {
                        "task": task,
                        "command": command,
                        "exit_code": code,
                        "seconds": round(time.monotonic() - started, 3),
                        "log": log,
                    }
                )
                manifest["commands"] = results
                final = checkout_state()
                manifest.update({"end_" + key: value for key, value in final.items()})
                manifest["candidate_unchanged"] = all(
                    manifest[key] == value for key, value in final.items()
                )
                manifest["artifacts_sha256"] = artifact_hashes(evidence)
                (evidence / "manifest.json").write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                )
                if not manifest["candidate_unchanged"] and args.task in {
                    "check",
                    "check-local",
                    "ci",
                    *TEST_TASKS,
                }:
                    print("Candidate changed during validation; evidence is diagnostic only.")
                    return code or 1
            if code:
                return code
            if smoke_code:
                return smoke_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
