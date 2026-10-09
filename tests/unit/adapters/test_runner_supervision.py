"""Model bounded runner decisions while native process tests cover OS behavior."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_company.adapters import runner

pytestmark = pytest.mark.unit


def handler(_event: dict[str, object]) -> dict[str, object]:
    """Provide a known adapter module name for worker selection."""
    return {}


handler.__module__ = "agent_company.adapters.codex"


def failure(name: str, code: str) -> dict[str, object]:
    """Expose the bounded event and error selected by the runner."""
    return {"name": name, "code": code}


class Worker:
    """Record the modeled worker lifecycle and its bounded response."""

    def __init__(
        self, output: bytes = b'{"ok":true}', returncode: int = 0, timeout: bool = False
    ) -> None:
        """Set one response and optional timeout for a deterministic supervisor case."""
        self.output = output
        self.returncode = returncode
        self.timeout = timeout
        self.calls: list[bytes | None] = []
        self.killed = False

    def __enter__(self) -> Worker:
        """Expose the modeled process without spawning one."""
        return self

    def __exit__(self, *_args: object) -> None:
        """End the modeled process context."""

    def communicate(
        self, data: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        """Return one response or simulate a deadline followed by reap."""
        self.calls.append(data)
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("worker", timeout or 0)
        return self.output, b""

    def kill(self) -> None:
        """Record forced termination before the second communicate call."""
        self.killed = True


def input_bytes(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> None:
    """Supply a bounded stdin stream without a real pipe or thread delay."""
    chunks = iter([raw, b""])
    monkeypatch.setattr(runner.os, "read", lambda _fd, _size: next(chunks))
    monkeypatch.setattr(runner.sys.stdin, "fileno", lambda: 0)


def install_worker(monkeypatch: pytest.MonkeyPatch, worker: Worker) -> list[list[str]]:
    """Capture worker arguments at the subprocess lookup point."""
    starts: list[list[str]] = []

    def popen(argv: list[str], **_options: object) -> Worker:
        """Return the deterministic worker with its command captured."""
        starts.append(argv)
        return worker

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    return starts


def test_runner_returns_worker_result_after_bounded_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Send exact input bytes to the selected worker and preserve its result."""
    raw = b'{"hook_event_name":"PreToolUse"}'
    input_bytes(monkeypatch, raw)
    worker = Worker(output=b'{"decision":"deny"}')
    starts = install_worker(monkeypatch, worker)
    assert runner.run(handler, failure) == 0
    assert worker.calls == [raw]
    assert starts[0][-2:] == ["--worker", "codex"]
    assert json.loads(capsys.readouterr().out) == {"decision": "deny"}


