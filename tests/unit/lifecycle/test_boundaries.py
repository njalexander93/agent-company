"""Validate pure serialization and integrity boundaries without Git or filesystem setup."""

import pytest

from agent_company.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "payload",
    [
        '{"owner":"first","owner":"second"}',
        '{"nested":{"generation":1,"generation":2}}',
        '{"value":NaN}',
        '{"value":Infinity}',
        b'"\xff"',
    ],
)
def test_strict_json_rejects_ambiguous_or_nonfinite_input(payload: bytes | str) -> None:
    """Reject ambiguity before parsed values can affect lifecycle identity or authority.

    Args:
        payload: Invalid JSON representation requiring a bounded request error.

    Raises:
        AssertionError: Parsing succeeds or reports the wrong error code.
    """
    # Parse untrusted bytes or text and require the same bounded request error.
    with pytest.raises(core.WorkspaceError) as error:
        core.strict_json(payload)
    assert error.value.code == "INVALID_REQUEST"


def test_canonical_json_preserves_unicode_and_ignores_mapping_order() -> None:
    """Keep content identity stable while preserving exact Unicode values.

    Raises:
        AssertionError: Serialization depends on insertion order or loses Unicode content.
    """
    # Encode equivalent mappings constructed in opposite insertion order.
    first = core.canonical({"z": [1, True, None], "a": "é"})
    second = core.canonical({"a": "é", "z": [1, True, None]})
    # Require exact UTF-8 bytes and a lossless strict parse.
    assert first == second == '{"a":"é","z":[1,true,null]}'.encode()
    assert core.strict_json(first) == {"a": "é", "z": [1, True, None]}


@pytest.mark.parametrize("path", ["../roadmap.md", "/roadmap.md", "context/../secret.md"])
def test_payload_paths_reject_escape_spellings(path: str) -> None:
    """Reject path escapes independently of the caller's filesystem layout.

    Args:
        path: Absolute or traversal-containing payload locator.

    Raises:
        AssertionError: An escape spelling is accepted or returns the wrong error.
    """
    # Validate the path without opening any file or directory.
    with pytest.raises(core.WorkspaceError) as error:
        core.payload_path(path)
    assert error.value.code == "UNSAFE_PATH"


def test_event_paths_require_explicit_event_access() -> None:
    """Keep event files outside ordinary note-write authority.

    Raises:
        AssertionError: Ordinary note access admits events or explicit event access rejects them.
    """
    # Refuse the event stream when the caller has only ordinary payload authority.
    with pytest.raises(core.WorkspaceError) as error:
        core.payload_path("events.jsonl")
    assert error.value.code == "UNSAFE_PATH"
    # Admit the same exact path only when event access is explicit.
    assert core.payload_path("events.jsonl", events=True) == "events.jsonl"


def test_decode_enforces_decoded_size_at_base64_padding_boundary() -> None:
    """Enforce decoded length when encoded lengths alone cannot distinguish the limit.

    Raises:
        AssertionError: Exact-budget bytes fail or an extra decoded byte is admitted.
    """
    # Admit a two-byte payload at its exact budget.
    assert core.decode("YWI=", limit=2) == b"ab"
    # Reject three decoded bytes even though both representations occupy four characters.
    with pytest.raises(core.WorkspaceError) as error:
        core.decode("YWJj", limit=2)
    assert error.value.code == "SIZE_LIMIT"


@pytest.mark.parametrize("mutation", ["missing_newline", "changed_content", "wrong_anchor"])
def test_event_integrity_rejects_truncation_tampering_and_wrong_anchor(mutation: str) -> None:
    """Validate complete event bytes and their preceding chain identity.

    Args:
        mutation: Integrity boundary deliberately violated after validating the original event.

    Raises:
        AssertionError: Valid anchored history fails or a modified history is accepted.
    """
    # Construct an anchored event and prove its intact sequence can be read independently.
    prior = "a" * 64
    event = {"seq": 8, "prev_digest": prior, "code": "OK"}
    digest = core.sha(core.canonical(event))
    data = core.canonical({**event, "digest": digest}) + b"\n"
    assert core.validate_events(data, seq=7, prior=prior) == (8, digest)
    # Violate exactly one framing, content or chain-anchor obligation.
    if mutation == "missing_newline":
        data = data[:-1]
    # Preserve framing while altering content without updating its digest.
    elif mutation == "changed_content":
        data = data.replace(b'"OK"', b'"NO"')
    # Preserve event bytes while substituting the caller's expected predecessor.
    else:
        prior = "b" * 64
    # Require a bounded integrity failure for every altered history.
    with pytest.raises(core.WorkspaceError) as error:
        core.validate_events(data, seq=7, prior=prior)
    assert error.value.code == "INTEGRITY_ERROR"
