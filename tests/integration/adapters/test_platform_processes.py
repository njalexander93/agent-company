"""Exercise real native-shell transports and bounded hook workers in disposable copies."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_company.adapters import common
from tests.platform_support import environment_python, link_directory, shell_command
from tests.support import ROOT

pytestmark = pytest.mark.integration

HOSTS = [("codex", "PreToolUse", 0), ("claude", "PreToolUse", 2), ("cursor", "preToolUse", 2)]


@pytest.mark.parametrize(("host", "event", "status"), HOSTS)
def test_hook_bounds_malformed_and_open_stdin(
    host: str, event: str, status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject malformed bytes and an unclosed input pipe using the native process deadline.

    Args:
        host: Actual adapter entry point.
        event: Host event name retained in the parameter set for worker tests.
        status: Host's documented failure exit convention.
        monkeypatch: Scoped removal of imported-Claude skip state.

    Raises:
        AssertionError: Input handling hangs, crashes or loses the host failure convention.
    """
    monkeypatch.delenv("CURSOR_VERSION", raising=False)
    argv = [sys.executable, "-m", "agent_company.adapters." + host]
    malformed = subprocess.run(argv, input="{", capture_output=True, text=True, timeout=6)
    assert malformed.returncode == status, malformed.stderr
    assert isinstance(json.loads(malformed.stdout), dict)
    assert "Traceback" not in malformed.stderr
    # Invalid event-name types must never escape failure rendering as unhashable keys.
    for event_name in ([], {}, None, 1):
        invalid = subprocess.run(
            argv,
            input=json.dumps({"hook_event_name": event_name}),
            capture_output=True,
            text=True,
            timeout=6,
        )
        assert invalid.returncode == status, invalid.stdout + invalid.stderr
        assert isinstance(json.loads(invalid.stdout), dict)
        assert "INVALID_REQUEST" in invalid.stdout + invalid.stderr
        assert "Traceback" not in invalid.stderr
    # Keep stdin open after a partial envelope. The supervisor must exit without waiting for EOF.
    with subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:
        assert process.stdin is not None
        process.stdin.write(json.dumps({"hook_event_name": event}).encode()[:-1])
        process.stdin.flush()
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            pytest.fail("Hook did not bound an open stdin pipe")
        finally:
            process.stdin.close()
        assert process.stdout is not None and process.stderr is not None
        output, errors = process.stdout.read(), process.stderr.read()
    assert process.returncode == status, errors
    assert isinstance(json.loads(output), dict)
    assert b"BUSY" in errors


@pytest.mark.parametrize(("host", "event", "status"), HOSTS)
@pytest.mark.parametrize("fault", ["late_write", "exception"])
def test_supervisor_reaps_worker_and_preserves_failure_contract(
    host: str, event: str, status: int, fault: str, tmp_path: Path
) -> None:
    """Inject worker behavior in copied source while retaining the real process supervisor.

    Args:
        host: Adapter copied into an isolated source tree.
        event: Host-specific event name.
        status: Expected native failure exit convention.
        fault: Controlled worker timeout or lifecycle exception.
        tmp_path: Disposable source and sentinel location.

    Raises:
        AssertionError: A timed-out worker survives or an exception loses its diagnostic.
    """
    source = tmp_path / "src"
    shutil.copytree(ROOT / "src", source, ignore=shutil.ignore_patterns("__pycache__"))
    path = source / "agent_company/adapters" / (host + ".py")
    sentinel = tmp_path / "late-write"
    started = tmp_path / "worker-started"
    if fault == "late_write":
        body = (
            "    import time\n    from pathlib import Path\n"
            f"    Path({str(started)!r}).write_bytes(b'started')\n"
            "    time.sleep(3)\n"
            f"    Path({str(sentinel)!r}).write_bytes(b'escaped timeout')\n"
            "    return {}\n"
        )
    else:
        body = '    raise core.WorkspaceError("BINDING_CONFLICT")\n'
    original = path.read_text(encoding="utf-8")
    marker = '\nif __name__ == "__main__":'
    assert marker in original
    path.write_text(
        original.replace(marker, "\ndef handle(event):\n" + body + marker), encoding="utf-8"
    )
    environment = {**os.environ, "PYTHONPATH": str(source)}
    environment.pop("CURSOR_VERSION", None)
    # Inject only the copied handler. Processes, deadlines and termination remain production code.
    result = subprocess.run(
        [sys.executable, str(path)],
        input=json.dumps({"hook_event_name": event}),
        env=environment,
        capture_output=True,
        text=True,
        timeout=6,
    )
    assert result.returncode == status, result.stdout + result.stderr
    response = json.loads(result.stdout)
    assert isinstance(response, dict)
    # A valid pre-tool envelope needs an explicit denial, even when Codex exits successfully.
    if host == "cursor":
        assert response["permission"] == "deny"
    else:
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    if fault == "late_write":
        assert started.exists(), "The mutation worker must actually start before the deadline"
        assert "BUSY" in result.stderr
        # An escaped worker would write within three seconds of its recorded start.
        time.sleep(3.2)
        assert not sentinel.exists()
    else:
        assert "BINDING_CONFLICT" in result.stdout


