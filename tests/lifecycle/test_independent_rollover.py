"""Independent rollover probes using temporary repositories and provider fixtures."""

import json
from unittest import mock

import pytest
from agent_company.lifecycle import task_workspace as w

from tests.support import Fixture
from tests.types import JsonObject

pytestmark = pytest.mark.integration


class IndependentRolloverTests(Fixture):
    """Probe rollover retention and capacity with synthetic provider observations."""

    def checkpoint_archive(self) -> list[JsonObject]:
        """Verify a nonterminal snapshot with synthetic provider read-back.

        Returns:
            Fixture observations for the exported parts and index.

        Raises:
            AssertionError: Archive preparation, indexing or verification fails.
        """
        # Prepare a live snapshot and synthesize read-back for its parts.
        export = self.call("archive-prepare", expected_revision=self.state()["revision"])
        observations = self.observations(export)
        # Build the index and add its synthetic provider observation.
        index = self.call("archive-index", observations=observations)
        observations.append(
            {
                "id": "index",
                "url": "https://linear.app/test/document/index",
                "issue": self.base["issue_uuid"],
                "updatedAt": "2026-10-03T00:00:00Z",
                "origin": "linear_get_document",
                "request_id": "fixture-get",
                "content": index["content"],
            }
        )
        # Verify the full snapshot before returning the observations.
        self.call("archive-verify", observations=observations)
        return observations

    def rollover(self) -> JsonObject:
        """Rotate events against the current committed revision.

        Returns:
            The successful rollover response.

        Raises:
            AssertionError: Rollover prerequisites are not satisfied.
        """
        # Dispatch rollover with the latest revision and require success.
        return self.call("event-rollover", expected_revision=self.state()["revision"])

    def assert_history(self) -> None:
        """Validate every segment and active event against the committed chain head.

        Raises:
            AssertionError: Newlines, sequence, digest links or committed head disagree.
            OSError: A retained event file cannot be read.
            json.JSONDecodeError: An event line is malformed.
        """
        # Order retained segments before the active stream and initialize chain state.
        payload = self.root / ".task/TEST-1"
        paths = sorted(payload.glob("events-*.jsonl")) + [payload / "events.jsonl"]
        sequence, head = 0, None
        # Read each stream in order and require complete newline-terminated events.
        for path in paths:
            data = path.read_bytes()
            self.assertTrue(data.endswith(b"\n"))
            # Validate each sequence number and digest link before advancing the head.
            for line in data.splitlines():
                event = json.loads(line)
                digest = event.pop("digest")
                sequence += 1
                self.assertEqual(event["seq"], sequence)
                self.assertEqual(event["prev_digest"], head)
                self.assertEqual(w.sha(w.canonical(event)), digest)
                head = digest
        # Compare the reconstructed chain with the committed state.
        self.assertEqual((sequence, head), (self.state()["seq"], self.state()["head"]))

    def test_rollover_requires_current_verified_archive_and_revision(self) -> None:
        """Require a current archive and revision before rotating the event stream.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Try rollover before any archive has been verified.
        self.create()
        before = self.state()
        self.assertEqual(
            w.execute(self.req("event-rollover", expected_revision=before["revision"]))["code"],
            "ARCHIVE_PENDING",
        )
        # Verify an archive, then reject the old revision used before that checkpoint.
        self.checkpoint_archive()
        self.assertEqual(
            w.execute(self.req("event-rollover", expected_revision=before["revision"]))["code"],
            "REVISION_CONFLICT",
        )
        # Invalidate the archive with a new event and ensure no segment is published.
        self.call("event", event_type="check", event={"code": "OK"})
        self.assertEqual(
            w.execute(self.req("event-rollover", expected_revision=self.state()["revision"]))[
                "code"
            ],
            "ARCHIVE_PENDING",
        )
        self.assertEqual(list((self.root / ".task/TEST-1").glob("events-*.jsonl")), [])

    def test_non_coordinator_cannot_rollover(self) -> None:
        """Reject event rollover by a scoped participant who is not coordinator.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Assign and attach a reviewer without coordinator ownership.
        self.create()
        member = self.req("attach", session_id="reviewer", binding_generation=None)
        key = w.participant_key(member)
        self.call(
            "scope", expected_revision=self.state()["revision"], target_participant=key, packet=[]
        )
        generation = self.require_ok(w.execute(member))["binding_generation"]
        # Verify the archive so ownership is the boundary under test.
        self.checkpoint_archive()
        # Attempt rollover as the reviewer and require an ownership denial.
        denied = w.execute(
            self.req(
                "event-rollover",
                session_id="reviewer",
                binding_generation=generation,
                expected_revision=self.state()["revision"],
            )
        )
        self.assertEqual(denied["code"], "NOT_OWNER")

    def test_crash_retry_all_publication_boundaries_and_global_chain(self) -> None:
        """Recover rollover failures without losing segments or duplicating events.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Create the issue whose event history will span every failure seam.
        self.create()
        # Select each persistence boundary, including segment publication.
        for point in (
            "before-intent",
            "intent",
            "payload:events.jsonl",
            "payload:segment",
            "payload-flush",
            "state",
            "transaction-complete",
        ):
            # Snapshot a verified stream and its prior sequence for this seam.
            with self.subTest(point=point):
                self.checkpoint_archive()
                payload = self.root / ".task/TEST-1"
                old = (payload / "events.jsonl").read_bytes()
                prior_sequence = self.state()["seq"]
                request = self.req("event-rollover", expected_revision=self.state()["revision"])

                def crash(actual: str) -> None:
                    """Interrupt the selected rollover persistence boundary.

                    Args:
                        actual: Persistence seam reported by the core.

                    Raises:
                        OSError: The configured seam is reached; other seams return normally.
                    """
                    # Raise only at the selected seam; let other persistence steps continue.
                    if actual == point or (
                        point == "payload:segment" and actual.startswith("payload:events-")
                    ):
                        raise OSError("injected rollover crash")

                # Interrupt rollover at the selected boundary.
                with mock.patch.object(w, "FAILPOINT", crash):
                    self.assertFalse(w.execute(request)["ok"])
                # Retry identically and verify segment preservation and one new event.
                result = self.require_ok(w.execute(request))
                self.assertEqual(w.execute(request), result)
                self.assertEqual((payload / result["segment"]).read_bytes(), old)
                self.assertEqual(self.state()["seq"], prior_sequence + 1)
                self.assert_history()

    def test_rollover_history_survives_cleanup_and_restore(self) -> None:
        """Restore every retained event segment and its global digest chain.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Initialize the disposable issue and retained-segment comparison set.
        self.create()
        retained = {}
        # Produce two independently archived rollover segments.
        for _ in range(2):
            self.checkpoint_archive()
            result = self.rollover()
            retained[result["segment"]] = (
                self.root / ".task/TEST-1" / result["segment"]
            ).read_bytes()
        # Archive the terminal history, clean it and restore it from fixture observations.
        observations = self.archive()
        self.require_ok(w.execute(self.cleanup_request(observations)))
        self.assertFalse((self.root / ".task/TEST-1").exists())
        restored = self.call("restore", observations=observations)
        self.assertGreater(restored["binding_generation"], self.base["binding_generation"])
        # Compare every restored segment with its exact pre-cleanup bytes.
        for path, data in retained.items():
            self.assertEqual((self.root / ".task/TEST-1" / path).read_bytes(), data)
        # Verify segment metadata and the complete global event chain.
        self.assertEqual(len(self.state()["event_segments"]), 2)
        self.assert_history()

    def test_archive_reserve_allows_checkpoint_and_rollover_when_required_event_full(self) -> None:
        """Keep checkpoint capacity available after ordinary required events are refused.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Capture the stream before constraining its remaining capacity.
        self.create()
        active = self.root / ".task/TEST-1/events.jsonl"
        before = active.read_bytes()
        # Reserve checkpoint space while refusing an ordinary required append.
        with mock.patch.object(w, "MAX_EVENTS", len(before) + 8192 + 512):
            self.assertEqual(
                w.execute(self.req("event", event_type="check", event={"code": "OK"}))["code"],
                "ARCHIVE_PENDING",
            )
            self.assertEqual(active.read_bytes(), before)
            # Use the reserved space to verify an archive and roll over.
            self.checkpoint_archive()
            self.rollover()
        # Validate the final chain after restoring the normal capacity limit.
        self.assert_history()

    def test_optional_observation_respects_checkpoint_reserve(self) -> None:
        """Suppress optional observations before consuming reserved checkpoint capacity.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Capture the stream and constrain capacity around the checkpoint reserve.
        self.create()
        active = self.root / ".task/TEST-1/events.jsonl"
        before = active.read_bytes()
        # Submit the optional observation under the temporary cap.
        with mock.patch.object(w, "MAX_EVENTS", len(before) + 8193):
            result = w.execute(self.req("event", event_type="observation", event={"code": "OK"}))
        # Require suppression, unchanged bytes and exactly one recorded loss.
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["code"], "OPTIONAL_SUPPRESSED")
        self.assertEqual(active.read_bytes(), before)
        self.assertEqual(self.state()["optional_loss_count"], 1)

    def test_scoped_read_excludes_unassigned_segments(self) -> None:
        """Disclose an archived segment only after explicit packet assignment.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Produce a retained event segment in a verified archive.
        self.create()
        self.checkpoint_archive()
        segment = self.rollover()["segment"]
        # Attach a reviewer whose empty packet excludes that segment.
        member = self.req("attach", session_id="reviewer", binding_generation=None)
        key = w.participant_key(member)
        self.call(
            "scope", expected_revision=self.state()["revision"], target_participant=key, packet=[]
        )
        generation = self.require_ok(w.execute(member))["binding_generation"]
        # Verify scoped reads disclose neither the segment nor other references.
        result = self.call("read", session_id="reviewer", binding_generation=generation)
        self.assertEqual(result["references"], [])
        self.assertNotIn(segment, json.dumps(result))
        # Assign the exact segment digest to that reviewer and check availability.
        packet = [
            {
                "id": "archived-events",
                "locator": segment,
                "sha256": w.sha((self.root / ".task/TEST-1" / segment).read_bytes()),
                "required": True,
                "authority": "diagnostic",
                "reason": "review",
                "stage": "review",
                "reader": key,
            }
        ]
        self.call(
            "scope",
            expected_revision=self.state()["revision"],
            target_participant=key,
            packet=packet,
        )
        allowed = self.call("read", session_id="reviewer", binding_generation=generation)
        self.assertEqual(len(allowed["references"]), 1)
        self.assertEqual(allowed["references"][0]["locator"], segment)
        self.assertTrue(allowed["references"][0]["available"])

    def test_near_cap_tool_admission_denies_without_leaving_pending_work(self) -> None:
        """Reject tools lacking completion capacity without leaving pending state.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Establish readiness and capture the original event bytes.
        self.create()
        self.ready()
        active = self.root / ".task/TEST-1/events.jsonl"
        before = active.read_bytes()
        # Constrain capacity and check admission refuses the tool atomically.
        with mock.patch.object(w, "MAX_EVENTS", len(before) + 8192 + 512):
            result = w.execute(self.req("tool-start", tool_id="cannot-finish"))
            self.assertEqual(result["code"], "ARCHIVE_PENDING")
            self.assertEqual(self.state()["participants"][self.base["coordinator"]]["pending"], {})
            self.assertEqual(active.read_bytes(), before)
            # Verify reserved archive and rollover work is still possible.
            self.checkpoint_archive()
            self.rollover()
        # Check the resulting event chain after capacity is restored.
        self.assert_history()

    def test_admitted_async_tool_can_transition_complete_and_archive_at_cap(self) -> None:
        """Reserve enough event capacity for an admitted asynchronous tool to finish.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Establish readiness and choose a bounded cap for admission and completion.
        self.create()
        self.ready()
        active = self.root / ".task/TEST-1/events.jsonl"
        cap = len(active.read_bytes()) + 3 * 8192 + 1024

        def exhaust_ordinary_events() -> None:
            """Fill the bounded stream until ordinary event admission is refused.

            Raises:
                AssertionError: Rejection has the wrong code or the cap is not reached
                    within 100 attempts.
            """
            # Bound the fill loop so a missing capacity limit fails deterministically.
            for _ in range(100):
                result = w.execute(self.req("event", event_type="check", event={"code": "OK"}))
                # Accept only the expected archive-capacity refusal as the stopping condition.
                if not result["ok"]:
                    self.assertEqual(result["code"], "ARCHIVE_PENDING")
                    return
            # Fail explicitly if ordinary events never reach the configured cap.
            self.fail("Expected bounded fixture to reach event admission cap")

        # Admit one tool, then fill ordinary capacity before its async transition.
        with mock.patch.object(w, "MAX_EVENTS", cap):
            self.call("tool-start", tool_id="admitted")
            exhaust_ordinary_events()
            # Record the asynchronous transition without requiring ordinary free capacity.
            self.call("tool-complete", tool_id="admitted", async_handle="handle-a", completed=False)
            exhaust_ordinary_events()
            # Capture state after filling ordinary capacity a second time.
            before = active.read_bytes()
            revision = self.state()["revision"]
            # Replay the same async transition to exercise no-op handling.
            for _ in range(3):
                self.call(
                    "tool-complete", tool_id="admitted", async_handle="handle-a", completed=False
                )
            # Verify replay leaves the stream and revision unchanged.
            self.assertEqual(active.read_bytes(), before)
            self.assertEqual(self.state()["revision"], revision)
            # Reject a conflicting async handle without publishing another event.
            changed = w.execute(
                self.req(
                    "tool-complete", tool_id="admitted", async_handle="handle-b", completed=False
                )
            )
            self.assertEqual(changed["code"], "REQUEST_CONFLICT")
            self.assertEqual(active.read_bytes(), before)
            # Complete the admitted tool using its reserved capacity, then archive and rotate.
            self.call("tool-complete", tool_id="admitted", completed=True)
            self.assertEqual(self.state()["participants"][self.base["coordinator"]]["pending"], {})
            self.checkpoint_archive()
            self.rollover()
        # Validate the complete chain after leaving the constrained-capacity scope.
        self.assert_history()