def test_runner_selects_script_entrypoint_by_source_stem(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A direct file hook entry uses its known adapter filename for worker selection."""
    input_bytes(monkeypatch, b'{"hook_event_name":"PreToolUse"}')
    namespace: dict[str, object] = {
        "__name__": "__main__",
        "__file__": str(tmp_path / "checkout/src/agent_company/adapters/codex.py"),
    }
    exec("def direct_handler(event):\n    return {}", namespace)
    direct_handler = namespace["direct_handler"]
    assert callable(direct_handler)
    starts = install_worker(monkeypatch, Worker())
    assert runner.run(direct_handler, failure) == 0  # type: ignore[arg-type]
    assert starts[0][-1] == "codex"


def test_runner_forwards_worker_stderr_without_changing_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Worker diagnostics reach stderr while the typed response stays intact."""
    input_bytes(monkeypatch, b'{"hook_event_name":"PreToolUse"}')

    class NoisyWorker(Worker):
        """Return a valid result and one bounded diagnostic byte stream."""

        def communicate(
            self, data: bytes | None = None, timeout: float | None = None
        ) -> tuple[bytes, bytes]:
            """Record the request and supply stderr beside the response."""
            self.calls.append(data)
            return self.output, b"worker note"

    install_worker(monkeypatch, NoisyWorker())
    assert runner.run(handler, failure) == 0
    output = capsys.readouterr()
    assert "worker note" in output.err
    assert json.loads(output.out) == {"ok": True}


def test_runner_reaps_worker_before_timeout_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kill and reap a timed-out worker before returning BUSY."""
    input_bytes(monkeypatch, b'{"hook_event_name":"PreToolUse"}')
    worker = Worker(timeout=True)
    install_worker(monkeypatch, worker)
    assert runner.run(handler, failure) == 2
    assert worker.killed is True
    assert worker.calls == [b'{"hook_event_name":"PreToolUse"}', None]
    assert json.loads(capsys.readouterr().out) == {"name": "PreToolUse", "code": "BUSY"}


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"[]", "INVALID_REQUEST"),
        (b'{"hook_event_name":1}', "INVALID_REQUEST"),
        (b"{}", "INVALID_REQUEST"),
    ],
)
def test_runner_rejects_malformed_envelope_before_worker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    raw: bytes,
    code: str,
) -> None:
    """Keep malformed input away from process creation."""
    input_bytes(monkeypatch, raw)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kw: pytest.fail("worker"))
    assert runner.run(handler, failure) == 2
    assert json.loads(capsys.readouterr().out)["code"] == code


def test_runner_rejects_invalid_worker_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Turn an unknown worker exit status into a recovery diagnostic."""
    input_bytes(monkeypatch, b'{"hook_event_name":"PreToolUse"}')
    install_worker(monkeypatch, Worker(returncode=9))
    assert runner.run(handler, failure) == 2
    assert json.loads(capsys.readouterr().out) == {
        "name": "PreToolUse",
        "code": "RECOVERY_REQUIRED",
    }


def test_worker_main_rejects_unknown_entry_before_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid importing adapters for malformed worker arguments."""
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--worker", "unknown"])
    monkeypatch.setattr(
        runner.importlib, "import_module", lambda _name: pytest.fail("unexpected import")
    )
    assert runner.main() == 2


def test_worker_main_returns_translated_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emit one JSON line from the selected adapter handler."""
    output = io.BytesIO()
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--worker", "cursor"])
    monkeypatch.setattr(runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"a":1}')))
    monkeypatch.setattr(runner.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(
        runner.importlib,
        "import_module",
        lambda name: SimpleNamespace(handle=lambda event: {"a": event["a"] + 1}),
    )
    assert runner.main() == 0
    assert output.getvalue() == b'{"a":2}\n'


def test_runner_rejects_oversized_input_before_worker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stop after one byte beyond the bounded hook input limit."""
    input_bytes(monkeypatch, b"x" * (runner.INPUT_LIMIT + 1))
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kw: pytest.fail("worker"))
    assert runner.run(handler, failure) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "SIZE_LIMIT"


def test_runner_reports_stdin_read_failure_without_starting_worker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep a failed host pipe read within the bounded recovery response."""
    monkeypatch.setattr(runner.sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(runner.os, "read", lambda *_args: (_ for _ in ()).throw(OSError("pipe")))
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kw: pytest.fail("worker"))
    assert runner.run(handler, failure) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "RECOVERY_REQUIRED"


def test_runner_preserves_worker_failure_status_and_bounded_diagnostic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep a worker's documented failure result and host-specific exit code."""
    input_bytes(monkeypatch, b'{"hook_event_name":"PreToolUse"}')
    install_worker(monkeypatch, Worker(output=b'{"decision":"deny"}', returncode=2))
    assert runner.run(handler, failure, error_status=0) == 0
    assert json.loads(capsys.readouterr().out) == {"decision": "deny"}


def test_worker_main_renders_bounded_handler_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a public diagnostic without serializing handler exception details."""
    output = io.BytesIO()
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--worker", "cursor"])
    monkeypatch.setattr(
        runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"hook_event_name":"preToolUse"}'))
    )
    monkeypatch.setattr(runner.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(
        runner.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            handle=lambda _event: (_ for _ in ()).throw(OSError("private detail")),
            failure=lambda name, code: {"name": name, "code": code},
        ),
    )
    assert runner.main() == 2
    assert json.loads(output.getvalue()) == {"name": "preToolUse", "code": "RECOVERY_REQUIRED"}


def test_worker_main_bounds_oversized_handler_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace an oversized native result with a small host failure response."""
    output = io.BytesIO()
    monkeypatch.setattr(runner.sys, "argv", ["runner", "--worker", "cursor"])
    monkeypatch.setattr(
        runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b'{"hook_event_name":"preToolUse"}'))
    )
    monkeypatch.setattr(runner.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(
        runner.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            handle=lambda _event: {"text": "x" * (runner.INPUT_LIMIT + 1)},
            failure=lambda name, code: {"name": name, "code": code},
        ),
    )
    assert runner.main() == 2
    assert json.loads(output.getvalue()) == {"name": "preToolUse", "code": "SIZE_LIMIT"}
