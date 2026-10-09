"""Prove child-only line and branch collection with the project coverage configuration."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from coverage import Coverage

ROOT = Path(__file__).resolve().parents[1]
CHILD = """def choose(value):
    if value:
        return 'yes'
    return 'no'
"""
TEST = """import subprocess
import sys
from pathlib import Path

def test_child_only():
    child = Path(__file__).with_name('child_only.py')
    for value, expected in (('1', 'yes'), ('0', 'no')):
        result = subprocess.run([sys.executable, str(child), value],
                                check=True, capture_output=True, text=True)
        assert result.stdout.strip() == expected
"""


def probe(*, instrumented: bool = True, isolated_child: bool = False) -> dict[str, object]:
    """Run a disposable child-only pytest case and inspect its real coverage arcs."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agent-company-coverage-smoke-") as name:
        directory = Path(name)
        child = directory / "child_only.py"
        child.write_text(
            CHILD + "\nif __name__ == '__main__':\n"
            "    import sys\n    print(choose(sys.argv[1] == '1'))\n",
            encoding="utf-8",
        )
        test_source = TEST.replace(
            "[sys.executable, str(child), value]",
            "[sys.executable, '-I', str(child), value]"
            if isolated_child
            else "[sys.executable, str(child), value]",
        )
        (directory / "test_child.py").write_text(test_source, encoding="utf-8")
        data_file = directory / ".coverage"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--override-ini=addopts=",
            "--cov",
            str(directory),
            "--cov-config",
            str(ROOT / "pyproject.toml"),
            "--cov-report=",
            "--cov-fail-under=0",
            str(directory / "test_child.py"),
        ]
        environment = os.environ.copy()
        environment.pop("COVERAGE_PROCESS_CONFIG", None)
        environment.pop("AGENT_COMPANY_PYTEST_EVIDENCE", None)
        environment["COVERAGE_FILE"] = str(data_file)
        if not instrumented:
            # A disposable alternate configuration is the negative control.
            config = directory / "disabled.toml"
            config.write_text(
                "[tool.coverage.run]\nbranch = true\nrelative_files = true\n",
                encoding="utf-8",
            )
            command[command.index(str(ROOT / "pyproject.toml"))] = str(config)
        result = subprocess.run(
            command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=20
        )
        coverage = Coverage(data_file=str(data_file), config_file=False)
        coverage.load()
        data = coverage.get_data()
        measured = next(
            (path for path in data.measured_files() if path.endswith("child_only.py")), None
        )
        arcs = set(data.arcs(measured) or []) if measured else set()
        lines = set(data.lines(measured) or []) if measured else set()
        # Function body and both guard destinations execute only in children.
        expected_lines = {1, 2, 3, 4}
        expected_arcs = {(2, 3), (2, 4)}
        passed = result.returncode == 0 and expected_lines <= lines and expected_arcs <= arcs
        return {
            "passed": passed,
            "instrumented": instrumented,
            "isolated_child": isolated_child,
            "test_exit_code": result.returncode,
            "child_lines": sorted(lines),
            "child_arcs": sorted([list(arc) for arc in arcs]),
            "expected_lines": sorted(expected_lines),
            "expected_arcs": sorted([list(arc) for arc in expected_arcs]),
            "branch_data": data.has_arcs(),
            "config_sha256": hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest(),
            "seconds": round(time.monotonic() - started, 3),
            "test_output": result.stdout + result.stderr,
        }


def main() -> int:
    """Write a machine-readable instrumentation receipt for a native suite."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"child-only coverage: {'passed' if receipt['passed'] else 'failed'}")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
