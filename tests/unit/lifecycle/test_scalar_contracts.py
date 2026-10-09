"""Exercise lifecycle scalar contracts without a repository or host boundary."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from agent_company.lifecycle import task_workspace as core
from agent_company.lifecycle._errors import WorkspaceError, require
from agent_company.lifecycle._paths import entry_name

pytestmark = pytest.mark.unit


def test_workspace_error_retains_bounded_code_and_action() -> None:
    """Workspace error retains bounded code and action."""
    # Construct an error with an explicit public code and action.
    error = WorkspaceError("DENIED", "Retry from the assigned workspace")
    # Confirm the rejected operation reports DENIED.
    assert error.code == "DENIED"
    assert error.action == "Retry from the assigned workspace"
    assert str(error) == "DENIED"


def test_require_accepts_truthy_value_and_rejects_false_with_explicit_action() -> None:
    """Require accepts truthy value and rejects false with explicit action."""
    # Accept truthy input before probing the false boundary.
    assert require(1) is None
    # Reject false input with the requested action.
    with pytest.raises(WorkspaceError) as captured:
        require(False, "CONFLICT", "Use the current revision")
    # Confirm the rejected operation reports CONFLICT.
    assert (captured.value.code, captured.value.action) == (
        "CONFLICT",
        "Use the current revision",
    )


@pytest.mark.parametrize("name", ["a", "readme.md", "x_y-z", "COM0", "file…md"])
def test_entry_name_accepts_portable_ordinary_names(name: str) -> None:
    """Entry name accepts portable ordinary names.

    Args:
        name: Requested file, directory, or control-entry name.
    """
    assert entry_name(name) is None


def test_canonical_rejects_nonfinite_numbers() -> None:
    """Canonical rejects nonfinite numbers."""
    # Reject nonfinite numbers before canonical JSON serialization.
    with pytest.raises(ValueError, match="Out of range float values"):
        core.canonical({"rate": float("nan")})


def test_sha_identifies_exact_bytes() -> None:
    """Sha identifies exact bytes."""
    # Confirm the digest changes with exact input bytes.
    assert core.sha(b"") == hashlib.sha256(b"").hexdigest()
    assert core.sha(b"a") != core.sha(b"A")
    assert len(core.sha(b"binary\x00")) == 64


def test_now_returns_parseable_utc_timestamp() -> None:
    """Now returns parseable utc timestamp."""
    # Capture the clock value for UTC parsing.
    instant = datetime.fromisoformat(core.now())
    assert instant.tzinfo is not None
    assert instant.utcoffset() == timezone.utc.utcoffset(instant)


def test_fault_invokes_only_installed_local_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fault invokes only installed local hook.

    Args:
        monkeypatch: Pytest fixture used to replace the dependency under test.
    """
    # Install fake FAILPOINT calls.
    seen: list[str] = []
    monkeypatch.setattr(core, "FAILPOINT", None)
    # Confirm an absent hook produces no side effect.
    assert core.fault("after-intent") is None
    # Install fake FAILPOINT calls.
    monkeypatch.setattr(core, "FAILPOINT", seen.append)
    # Invoke only the locally installed fault hook.
    core.fault("after-intent")
    assert seen == ["after-intent"]


@pytest.mark.parametrize("value", ["a", "A" * 160, "ticket:30"])
def test_token_accepts_printable_bounded_identity(value: str) -> None:
    """Token accepts printable bounded identity.

    Args:
        value: Document value stored by the fake control boundary.
    """
    assert core.token(value) == value


@pytest.mark.parametrize("value", [None, "", "A" * 161, "x y", "x\n", "é"])
def test_token_rejects_missing_or_unsafe_identity(value: object) -> None:
    """Token rejects missing or unsafe identity.

    Args:
        value: Document value stored by the fake control boundary.
    """
    # Reject nonprintable or overlong identity tokens.
    with pytest.raises(WorkspaceError) as captured:
        core.token(value)  # type: ignore[arg-type]
    # Confirm the rejected operation reports INVALID_REQUEST.
    assert captured.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("value", ["A-1", "AGENT30-123", "A" * 16 + "-1234567890"])
def test_issue_id_accepts_grammar_boundaries(value: str) -> None:
    """Issue id accepts grammar boundaries.

    Args:
        value: Document value stored by the fake control boundary.
    """
    assert core.issue_id(value) == value


@pytest.mark.parametrize("value", ["a-1", "A-0", "A-01", "A-12345678901", "A_1", None])
def test_issue_id_rejects_invalid_identity(value: object) -> None:
    """Issue id rejects invalid identity.

    Args:
        value: Document value stored by the fake control boundary.
    """
    # Reject IDs outside the canonical issue grammar.
    with pytest.raises(WorkspaceError) as captured:
        core.issue_id(value)  # type: ignore[arg-type]
    # Confirm the rejected operation reports INVALID_ISSUE.
    assert captured.value.code == "INVALID_ISSUE"


@pytest.mark.parametrize("path", ["roadmap.md", "context/a.md", "context/a.b-c_d.md"])
def test_payload_path_accepts_authorized_notes(path: str) -> None:
    """Payload path accepts authorized notes.

    Args:
        path: Filesystem path passed to the operation.
    """
    assert core.payload_path(path) == path


@pytest.mark.parametrize(
    "path", ["context/A.md", "context/a.txt", "context/a..md", "context/a.md/x"]
)
def test_payload_path_rejects_noncanonical_notes(path: str) -> None:
    """Payload path rejects noncanonical notes.

    Args:
        path: Filesystem path passed to the operation.
    """
    # Reject paths outside the assigned payload grammar.
    with pytest.raises(WorkspaceError) as captured:
        core.payload_path(path)
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"


def test_event_segment_requires_explicit_authority() -> None:
    """Event segment requires explicit authority."""
    # Construct a valid event segment name before probing aliases.
    segment = "events-000000000001-000000000002.jsonl"
    # Reject paths outside the assigned payload grammar.
    with pytest.raises(WorkspaceError) as captured:
        core.payload_path(segment)
    # Confirm the rejected operation reports UNSAFE_PATH.
    assert captured.value.code == "UNSAFE_PATH"
    assert core.payload_path(segment, events=True) == segment


def test_base64_roundtrip_retains_binary_and_empty_payload() -> None:
    """Base64 roundtrip retains binary and empty payload."""
    # Confirm exact binary bytes survive base64 encoding and decoding.
    assert core.encode(b"") == ""
    assert core.encode(b"\x00\xff") == "AP8="
    assert core.decode("AP8=", limit=2) == b"\x00\xff"


@pytest.mark.parametrize("value", ["***", "YWJj!", "a", "YWI=\n"])
def test_decode_rejects_invalid_base64(value: str) -> None:
    """Decode rejects invalid base64.

    Args:
        value: Document value stored by the fake control boundary.
    """
    # Reject malformed or oversized base64 before exposing bytes.
    with pytest.raises(WorkspaceError) as captured:
        core.decode(value, limit=100)
    # Confirm the rejected operation reports INTEGRITY_ERROR.
    assert captured.value.code == "INTEGRITY_ERROR"


def test_decode_rejects_encoded_length_before_decoding() -> None:
    """Decode rejects encoded length before decoding."""
    # Reject malformed or oversized base64 before exposing bytes.
    with pytest.raises(WorkspaceError) as captured:
        core.decode("AAAAAA", limit=2)
    # Confirm the rejected operation reports SIZE_LIMIT.
    assert captured.value.code == "SIZE_LIMIT"
