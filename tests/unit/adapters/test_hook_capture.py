"""Check bounded metadata selection and non-authoritative diagnostic failures."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nplet.adapters import hook_capture

pytestmark = pytest.mark.unit


def test_response_shape_omits_output_even_for_protocol_control() -> None:
    """Retain fixed field types without retaining stdout or arbitrary response content."""
    # A command name never grants permission to record its output.
    event = {
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
        "tool_response": '{"stdout":"PRIVATE","exit_code":1,"PRIVATE":"secret"}',
    }
    observation = hook_capture.metadata(event, {}, 0)
    assert "PRIVATE" not in json.dumps(observation)
    assert "control_response" not in observation
    assert observation["response_shape"]["exit_code"] == {"type": "int", "value": 1}
    assert observation["response_shape"]["stdout"] == {"type": "str"}


def test_capture_omits_nested_payloads_unknown_keys_and_unrecognized_decisions() -> None:
    """Prevent nested decision-shaped payloads and arbitrary keys leaking into evidence."""
    # A malicious payload may imitate an output envelope; only fixed root metadata survives.
    event = {
        "session_id": "parent",
        "agent_id": "child",
        "tool_use_id": "tool",
        "PRIVATE_KEY": "PRIVATE_TEXT",
        "tool_input": {"hookSpecificOutput": {"permissionDecision": "PRIVATE_TEXT"}},
        "tool_response": {"hookSpecificOutput": {"decision": "PRIVATE_TEXT"}},
    }
    result = {
        "hookSpecificOutput": {"permissionDecision": "PRIVATE_TEXT"},
        "PRIVATE_KEY": "PRIVATE_TEXT",
    }
    observation = hook_capture.metadata(event, result, 0)
    encoded = json.dumps(observation)
    assert "PRIVATE" not in encoded
    assert observation["native"] == {
        "session_id": "parent",
        "agent_id": "child",
        "tool_use_id": "tool",
    }
    assert observation["adapter_decisions"] == {}


def test_capture_does_not_traverse_deep_payloads_or_infer_child_identity() -> None:
    """Ignore deeply nested contents and retain a missing child ID as missing."""
    # Depth is irrelevant because the observer never walks nested native contents.
    payload: object = "PRIVATE_TEXT"
    for _ in range(5000):
        payload = {"nested": payload}
    observation = hook_capture.metadata({"session_id": "parent", "tool_input": payload}, {}, 0)
    assert observation["native"] == {"session_id": "parent"}
    assert "agent_id" not in observation["native"]
    assert "PRIVATE" not in json.dumps(observation)


def test_capture_disabled_has_no_process_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave the default path unchanged when the private opt-in is absent.

    Args:
        monkeypatch: Fixture restoring environment and process creation.
    """
    # No observer worker may be created by default.
    monkeypatch.delenv(hook_capture.ENVIRONMENT, raising=False)
    monkeypatch.setattr(hook_capture.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("spawned"))
    hook_capture.capture({}, {}, 0)