def test_supervisor_rejects_oversized_worker_output(tmp_path: Path) -> None:
    """The real worker transport bounds a handler response before host publication."""
    source = tmp_path / "src"
    shutil.copytree(ROOT / "src", source, ignore=shutil.ignore_patterns("__pycache__"))
    adapter = source / "agent_company/adapters/codex.py"
    original = adapter.read_text(encoding="utf-8")
    marker = '\nif __name__ == "__main__":'
    assert marker in original
    adapter.write_text(
        original.replace(
            marker,
            '\ndef handle(event):\n    return {"oversized": "x" * (1024 * 1024)}\n' + marker,
        ),
        encoding="utf-8",
    )
    environment = {**os.environ, "PYTHONPATH": str(source)}
    result = subprocess.run(
        [sys.executable, str(adapter)],
        input=json.dumps({"hook_event_name": "PreToolUse"}),
        env=environment,
        capture_output=True,
        text=True,
        timeout=6,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "SIZE_LIMIT" in response["hookSpecificOutput"]["permissionDecisionReason"]
    assert len(result.stdout) < 2048


@pytest.mark.skipif(
    os.name != "nt", reason="Native Windows PowerShell transport; POSIX has shell tests"
)
@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
def test_windows_bootstrap_survives_literal_paths_and_json(
    shell: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute encoded bootstrap through both native PowerShell versions with quoted paths.

    Args:
        shell: Required PowerShell 5.1 or 7 executable on the Windows validation runner.
        tmp_path: Disposable Git checkout with an apostrophe and spaces.
        monkeypatch: Scoped encoder path replacement pointing only into the fixture.

    Raises:
        AssertionError: A native shell corrupts the request or interprets literal metacharacters.
    """
    assert shutil.which(shell), f"Required native shell missing: {shell}"
    root = tmp_path / "checkout O'Brien with spaces"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    link_directory(root / ".venv", Path(sys.prefix))
    script = root / "bootstrap helper.py"
    shutil.copy2(ROOT / "src/agent_company/adapters/bootstrap.py", script)
    monkeypatch.setattr(common, "PYTHON", str(environment_python(root / ".venv")))
    monkeypatch.setattr(common, "WINDOWS_BOOTSTRAP", script)
    request = {
        "schema_version": 1,
        "operation": "diagnose",
        "request_id": "native-shell-fixture",
        "worktree": str(root),
        "host": "codex",
        "session_id": "O'Brien-$HOME-`literal`",
    }
    command = common.bootstrap_command(request, "codex")
    assert common.bootstrap_request(command, "codex", common.PYTHON, common.LIFECYCLE) == request
    result = subprocess.run(
        shell_command(shell, command), capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    assert response["ok"] is True and response["code"] == "REGISTRATION_REQUIRED"
    assert not (root / ".task").exists()
