"""Carry a canonical lifecycle request through native Windows argument quoting."""

from __future__ import annotations

import base64
import binascii
import sys

from agent_company.lifecycle import task_workspace as core


def decode_request(encoded: str) -> core.JSONObject:
    """Decode one bounded canonical base64 JSON object without shell interpretation.

    Args:
        encoded: ASCII URL-safe base64 with its canonical padding.

    Returns:
        The strict lifecycle JSON object.

    Raises:
        core.WorkspaceError: If encoding, size, or request shape is invalid.
    """
    core.require(isinstance(encoded, str) and len(encoded) <= 65536, "SIZE_LIMIT")
    try:
        raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        raise core.WorkspaceError("INVALID_REQUEST") from error
    core.require(base64.urlsafe_b64encode(raw).decode("ascii") == encoded, "INVALID_REQUEST")
    core.require(len(raw) <= core.MAX_REQUEST, "SIZE_LIMIT")
    request = core.strict_json(raw)
    if not isinstance(request, dict):
        raise core.WorkspaceError("INVALID_REQUEST")
    return request


def main() -> int:
    """Execute the unchanged lifecycle API using one encoded JSON argument.

    Returns:
        Zero for lifecycle success, three for a reported request failure.
    """
    try:
        core.require(len(sys.argv) == 3 and sys.argv[1] == "--request-base64", "INVALID_REQUEST")
        result = core.execute(decode_request(sys.argv[2]))
    except core.WorkspaceError as error:
        result = {"ok": False, "code": error.code}
    print(core.canonical(result).decode())
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
