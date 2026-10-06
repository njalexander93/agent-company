"""Independent contract probes. All payloads live in disposable Git repositories.

Provider observations are fixtures: these tests make no provider/desktop acceptance claim.
"""

import copy
from pathlib import Path
from unittest import mock

import pytest

from agent_company.lifecycle import task_workspace as w
from tests.support import Fixture
from tests.types import JsonObject

pytestmark = pytest.mark.integration


class IndependentContractTests(Fixture):
    """Probe lifecycle boundaries independently using disposable stores and fake provider reads."""

    def update_request(self) -> JsonObject:
        """Build a roadmap replacement against the current committed revision.

        Returns:
            An update request with the current roadmap digest and synthetic provenance.
        """
        # Read the committed revision and digest to fence the replacement write.
        state = self.state()
        return self.req(
            "update",
            path="roadmap.md",
            content="# committed replacement\n",
            expected_revision=state["revision"],
            old_digest=state["files"]["roadmap.md"],
            provenance={"sources": self.evidence(), "status": "draft", "applicability": "test"},
        )

    def test_T02_foreign_clone_registration_preserves_canonical_payload(self) -> None:
        """Reject copied registration from a foreign clone without changing task data.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Copy registration into a separate clone to simulate a foreign caller.
        self.create()
        foreign = Path(self.temp.name).resolve() / "foreign"
        self.git("clone", "-q", str(self.root), str(foreign))
        (foreign / ".task").mkdir(mode=0o700)
        (foreign / ".task/.repository.json").write_bytes(
            (self.root / ".task/.repository.json").read_bytes()
        )
        # Snapshot the canonical roadmap before attempting foreign resume.
        before = (self.root / ".task/TEST-1/roadmap.md").read_bytes()
        # Require repository mismatch and preservation of the canonical bytes.
        result = w.execute(self.req("resume", worktree=str(foreign)))
        self.assertEqual(result["code"], "REPOSITORY_MISMATCH", result)
        self.assertEqual((self.root / ".task/TEST-1/roadmap.md").read_bytes(), before)

    def test_T03_detach_resume_fences_old_generation_and_preserves_progress(self) -> None:
        """Fence stale writes after resume while retaining committed progress.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Commit distinctive progress and capture the current generation.
        self.create()
        self.require_ok(w.execute(self.update_request()))
        before = (self.root / ".task/TEST-1/roadmap.md").read_bytes()
        old_generation = self.base["binding_generation"]
        # Retire and resume the participant to advance its generation.
        self.call("detach", evidence=self.evidence())
        resumed = self.call("resume")
        # Reject the old generation while verifying the roadmap remains unchanged.
        self.assertGreater(resumed["binding_generation"], old_generation)
        self.assertFalse(w.execute(self.update_request())["ok"])
        self.assertEqual((self.root / ".task/TEST-1/roadmap.md").read_bytes(), before)

    def test_T04_participant_cannot_update_coordinator_roadmap(self) -> None:
        """Prevent an assigned reviewer from writing the coordinator roadmap.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Assign a separate reviewer an empty permitted packet.
        self.create()
        member = self.req("attach", session_id="reviewer", binding_generation=None)
        key = w.participant_key(member)
        self.call(
            "scope", expected_revision=self.state()["revision"], target_participant=key, packet=[]
        )
        # Attach that reviewer and construct a roadmap update under its identity.
        generation = self.require_ok(w.execute(member))["binding_generation"]
        request = self.update_request()
        request.update(session_id="reviewer", binding_generation=generation)
        # Require an ownership denial rather than implicit coordinator rights.
        self.assertEqual(w.execute(request)["code"], "NOT_OWNER")

    def test_T05_recovery_preserves_foreign_edit_when_roadmap_disappears(self) -> None:
        """Stop interrupted recovery before overwriting externally changed event bytes.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Prepare an update that will stop after its intent is persisted.
        self.create()
        request = self.update_request()

        def crash(point: str) -> None:
            """Interrupt the update after its intent is persisted.

            Args:
                point: Persistence seam reported by the core.

            Raises:
                OSError: The configured seam is reached; other seams return normally.
            """
            # Raise only at the selected seam; let other persistence steps continue.
            if point == "intent":
                raise OSError("injected crash before publication")

        # Inject the failure while keeping the patch scoped to this attempt.
        with mock.patch.object(w, "FAILPOINT", crash):
            self.assertFalse(w.execute(request)["ok"])
        # Remove the roadmap and replace events with an untracked sentinel.
        payload = self.root / ".task/TEST-1"
        (payload / "roadmap.md").unlink()
        sentinel = b"FOREIGN-EDIT-MUST-SURVIVE\n"
        (payload / "events.jsonl").write_bytes(sentinel)
        # Retry recovery and require a stop that preserves the foreign bytes.
        result = w.execute(request)
        self.assertFalse(result["ok"], "Recovery must stop before overwriting changed bytes")
        self.assertEqual((payload / "events.jsonl").read_bytes(), sentinel)

    def test_T06_optional_observation_cap_is_suppressed_and_counted(self) -> None:
        """Count optional event loss without changing a full event stream.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Capture the initial stream and use its length as the temporary cap.
        self.create()
        before = (self.root / ".task/TEST-1/events.jsonl").read_bytes()
        # Submit an optional observation with no space available for publication.
        with mock.patch.object(w, "MAX_EVENTS", len(before)):
            result = w.execute(self.req("event", event_type="observation", event={"code": "OK"}))
        # Verify successful suppression, unchanged bytes and recorded loss.
        self.assertTrue(
            result["ok"],
            "Contract requires optional loss accounting instead of required-write refusal",
        )
        self.assertEqual((self.root / ".task/TEST-1/events.jsonl").read_bytes(), before)
        self.assertGreater(self.state().get("optional_loss_count", 0), 0)

    def test_T07_scope_change_invalidates_acknowledgement(self) -> None:
        """Require acknowledgment again after replacing an otherwise identical packet.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Establish a ready participant with an acknowledged empty packet.
        self.create()
        self.ready()
        self.call("ready")
        # Replace the packet and verify the old acknowledgment no longer grants readiness.
        self.call(
            "scope",
            expected_revision=self.state()["revision"],
            target_participant=self.base["coordinator"],
            packet=[],
        )
        self.assertEqual(w.execute(self.req("ready"))["code"], "NOT_READY")

    def test_T09_changed_local_bytes_after_plan_are_retained(self) -> None:
        """Refuse cleanup when local bytes change after the cleanup challenge.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Prepare an eligible archive and bind a fresh cleanup request.
        self.create()
        observations = self.archive()
        cleanup = self.cleanup_request(observations)
        # Introduce an out-of-band roadmap change after planning cleanup.
        path = self.root / ".task/TEST-1/roadmap.md"
        path.write_text("UNTRACKED-CHANGE-MUST-SURVIVE\n")
        # Require an untracked-change refusal that preserves the changed bytes.
        self.assertEqual(w.execute(cleanup)["code"], "UNTRACKED_CHANGE")
        self.assertEqual(path.read_text(), "UNTRACKED-CHANGE-MUST-SURVIVE\n")

    def test_T10_partial_cleanup_recovery_preserves_other_issue(self) -> None:
        """Finish interrupted deletion without touching another issue.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Prepare eligible cleanup and a sentinel in another issue directory.
        self.create()
        observations = self.archive()
        cleanup = self.cleanup_request(observations)
        sentinel = self.root / ".task/OTHER-7"
        sentinel.mkdir()
        (sentinel / "keep").write_text("outside cleanup target")

        def crash(point: str) -> None:
            """Interrupt cleanup after event deletion.

            Args:
                point: Persistence seam reported by the core.

            Raises:
                OSError: The configured seam is reached; other seams return normally.
            """
            # Raise only at the selected seam; let other persistence steps continue.
            if point == "delete:events.jsonl":
                raise OSError("injected partial deletion")

        # Interrupt deletion after the selected payload file is removed.
        with mock.patch.object(w, "FAILPOINT", crash):
            self.assertFalse(w.execute(cleanup)["ok"])
        # Retry the recorded cleanup intent and verify narrow deletion.
        self.require_ok(w.execute(cleanup))
        self.assertEqual(self.state()["storage"], "cleaned")
        self.assertEqual((sentinel / "keep").read_text(), "outside cleanup target")

    def test_T11_corrupt_or_unavailable_archive_never_recreates_empty_issue(self) -> None:
        """Keep a cleaned workspace absent when restore evidence is incomplete or corrupt.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Clean the issue only after its synthetic archive is verified.
        self.create()
        observations = self.archive()
        self.require_ok(w.execute(self.cleanup_request(observations)))
        # Prepare malformed archive content from a copy of the retained observations.
        corrupt = copy.deepcopy(observations)
        corrupt[0]["content"] += "\nCORRUPTED"
        # Change the encoded archive bytes, not merely its human-readable heading.
        corrupt[0]["content"] = corrupt[0]["content"].replace("eyJ", "eyK", 1)
        # Try absent, incomplete and corrupt evidence without allowing empty restoration.
        for source in ([], observations[1:], corrupt):
            # Require failure while retaining the cleaned storage state.
            result = w.execute(self.req("restore", observations=source))
            self.assertFalse(result["ok"], result)
            self.assertFalse((self.root / ".task/TEST-1").exists())
            self.assertEqual(self.state()["storage"], "cleaned")