@pytest.mark.parametrize("error", [OSError("write"), RecursionError("depth"), ValueError("format")])
def test_capture_format_failure_is_non_authoritative(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """Contain diagnostic formatting failures before a worker can affect the result.

    Args:
        monkeypatch: Fixture isolating the private opt-in and formatter.
        error: Representative diagnostic failure to contain.
    """
    # The original adapter result exists before this optional observation starts.
    monkeypatch.setenv(hook_capture.ENVIRONMENT, "/private/tmp/unused")
    monkeypatch.setattr(
        hook_capture, "os", SimpleNamespace(name="posix", environ=hook_capture.os.environ)
    )
    monkeypatch.setattr(hook_capture, "metadata", lambda *_a: (_ for _ in ()).throw(error))
    result = {"hookSpecificOutput": {"permissionDecision": "deny"}}
    hook_capture.capture({}, result, 2)
    assert result == {"hookSpecificOutput": {"permissionDecision": "deny"}}


def test_writer_rejects_oversize_before_io(tmp_path: Path) -> None:
    """Reject excess diagnostic bytes without creating a capture artifact.

    Args:
        tmp_path: Disposable capture destination.
    """
    # Byte limits are checked before opening the destination directory.
    with pytest.raises(ValueError, match="Invalid capture boundary"):
        hook_capture.write_record(tmp_path, b"x" * (hook_capture.BYTE_LIMIT + 1))
    assert not list(tmp_path.iterdir())


def test_capture_timeout_kills_and_reaps_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contain a hung diagnostic after the original adapter has completed.

    Args:
        monkeypatch: Fixture isolating environment and diagnostic process creation.
    """
    # Reuse the deterministic process model; no native subprocess runs in this unit test.
    from tests.unit.adapters.test_runner_supervision import Worker

    writer = Worker(timeout=True)
    monkeypatch.setenv(hook_capture.ENVIRONMENT, "/private/tmp/unused")
    monkeypatch.setattr(
        hook_capture, "os", SimpleNamespace(name="posix", environ=hook_capture.os.environ)
    )
    monkeypatch.setattr(hook_capture.subprocess, "Popen", lambda *_a, **_kw: writer)
    result = {"decision": "block"}
    hook_capture.capture({}, result, 2)
    assert writer.killed
    assert len(writer.calls) == 2 and writer.calls[1] is None
    assert result == {"decision": "block"}


def test_capture_context_close_failure_is_non_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the guard response even if a diagnostic process context fails to close.

    Args:
        monkeypatch: Fixture isolating environment and diagnostic process creation.
    """
    from tests.unit.adapters.test_runner_supervision import Worker

    class CloseFailure(Worker):
        """Model an ordinary failure while retiring only the diagnostic context."""

        def __exit__(self, *_args: object) -> None:
            """Raise a close error after the diagnostic process has completed.

            Args:
                _args: Context-manager exception fields.

            Raises:
                OSError: The simulated diagnostic context cannot close.
            """
            raise OSError("diagnostic close")

    # Closing this observer must never replace the already-completed adapter decision.
    monkeypatch.setenv(hook_capture.ENVIRONMENT, "/private/tmp/unused")
    monkeypatch.setattr(
        hook_capture, "os", SimpleNamespace(name="posix", environ=hook_capture.os.environ)
    )
    monkeypatch.setattr(hook_capture.subprocess, "Popen", lambda *_a, **_kw: CloseFailure())
    result = {"decision": "block"}
    hook_capture.capture({}, result, 2)
    assert result == {"decision": "block"}


@pytest.fixture
def writer_boundary(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Model OS descriptors without opening files or assuming native POSIX support.

    Args:
        monkeypatch: Fixture restoring the module's OS boundary.

    Returns:
        Observable descriptor allocator, stream and close operations.
    """
    from unittest.mock import MagicMock, Mock

    # Separate directory ownership from the stream's transferred file descriptor.
    stream = MagicMock()
    stream.__enter__.return_value = stream
    boundary = SimpleNamespace(
        O_RDONLY=1,
        O_DIRECTORY=2,
        O_NOFOLLOW=4,
        O_WRONLY=8,
        O_CREAT=16,
        O_EXCL=32,
        open=Mock(side_effect=[10, 11]),
        fstat=Mock(return_value=SimpleNamespace(st_mode=0o700)),
        fdopen=Mock(return_value=stream),
        close=Mock(),
        stream=stream,
    )
    # Install the modeled OS only for this test and expose its observations.
    monkeypatch.setattr(hook_capture, "os", boundary)
    return boundary


def test_write_record_transfers_file_ownership_and_closes_directory(writer_boundary):
    """A successful private write transfers file ownership exactly once to its stream.

    Args:
        writer_boundary: Modeled descriptor and stream boundary.
    """
    # Write through modeled descriptors and inspect ownership transfer.
    hook_capture.write_record(Path.cwd(), b"redacted")
    # Verify exclusive private allocation, stream retirement and directory cleanup.
    assert writer_boundary.open.call_args_list[1].args == ("event-0000.json", 60, 0o600)
    assert writer_boundary.open.call_args_list[1].kwargs == {"dir_fd": 10}
    writer_boundary.fdopen.assert_called_once_with(11, "wb")
    writer_boundary.stream.write.assert_called_once_with(b"redacted")
    writer_boundary.stream.__exit__.assert_called_once()
    writer_boundary.close.assert_called_once_with(10)


@pytest.mark.parametrize("fault", ["private", "full", "open", "write", "fdopen"])
def test_write_record_failures_close_owned_directory(writer_boundary, monkeypatch, fault):
    """Rejected or failed writes close the directory and never overwrite existing slots.

    Args:
        writer_boundary: Modeled descriptor and stream boundary.
        monkeypatch: Fixture restoring slot limits.
        fault: Failing filesystem boundary under examination.
    """
    # Retain the injected exception so an unrelated failure cannot satisfy this case.
    injected = OSError("injected writer failure")
    # Inject failures at distinct ownership and publication boundaries.
    if fault == "private":
        writer_boundary.fstat.return_value.st_mode = 0o755
    elif fault == "full":
        monkeypatch.setattr(hook_capture, "FILE_LIMIT", 2)
        writer_boundary.open.side_effect = [10, FileExistsError(), FileExistsError()]
    elif fault == "open":
        writer_boundary.open.side_effect = [10, injected]
    elif fault == "write":
        writer_boundary.stream.write.side_effect = injected
    else:
        writer_boundary.fdopen.side_effect = injected
    # Failure must retire the directory even when no stream can be constructed.
    with pytest.raises(OSError) as captured:
        hook_capture.write_record(Path.cwd(), b"redacted")
    # Match policy diagnostics exactly; OS failure cases must retain exception identity.
    if fault == "private":
        assert str(captured.value) == "Capture directory must be private"
    elif fault == "full":
        assert str(captured.value) == "Capture slots exhausted"
    else:
        assert captured.value is injected
    # Verify cleanup according to the last ownership boundary reached.
    assert any(call.args == (10,) for call in writer_boundary.close.call_args_list)
    if fault in {"private", "full", "open"}:
        writer_boundary.fdopen.assert_not_called()
        writer_boundary.stream.write.assert_not_called()
    elif fault == "write":
        writer_boundary.stream.__exit__.assert_called_once()
    else:
        # The descriptor has no stream owner when fdopen fails.
        assert any(call.args == (11,) for call in writer_boundary.close.call_args_list)


def test_write_record_skips_existing_slot_without_overwrite(writer_boundary):
    """A collision allocates another exclusive slot instead of replacing its contents.

    Args:
        writer_boundary: Modeled descriptor and stream boundary.
    """
    # Preserve an occupied slot and allow only the next exclusive allocation.
    writer_boundary.open.side_effect = [10, FileExistsError(), 11]
    # Exercise collision recovery without touching an existing record.
    hook_capture.write_record(Path.cwd(), b"redacted")
    # Verify the alternate slot receives the data and all owned resources retire.
    assert writer_boundary.open.call_args_list[2].args[0] == "event-0001.json"
    writer_boundary.stream.write.assert_called_once_with(b"redacted")
    writer_boundary.close.assert_called_once_with(10)


def test_write_record_rejects_relative_path_before_descriptor_allocation(writer_boundary):
    """A relative capture destination cannot allocate descriptors.

    Args:
        writer_boundary: Modeled descriptor and stream boundary.
    """
    # Reject the unsafe path before it can acquire any descriptor.
    with pytest.raises(ValueError, match="^Invalid capture boundary$"):
        hook_capture.write_record(Path("relative"), b"redacted")
    # Confirm rejection has no allocation or cleanup effects.
    writer_boundary.open.assert_not_called()
    writer_boundary.close.assert_not_called()


@pytest.mark.parametrize(
    "argv,platform",
    [
        (["capture"], "posix"),
        (["capture", "wrong", "/capture"], "posix"),
        (["capture", "--write", "/capture"], "nt"),
    ],
)
def test_main_rejects_invalid_invocation_without_reading(monkeypatch, argv, platform):
    """Only the internal POSIX writer invocation may consume standard input.

    Args:
        monkeypatch: Fixture restoring process arguments and input.
        argv: Simulated command-line arguments.
        platform: Simulated OS family.
    """
    from unittest.mock import Mock

    # Prepare isolated input and output boundaries.
    source, writer = Mock(), Mock()
    monkeypatch.setattr(hook_capture.sys, "argv", argv)
    monkeypatch.setattr(hook_capture.sys, "stdin", SimpleNamespace(buffer=source))
    monkeypatch.setattr(hook_capture, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(hook_capture, "write_record", writer)
    # Dispatch the invalid invocation and verify it cannot consume input.
    assert hook_capture.main() == 2
    source.read.assert_not_called()
    writer.assert_not_called()


@pytest.mark.parametrize("failure", [None, "read", "write"])
def test_main_bounds_input_and_reports_writer_failure(monkeypatch, failure):
    """Writer dispatch reads one bounded payload and reports failures with status two.

    Args:
        monkeypatch: Fixture restoring process boundaries.
        failure: Input or output failure to inject, or None for success.
    """
    from unittest.mock import Mock

    # Model process boundaries without launching or writing a real diagnostic process.
    # Prepare isolated input and output boundaries.
    source, writer = Mock(), Mock()
    source.read.return_value = b"redacted"
    # Select a failure before or after the bounded input read.
    if failure == "read":
        source.read.side_effect = OSError("input")
    elif failure == "write":
        writer.side_effect = OSError("output")
    # Install the internal writer invocation against the modeled process I/O.
    monkeypatch.setattr(hook_capture.sys, "argv", ["capture", "--write", str(Path.cwd())])
    monkeypatch.setattr(hook_capture.sys, "stdin", SimpleNamespace(buffer=source))
    monkeypatch.setattr(hook_capture, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(hook_capture, "write_record", writer)
    # Verify the exit status and the exact bounded read request.
    assert hook_capture.main() == (0 if failure is None else 2)
    source.read.assert_called_once_with(hook_capture.BYTE_LIMIT + 1)
    # Input failure must prevent writes; all other cases dispatch the exact bytes.
    if failure == "read":
        writer.assert_not_called()
    else:
        writer.assert_called_once_with(Path.cwd(), b"redacted")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("invalid", {"type": "str"}),
        ("x" * 65537, {"type": "str"}),
        (
            ["private", 4, None, {}, "omitted"],
            {"type": "list", "item_types": ["str", "int", "NoneType", "dict"]},
        ),
        (
            {"interrupted": True, "isError": False, "exit_code": True},
            {
                "type": "dict",
                "interrupted": {"type": "bool", "value": True},
                "isError": {"type": "bool", "value": False},
                "exit_code": {"type": "bool"},
            },
        ),
    ],
)
def test_response_shape_retains_only_bounded_typed_facts(value, expected):
    """Malformed, oversized and structured responses never reveal payload contents.

    Args:
        value: Native response value under examination.
        expected: Independently specified redacted shape.
    """
    assert hook_capture.response_shape(value) == expected


def test_metadata_retains_recognized_decisions_and_omits_invalid_native_fields():
    """Metadata preserves typed decisions without coercing oversized or numeric IDs."""
    # Mix valid decisions with native identifiers that must be omitted.
    event = {"session_id": "x" * 513, "agent_id": 1, "turn_id": "turn"}
    result = {
        "hookSpecificOutput": {"permissionDecision": "deny"},
        "decision": "block",
        "continue": False,
    }
    # Select metadata, then verify typed decisions and identifier filtering.
    observation = hook_capture.metadata(event, result, 2)
    assert observation["native"] == {"turn_id": "turn"}
    assert observation["adapter_decisions"] == {
        "permissionDecision": "deny",
        "decision": "block",
        "continue": False,
    }
    assert observation["adapter_exit_code"] == 2


@pytest.mark.parametrize("mode", ["unsupported-os", "oversize"])
def test_capture_skips_unavailable_or_oversize_writer(monkeypatch, mode):
    """Unsupported platforms and oversized metadata cannot spawn a diagnostic worker.

    Args:
        monkeypatch: Fixture restoring process boundaries.
        mode: Reason the writer must remain disabled.
    """
    from unittest.mock import Mock

    # Model an enabled destination with an unavailable platform or oversized payload.
    writer = Mock()
    monkeypatch.setattr(
        hook_capture,
        "os",
        SimpleNamespace(
            name="nt" if mode == "unsupported-os" else "posix",
            environ={hook_capture.ENVIRONMENT: "/capture"},
        ),
    )
    monkeypatch.setattr(hook_capture.subprocess, "Popen", writer)
    monkeypatch.setattr(
        hook_capture, "metadata", lambda *_args: {"large": "x" * hook_capture.BYTE_LIMIT}
    )
    # Confirm neither disabled boundary can start a worker.
    hook_capture.capture({}, {"decision": "deny"}, 2)
    writer.assert_not_called()


def test_module_entrypoint_returns_rejection_exit_status(monkeypatch):
    """The disposable writer entrypoint reports an invalid invocation without I/O.

    Args:
        monkeypatch: Fixture restoring process arguments and standard input.
    """
    import runpy
    from unittest.mock import Mock

    # Supply an invalid entrypoint invocation and observe any attempted input read.
    source = Mock()
    monkeypatch.setattr(hook_capture.sys, "argv", ["capture"])
    monkeypatch.setattr(hook_capture.sys, "stdin", SimpleNamespace(buffer=source))
    # Execute the entrypoint and verify its documented rejection status.
    with pytest.raises(SystemExit) as captured:
        runpy.run_path(hook_capture.__file__, run_name="__main__")
    assert captured.value.code == 2
    source.read.assert_not_called()


@pytest.mark.parametrize("fault", ["directory-open", "stat"])
def test_writer_directory_failures_close_only_acquired_descriptors(writer_boundary, fault):
    """Directory errors must not close an unowned descriptor or allocate a record.

    Args:
        writer_boundary: Modeled descriptor and stream boundary.
        fault: Directory acquisition or inspection failure.
    """
    # Preserve the exact failure identity across directory cleanup.
    injected = OSError("injected directory failure")
    # Failure before acquisition creates no ownership; inspection failure retains cleanup.
    if fault == "directory-open":
        writer_boundary.open.side_effect = injected
    else:
        writer_boundary.fstat.side_effect = injected
    # Exercise the selected boundary and require its original failure.
    with pytest.raises(OSError) as captured:
        hook_capture.write_record(Path.cwd(), b"redacted")
    assert captured.value is injected
    # Only a successfully acquired directory descriptor may be closed.
    writer_boundary.fdopen.assert_not_called()
    if fault == "directory-open":
        writer_boundary.close.assert_not_called()
    else:
        writer_boundary.close.assert_called_once_with(10)


def test_capture_success_preserves_original_result_and_uses_bounded_process(monkeypatch):
    """A successful observer gets redacted bytes without changing the completed decision.

    Args:
        monkeypatch: Fixture restoring process creation and private activation.
    """
    from unittest.mock import MagicMock, Mock

    # Model the writer independently from the already-completed lifecycle operation.
    writer = MagicMock()
    writer.__enter__.return_value = writer
    spawn = Mock(return_value=writer)
    monkeypatch.setattr(
        hook_capture,
        "os",
        SimpleNamespace(name="posix", environ={hook_capture.ENVIRONMENT: "/capture"}),
    )
    monkeypatch.setattr(hook_capture.subprocess, "Popen", spawn)
    # Capture after the authoritative decision is already fixed.
    result = {"decision": "block"}
    hook_capture.capture({"tool_response": {"stdout": "private body"}}, result, 2)
    # Verify the decision is unchanged and the worker receives only bounded redacted data.
    assert result == {"decision": "block"}
    data = writer.communicate.call_args.args[0]
    assert b"private body" not in data
    assert json.loads(data)["adapter_exit_code"] == 2
    assert writer.communicate.call_args.kwargs == {"timeout": hook_capture.TIME_LIMIT}
    assert spawn.call_args.args[0][-2:] == ["--write", "/capture"]
    writer.kill.assert_not_called()
