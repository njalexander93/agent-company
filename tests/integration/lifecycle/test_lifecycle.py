"""Test local lifecycle persistence in disposable Git stores.

Provider evidence is synthetic. These tests do not prove live desktop coverage,
provider acceptance, human approval or adversarial runtime isolation.
"""

import copy
import json
import multiprocessing
import os
import subprocess
from pathlib import Path

import pytest

from agent_company.lifecycle import task_workspace as w
from tests.support import ROOT, Fixture, call_process

pytestmark = pytest.mark.integration


class LifecycleTests(Fixture):
    """Exercise local lifecycle, containment and adapter contracts in disposable stores."""

    def test_T01_cross_worktree_concurrent_create_and_isolation(self) -> None:
        """Verify concurrent creation converges while another issue stays separate.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Prepare matching issue creation from both registered worktrees.
        requests = [
            self.req("create"),
            self.req("create", worktree=str(self.other), binding_generation=1),
        ]
        # Race both creators using separate processes.
        with multiprocessing.get_context("spawn").Pool(2) as pool:
            results = pool.map(call_process, requests)
        # Check every result and the shared canonical view.
        for r in results:
            self.require_ok(r)
        view = self.other / ".task/TEST-1"
        self.assertTrue(view.is_symlink() or view.is_junction())
        self.assertEqual((self.other / ".task/TEST-1").resolve(), self.root / ".task/TEST-1")
        # Create a different issue and verify its roadmap has a distinct identity.
        second = self.req("create", issue_id="TEST-2", issue_uuid="second", session_id="second")
        second["coordinator"] = w.participant_key(second)
        self.require_ok(w.execute(second))
        self.assertNotEqual(
            (self.root / ".task/TEST-1/roadmap.md").read_bytes(),
            (self.root / ".task/TEST-2/roadmap.md").read_bytes(),
        )

    def test_T02_invalid_ids_links_foreign_registration(self) -> None:
        """Reject unsafe issue IDs and linked payloads without changing outside bytes.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Try malformed and ambiguous IDs before creating any payload.
        for identifier in [
            "../TEST-1",
            "test-1",
            "TEST-01",
            "TEST-0",
            "TEST-1/",
            "ТEST-1",
            "TEST-1\n",
        ]:
            self.assertFalse(w.execute(self.req("create", issue_id=identifier))["ok"])
        # Create an outside sentinel and substitute a payload symlink.
        sentinel = Path(self.temp.name).resolve() / "sentinel"
        sentinel.write_text("outside")
        self.create()
        note = self.root / ".task/TEST-1/context/escape.md"
        note.symlink_to(sentinel)
        # Check that readiness refuses the substituted path without changing its target.
        self.assertFalse(w.execute(self.req("ready"))["ok"])
        self.assertEqual(sentinel.read_text(), "outside")
        # Replace the symlink with a hard link and repeat the containment check.
        note.unlink()
        os.link(sentinel, note)
        self.assertFalse(w.execute(self.req("ready"))["ok"])
        self.assertEqual(sentinel.read_text(), "outside")

    def test_T03_resume_idempotency_adoption_generation(self) -> None:
        """Preserve exact roadmap bytes across retries, resume and manual adoption.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Create once and replay the same request to check idempotency.
        request = self.req("create")
        first = self.require_ok(w.execute(request))
        self.assertEqual(first, w.execute(request))
        self.base["binding_generation"] = first["binding_generation"]
        # Capture progress and verify resume preserves it.
        before = (self.root / ".task/TEST-1/roadmap.md").read_bytes()
        self.call("resume")
        self.assertEqual(before, (self.root / ".task/TEST-1/roadmap.md").read_bytes())
        # Reject stale participant generations and conflicting provider identities.
        self.assertEqual(
            w.execute(self.req("resume", binding_generation=999))["code"], "STALE_BINDING"
        )
        self.assertEqual(
            w.execute({**request, "issue_uuid": "different"})["code"], "ISSUE_MISMATCH"
        )
        # Prepare a manually maintained workspace with distinctive formatting.
        manual = self.root / ".task/MANUAL-2"
        (manual / "context").mkdir(parents=True)
        (manual / "roadmap.md").write_text("# Preserve my exact formatting\n")
        # Bind adoption to the inspected inventory and its explicit owner.
        request = self.req(
            "adopt",
            issue_id="MANUAL-2",
            session_id="manual",
            binding_generation=None,
            issue_uuid="manual",
            evidence=self.evidence(),
        )
        key = w.participant_key(request)
        request.update(
            coordinator=key,
            inventory={"roadmap.md": w.sha((manual / "roadmap.md").read_bytes())},
            owners={"roadmap.md": key},
        )
        # Adopt the existing bytes and verify only the adoption event is new.
        self.require_ok(w.execute(request))
        self.assertEqual((manual / "roadmap.md").read_text(), "# Preserve my exact formatting\n")
        self.assertEqual(len((manual / "events.jsonl").read_text().splitlines()), 1)

    def test_T04_concurrent_update_and_owner(self) -> None:
        """Allow one revision-matched writer and reject its racing stale update.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Prepare two competing writes from the same committed revision.
        self.create()
        state = self.state()
        requests = [
            self.req(
                "update",
                path="roadmap.md",
                content="# " + str(i),
                expected_revision=state["revision"],
                old_digest=state["files"]["roadmap.md"],
                provenance={
                    "sources": self.evidence(),
                    "status": "draft",
                    "applicability": "fixture",
                },
            )
            for i in range(2)
        ]
        # Race the writes in separate processes under the shared issue lock.
        with multiprocessing.get_context("spawn").Pool(2) as pool:
            results = pool.map(call_process, requests)
        # Require one winner and one explicit revision conflict.
        self.assertEqual(sum(r["ok"] for r in results), 1, results)
        self.assertIn("REVISION_CONFLICT", [r["code"] for r in results])

    def test_T05_transaction_boundaries_exactly_once(self) -> None:
        """Recover each interrupted creation with exactly one committed event.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Exercise each selected persistence boundary with a fresh issue.
        for point in [
            "intent",
            "payload:roadmap.md",
            "payload:events.jsonl",
            "payload-flush",
            "state",
            "transaction-complete",
        ]:
            # Isolate the seam and give this attempt its own coordinator identity.
            with self.subTest(point=point):
                request = self.req(
                    "create", issue_id="CRASH-" + str(self.serial + 1), session_id=point
                )
                request["coordinator"] = w.participant_key(request)

                def fail(actual: str) -> None:
                    """Interrupt the selected transaction persistence boundary.

                    Args:
                        actual: Persistence seam reported by the core.

                    Raises:
                        OSError: The configured seam is reached; other seams return normally.
                    """
                    # Raise only at the selected seam; let other persistence steps continue.
                    if actual == point:
                        raise OSError("injected")

                # Install the failure seam before executing the creation transaction.
                w.FAILPOINT = fail
                # Expect the injected I/O failure to become an unsuccessful result.
                try:
                    self.assertFalse(w.execute(request)["ok"])
                # Always remove the seam before retrying or leaving the subtest.
                finally:
                    w.FAILPOINT = None
                # Retry the same transaction and verify exactly-once event publication.
                self.require_ok(w.execute(request))
                events = (self.root / ".task" / request["issue_id"] / "events.jsonl").read_bytes()
                self.assertEqual(w.validate_events(events)[0], 1)

    def test_T06_concurrent_events_secret_rejection_and_corruption(self) -> None:
        """Serialize concurrent events, reject free text and recover a partial tail.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Prepare independent event requests against one disposable issue.
        self.create()
        requests = [self.req("event", event_type="check", event={"code": "OK"}) for _ in range(12)]
        # Append concurrently to exercise shared event coordination.
        with multiprocessing.get_context("spawn").Pool(4) as pool:
            # Require every concurrent append to complete successfully.
            for r in pool.map(call_process, requests):
                self.require_ok(r)
        # Validate the complete stream and reject an unrestricted diagnostic payload.
        path = self.root / ".task/TEST-1/events.jsonl"
        self.assertEqual(w.validate_events(path.read_bytes())[0], 13)
        self.assertFalse(
            w.execute(self.req("event", event_type="check", event={"summary": "SECRET-MARKER"}))[
                "ok"
            ]
        )
        self.assertNotIn(b"SECRET-MARKER", path.read_bytes())
        # Inject an interrupted final write without altering the committed prefix.
        with path.open("ab") as stream:
            stream.write(b'{"interrupted":')
        # Trigger tail recovery and verify the unrelated missing-scope diagnostic remains.
        self.assertEqual(w.execute(self.req("ready"))["code"], "SCOPE_MISSING")
        self.assertTrue(path.read_bytes().endswith(b"\n"))

    def test_T07_scope_and_stale_source(self) -> None:
        """Deny readiness when an acknowledged required source changes.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Create a required source and a packet pinned to its initial bytes.
        self.create()
        source = Path(self.temp.name).resolve() / "source.md"
        source.write_text("required source")
        packet = [
            {
                "id": "source",
                "locator": str(source),
                "sha256": w.sha(source.read_bytes()),
                "required": True,
                "authority": "fixture",
                "reason": "test",
                "stage": "review",
                "reader": self.base["coordinator"],
            }
        ]
        # Deliver only the assigned reference and check that the roadmap is not disclosed.
        self.call(
            "scope",
            expected_revision=self.state()["revision"],
            target_participant=self.base["coordinator"],
            packet=packet,
        )
        read = self.call("read")
        self.assertEqual(len(read["references"]), 1)
        self.assertNotIn("roadmap.md", json.dumps(read))
        # Acknowledge the exact packet and establish readiness.
        self.call("acknowledge", packet_digest=read["packet_digest"])
        self.call("ready")
        # Change the source after acknowledgment and require a stale-source stop.
        source.write_text("changed")
        self.assertEqual(w.execute(self.req("ready"))["code"], "SOURCE_STALE")

    def test_T08_active_in_review_pending_retained(self) -> None:
        """Retain active, pending-tool and in-review workspaces.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Establish readiness and verify active work cannot be collected.
        self.create()
        self.ready()
        self.assertEqual(w.execute(self.req("cleanup-plan"))["code"], "RETAINED")
        # Keep pending execution from detaching or sealing its workspace.
        self.call("tool-start", tool_id="tool-1")
        self.assertEqual(
            w.execute(self.req("detach", evidence=self.evidence()))["code"], "PENDING_OPERATION"
        )
        self.assertEqual(
            w.execute(
                self.req("archive-prepare", expected_revision=self.state()["revision"], seal=True)
            )["code"],
            "PENDING_OPERATION",
        )
        # Settle the tool, enter review, and verify review still prevents cleanup.
        self.call("tool-complete", tool_id="tool-1", completed=True)
        self.call(
            "outcome",
            expected_revision=self.state()["revision"],
            disposition="in_review",
            evidence=self.evidence(),
        )
        self.assertEqual(w.execute(self.req("cleanup-plan"))["code"], "RETAINED")

    def test_T09_archive_validation_and_fresh_cleanup(self) -> None:
        """Require matching provider fixtures and a fresh cleanup challenge.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Prepare a terminal archive using synthetic provider observations.
        self.create()
        observations = self.archive()
        # Alter one archive identity dimension per verification attempt.
        for mutate in ["parent", "part", "version"]:
            bad = copy.deepcopy(observations)
            # Reject observations attributed to a different issue.
            if mutate == "parent":
                bad[0]["issue"] = "foreign"
            # Reject an incomplete multipart observation set.
            if mutate == "part":
                bad = bad[1:]
            # Reject a provider version that differs from the verified archive.
            if mutate == "version":
                bad[0]["updatedAt"] = "changed"
            # Verify malformed evidence retains the local workspace.
            self.assertFalse(w.execute(self.req("archive-verify", observations=bad))["ok"])
            self.assertTrue((self.root / ".task/TEST-1").is_dir())
        # Reject stale read-back, then collect only with a newly bound challenge.
        self.assertFalse(w.execute(self.req("cleanup-commit", observations=observations))["ok"])
        self.require_ok(w.execute(self.cleanup_request(observations)))
        self.assertFalse((self.root / ".task/TEST-1").exists())

    def test_T10_cleanup_interruption_and_outside_sentinel(self) -> None:
        """Resume interrupted cleanup without deleting a neighboring issue.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Prepare eligible cleanup and an outside issue sentinel.
        self.create()
        observations = self.archive()
        request = self.cleanup_request(observations)
        sentinel = self.root / ".task/OTHER-2"
        sentinel.mkdir()
        (sentinel / "keep").write_text("keep")

        def fail(point: str) -> None:
            """Interrupt cleanup at quarantine publication.

            Args:
                point: Persistence seam reported by the core.

            Raises:
                OSError: The configured seam is reached; other seams return normally.
            """
            # Raise only at the selected seam; let other persistence steps continue.
            if point == "quarantine":
                raise OSError("injected")

        # Install the quarantine failure seam.
        w.FAILPOINT = fail
        # Observe the interrupted cleanup as an unsuccessful result.
        try:
            self.assertFalse(w.execute(request)["ok"])
        # Remove the seam even if the assertion fails.
        finally:
            w.FAILPOINT = None
        # Retry cleanup and verify both the tombstone state and outside sentinel.
        self.require_ok(w.execute(request))
        self.assertEqual((sentinel / "keep").read_text(), "keep")
        self.assertEqual(self.state()["storage"], "cleaned")

    def test_T11_restore_and_repeated_restore(self) -> None:
        """Restore verified history once and require recovery after cleanup.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Archive and remove a terminal disposable workspace.
        self.create()
        observations = self.archive()
        self.require_ok(w.execute(self.cleanup_request(observations)))
        # Require recovery rather than an empty resume, then restore the saved bytes.
        self.assertEqual(w.execute(self.req("resume"))["code"], "RECOVERY_REQUIRED")
        restore = self.req("restore", observations=observations)
        result = self.require_ok(w.execute(restore))
        # Replay restore and verify the reopened generation and event history.
        self.assertEqual(result, w.execute(restore))
        self.assertEqual(self.state()["disposition"], "active")
        self.assertGreater(result["binding_generation"], 1)
        self.assertEqual(
            w.validate_events((self.root / ".task/TEST-1/events.jsonl").read_bytes())[0], 4
        )

    def test_T16_ignore_patterns_keep_products(self) -> None:
        """Ignore generated task and OS metadata while keeping reusable products.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Install the repository ignore rules in the disposable Git repository.
        (self.root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
        # Select representative generated paths and product paths.
        ignored = [
            ".task/TEST-1/roadmap.md",
            ".DS_Store",
            "nested/.DS_Store",
            "nested/._data",
            "Thumbs.db",
            "nested/Desktop.ini",
            "nested/.Trash-1000/file",
            "$RECYCLE.BIN/file",
        ]
        kept = [
            "docs/runtime/task-workspace.md",
            ".codex/hooks.json",
            "src/agent_company/resources/task_workspace/roadmap.md",
            "nested/product.ini",
            "product.cab",
            ".env.example",
            ".github/workflows/check.yml",
        ]
        # Require each generated or OS metadata path to match an ignore rule.
        for path in ignored:
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(self.root), "check-ignore", "--no-index", "-q", path]
                ).returncode,
                0,
                path,
            )
        # Require reusable product and configuration paths to remain trackable.
        for path in kept:
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(self.root), "check-ignore", "--no-index", "-q", path]
                ).returncode,
                1,
                path,
            )
