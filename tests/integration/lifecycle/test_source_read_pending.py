"""Verify pre-readiness source reads share authoritative lifecycle pending accounting."""

import uuid

import pytest

from nplet.lifecycle import task_workspace as core
from tests.support import Fixture

pytestmark = pytest.mark.integration


class SourceReadTests(Fixture):
    """Check exact assigned preparation without weakening ordinary tool readiness."""

    def test_source_preparation_is_scoped_idempotent_and_blocks_retirement(self) -> None:
        """Admit only current available source preparation and retain unresolved work.

        Raises:
            AssertionError: A forbidden request mutates state or retirement loses work.
        """
        # Assign a real roadmap reference, then invalidate readiness through explicit scope.
        self.create()
        self.ready()
        key = self.base["coordinator"]
        refs = [
            {
                "id": "roadmap",
                "locator": "roadmap.md",
                "sha256": core.sha((self.root / ".task/TEST-1/roadmap.md").read_bytes()),
                "required": True,
                "authority": "fixture",
                "stage": "planning",
                "reader": key,
                "reason": "preparation",
            }
        ]
        self.call(
            "scope", expected_revision=self.state()["revision"], target_participant=key, packet=refs
        )
        packet = self.call("read")
        request = self.req(
            "source-read-start",
            tool_id="source",
            source_locator=refs[0]["locator"],
            packet_digest=packet["packet_digest"],
        )
        before = self.state()
        # Unready ordinary tools, arbitrary sources and stale packets have no side effects.
        for bad, code in (
            ({**request, "operation": "tool-start"}, "NOT_READY"),
            ({**request, "source_locator": "/outside/unassigned"}, "SCOPE_MISSING"),
            ({**request, "packet_digest": "0" * 64}, "SOURCE_STALE"),
            ({**request, "poll_handle": "arbitrary"}, "SCOPE_MISSING"),
        ):
            assert core.execute(bad)["code"] == code
            assert self.state() == before
        # Exact retries neither duplicate pending work nor advance the committed revision.
        first = self.require_ok(core.execute(request))
        admitted = self.state()
        assert core.execute(request) == first
        assert self.state() == admitted
        assert admitted["participants"][key]["ack"] is None
        assert admitted["participants"][key]["pending"] == {"source": {"status": "pending"}}
        assert (
            core.execute({**request, "request_id": str(uuid.uuid4())})["code"] == "REQUEST_CONFLICT"
        )
        assert core.execute({**request, "source_locator": "foreign"})["code"] == "REQUEST_CONFLICT"
        assert (
            core.execute(self.req("detach", evidence=self.evidence()))["code"]
            == "PENDING_OPERATION"
        )
        assert self.state() == admitted
        # An observed completion frees pending activity without acknowledging source bytes.
        self.call("tool-complete", tool_id="source", completed=True)
        assert self.state()["participants"][key]["pending"] == {}
        assert self.state()["participants"][key]["ack"] is None
        self.call("detach", evidence=self.evidence())

    def test_unavailable_or_unassigned_sources_never_reserve_pending_work(self) -> None:
        """Reject absent packets and optional unavailable references before tool reservation.

        Raises:
            AssertionError: Failed source preparation creates pending work or grants readiness.
        """
        # A newly attached participant has no implicit source-read capability.
        self.create()
        request = self.req(
            "source-read-start",
            tool_id="missing",
            source_locator="roadmap.md",
            packet_digest=core.sha(core.canonical([])),
        )
        before = self.state()
        assert core.execute(request)["code"] == "SCOPE_MISSING"
        assert self.state() == before
        # An optional missing source remains unavailable even with the correct packet digest.
        key = self.base["coordinator"]
        refs = [
            {
                "id": "missing",
                "locator": str(self.root / "missing.md"),
                "sha256": "a" * 64,
                "required": False,
                "authority": "fixture",
                "reason": "test",
                "stage": "planning",
                "reader": key,
            }
        ]
        self.call(
            "scope", expected_revision=before["revision"], target_participant=key, packet=refs
        )
        packet = self.call("read")
        request.update(source_locator=refs[0]["locator"], packet_digest=packet["packet_digest"])
        before = self.state()
        assert core.execute(request)["code"] == "SCOPE_MISSING"
        assert self.state() == before
        assert before["participants"][key]["pending"] == {}
