"""Run the same contributor checks on native Windows, Linux and macOS without Make."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

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
    "build": [["poetry", "build", "--format", "wheel"]],
}
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
        "sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True),
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
                "import json,sys; print(json.dumps({'version': sys.version,"
                " 'executable': sys.executable}))",
            ],
            cwd=ROOT,
            text=True,
        )
    )
    return result


def main() -> int:
    """Execute one named task, optionally saving command logs and an OS evidence manifest.

    Returns:
        The first failing command's exit code, or zero after all selected commands pass.
    """
    # A portable Python entry point replaces shell-specific help, cleanup and pipelines.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "task", nargs="?", default="help", choices=[*COMMANDS, "help", "clean", "check", "ci"]
    )
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument(
        "--platform-coverage",
        action="store_true",
        help="Collect one OS for the combined gate; requires evidence output.",
    )
    args = parser.parse_args()
    if args.platform_coverage and (
        not args.evidence_dir or args.task not in {"check", "test", "ci"}
    ):
        parser.error("--platform-coverage requires check/test/ci and --evidence-dir")
    if args.task == "help":
        print("Tasks: " + ", ".join([*COMMANDS, "clean", "check", "ci"]))
        return 0
    if args.task == "clean":
        clean()
        return 0
    tasks = CHECKS if args.task in {"check", "ci"} else [args.task]
    if args.task == "build":
        tasks = ["validate-config", "build"]
    # Evidence is opt-in and captures failures as well as successful commands.
    evidence = args.evidence_dir.resolve() if args.evidence_dir else None
    manifest = identity() if evidence else {}
    results: list[dict[str, object]] = []
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    if args.platform_coverage:
        environment["AGENT_COMPANY_COVERAGE_CONTEXT"] = (
            platform.system() + "-" + platform.python_version()
        )
        manifest["coverage_gate"] = "pending native Windows + Linux combination"
    if evidence:
        environment["COVERAGE_FILE"] = str(evidence / ".coverage")
    for task in tasks:
        for number, command in enumerate(COMMANDS[task]):
            command = list(command)
            if evidence and task == "test":
                command.extend(
                    [
                        f"--junitxml={evidence / 'tests.xml'}",
                        f"--cov-report=xml:{evidence / 'coverage.xml'}",
                    ]
                )
            if args.platform_coverage and task == "test":
                command.append("--cov-fail-under=0")
            print(" ".join(command), flush=True)
            started = time.monotonic()
            try:
                process = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE if evidence else None,
                    stderr=subprocess.STDOUT if evidence else None,
                )
                code = process.returncode
                output = process.stdout or ""
            except OSError as error:
                code, output = 127, str(error)
                print(output, file=sys.stderr)
            if evidence:
                log = f"{task}-{number}.log"
                (evidence / log).write_text(output, encoding="utf-8")
                print(output, end="", flush=True)
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
                (evidence / "manifest.json").write_text(
                    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                )
            if code:
                return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
