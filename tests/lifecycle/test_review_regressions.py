"""Author regression tests for independently reported findings; not independent review."""

import copy
import multiprocessing
from pathlib import Path
from unittest import mock

import pytest

from agent_company.lifecycle import task_workspace as w
from tests.support import Fixture, call_process
from tests.types import JsonObject

pytestmark = pytest.mark.integration


class ReviewRegressions(Fixture):
    """Retain author regressions for reported findings without claiming independent review."""

    def test_checkpoint_and_provenance_survive_archive_restore(self) -> None:
        """Preserve the recorded checkpoint across synthetic archival and restoration.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Record a checkpoint containing distinctive handoff and source fields.
        self.create()
        checkpoint = {
            "goal": "unique-goal",
            "constraints": ["unique-constraint"],
            "sources": self.evidence(),
            "progress": "unique-progress",
            "blockers": "unique-blocker",
            "handoff": "unique-handoff",
            "candidate": "test-only",
        }
        self.call("checkpoint", expected_revision=self.state()["revision"], checkpoint=checkpoint)
        # Archive, clean and restore the disposable workspace.
        observations = self.archive()
        self.require_ok(w.execute(self.cleanup_request(observations)))
        self.call("restore", observations=observations)
        # Compare the restored checkpoint with the original object.
        self.assertEqual(self.state()["checkpoint"], checkpoint)

    def test_completion_requires_source_bytes_and_provider_status(self) -> None:
        """Reject a completed outcome supported only by placeholder evidence locators.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Supply fixture-only evidence for each completion obligation.
        self.create()
        completion = {
            name: self.evidence() for name in ["human_acceptance", "merge", "obligations"]
        }
        # Attempt completion and require evidence failure without changing active status.
        result = w.execute(
            self.req(
                "outcome",
                expected_revision=self.state()["revision"],
                disposition="completed",
                evidence=self.evidence(),
                completion=completion,
            )
        )
        self.assertEqual(result["code"], "EVIDENCE_REQUIRED")
        self.assertEqual(self.state()["disposition"], "active")

    def test_explicit_reconciliation_imports_only_inspected_markdown(self) -> None:
        """Import an inspected external roadmap edit without replacing its bytes.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Write an external edit and hash the exact current inventory.
        self.create()
        path = self.root / ".task/TEST-1/roadmap.md"
        path.write_text("# inspected external edit\n")
        files = {
            p: w.sha((self.root / ".task/TEST-1" / p).read_bytes()) for p in self.state()["files"]
        }
        # Reconcile the inspected files and verify the edited roadmap remains readable.
        self.call(
            "reconcile-files",
            expected_revision=self.state()["revision"],
            inventory=files,
            evidence=self.evidence(),
        )
        self.assertEqual(path.read_text(), "# inspected external edit\n")
        self.call("diagnose")

    def test_rebind_retry_does_not_duplicate_and_changed_request_conflicts(self) -> None:
        """Replay identical rebind requests and reject changes under the same ID.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Create a second issue under its own coordinator identity.
        self.create()
        target = self.req(
            "create",
            issue_id="TEST-2",
            issue_uuid="second",
            session_id="other",
            binding_generation=None,
        )
        target["coordinator"] = w.participant_key(target)
        created = self.require_ok(w.execute(target))
        # Preassign the original coordinator a packet in the target issue.
        scope = {
            **target,
            "operation": "scope",
            "request_id": "target-scope",
            "binding_generation": 1,
            "expected_revision": created["revision"],
            "target_participant": self.base["coordinator"],
            "packet": [],
        }
        self.require_ok(w.execute(scope))
        # Rebind once and require identical replay results.
        request = self.req("rebind", new_issue_id="TEST-2", evidence=self.evidence())
        first = self.require_ok(w.execute(request))
        self.assertEqual(first, w.execute(request))
        # Change evidence while retaining request identity and require a conflict.
        altered = copy.deepcopy(request)
        altered["evidence"][0]["id"] = "different"
        self.assertEqual(w.execute(altered)["code"], "REQUEST_CONFLICT")

    def test_archive_prepare_retry_after_seal_is_exactly_once(self) -> None:
        """Recover a post-seal failure without publishing duplicate archive events.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Record a terminal outcome and prepare a sealing archive request.
        self.create()
        self.call(
            "outcome",
            expected_revision=self.state()["revision"],
            disposition="cancelled",
            evidence=self.evidence(),
        )
        request = self.req("archive-prepare", expected_revision=self.state()["revision"], seal=True)

        def crash(point: str) -> None:
            """Interrupt archive preparation after sealing.

            Args:
                point: Persistence seam reported by the core.

            Raises:
                OSError: The configured seam is reached; other seams return normally.
            """
            # Raise only at the selected seam; let other persistence steps continue.
            if point == "transaction-complete":
                raise OSError("after seal")

        # Fail after transaction completion to exercise replay after sealing.
        with mock.patch.object(w, "FAILPOINT", crash):
            self.assertFalse(w.execute(request)["ok"])
        # Retry the original request and verify exactly-once event history.
        first = self.require_ok(w.execute(request))
        self.assertEqual(first, w.execute(request))
        self.assertEqual(self.state()["seq"], 3)

    def test_attach_reopen_racing_cleanup_never_loses_new_work(self) -> None:
        """Serialize reopen against cleanup so exactly one valid outcome survives.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Load the process worker used to race independent lifecycle requests.

        # Prepare cleanup and reopen requests against one terminal archive.
        self.create()
        observations = self.archive()
        cleanup = self.cleanup_request(observations)
        reopen = self.req(
            "reopen", expected_revision=self.state()["revision"], evidence=self.evidence()
        )
        # Race the two state transitions using separate processes.
        with multiprocessing.get_context("fork").Pool(2) as pool:
            results = pool.map(call_process, [cleanup, reopen])
        # Require one winner and inspect its committed storage state.
        self.assertEqual(sum(r["ok"] for r in results), 1, results)
        state = self.state()
        # If cleanup wins, preserve the terminal disposition.
        if state["storage"] == "cleaned":
            self.assertEqual(state["disposition"], "cancelled")
        # If reopen wins, preserve active status and the task roadmap.
        else:
            self.assertEqual(state["disposition"], "active")
            self.assertTrue((self.root / ".task/TEST-1/roadmap.md").is_file())

    def test_view_swap_blocks_readiness_without_touching_other_directory(self) -> None:
        """Reject a swapped worktree view without changing its new target.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Create a ready issue and attach the second worktree view.
        self.create()
        self.ready()
        self.call("attach", worktree=str(self.other))
        # Replace that view with a link to an outside sentinel directory.
        view = self.other / ".task/TEST-1"
        view.unlink()
        outside = Path(self.temp.name).resolve() / "outside"
        outside.mkdir()
        (outside / "keep").write_text("sentinel")
        view.symlink_to(outside)
        # Require readiness failure while preserving the outside sentinel.
        self.assertFalse(w.execute(self.req("ready", worktree=str(self.other)))["ok"])
        self.assertEqual((outside / "keep").read_text(), "sentinel")

    def test_terminal_reconciliation_requires_explicit_reopen(self) -> None:
        """Retain external edits but reject their import into a terminal issue.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Close the issue with an explicit cancellation outcome.
        self.create()
        self.call(
            "outcome",
            expected_revision=self.state()["revision"],
            disposition="cancelled",
            evidence=self.evidence(),
        )
        # Write a post-terminal change and inspect the resulting inventory.
        path = self.root / ".task/TEST-1/roadmap.md"
        path.write_text("# changed after terminal")
        inventory = {
            p: w.sha((self.root / ".task/TEST-1" / p).read_bytes()) for p in self.state()["files"]
        }
        # Require terminal-state refusal without discarding the external edit.
        result = w.execute(
            self.req(
                "reconcile-files",
                expected_revision=self.state()["revision"],
                inventory=inventory,
                evidence=self.evidence(),
            )
        )
        self.assertEqual(result["code"], "TERMINAL")
        self.assertEqual(path.read_text(), "# changed after terminal")

    def test_multipart_more_than_old_verifier_limit_reconstructs(self) -> None:
        """Verify a small-chunk archive beyond the old observation-count boundary.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Create the disposable issue and retain the real archive exporter.
        self.create()
        export_documents = w.export_documents

        def export_bounded_parts(snapshot: JsonObject) -> JsonObject:
            """Exercise the real exporter with a snapshot-sized multipart budget.

            Args:
                snapshot: Frozen lifecycle snapshot supplied by archive preparation.

            Returns:
                Real export documents with a chunk size targeting 384 parts.

            Raises:
                AssertionError: Exported chunks do not reconstruct the exact snapshot.
                w.WorkspaceError: The real exporter rejects the snapshot or documents.
            """
            # Round up the chunk budget so template growth cannot exceed 384 parts.
            raw = w.canonical(snapshot)
            chunk_size = max(1, (len(raw) + 383) // 384)
            # Run the real exporter under the temporary chunk-size override.
            with mock.patch.object(w, "ARCHIVE_CHUNK", chunk_size):
                result = export_documents(snapshot)
            # Require byte-for-byte reconstruction before returning provider documents.
            reconstructed = b"".join(
                w.decode(w.parse_document(part["content"])["data"]) for part in result["parts"]
            )
            self.assertEqual(reconstructed, raw)
            return result

        # Derive the budget from the exact sealed snapshot, including lifecycle events.
        with mock.patch.object(w, "export_documents", export_bounded_parts):
            export = self.seal()
        # Check that the fixture crosses the old bound while staying bounded.
        self.assertGreater(len(export["parts"]), 257)
        self.assertLessEqual(len(export["parts"]), 513)
        # Build synthetic read-back for all parts and their index.
        observations = self.observations(export)
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
        # Keep all part observations plus the index within the provider-read limit.
        self.assertLessEqual(len(observations), 514)
        # Verify reconstruction and ensure idempotency state does not inline export bodies.
        self.call("archive-verify", observations=observations)
        stored = self.state()["requests"]
        self.assertFalse(
            any(
                "parts" in item["result"] and "snapshot" in item["result"]
                for item in stored.values()
            )
        )

    def test_old_archive_prepare_retry_returns_its_immutable_export(self) -> None:
        """Replay an older archive request without replacing the latest export pointer.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Prepare two archive revisions with separate request identities.
        self.create()
        first_request = self.req("archive-prepare", expected_revision=self.state()["revision"])
        first = self.require_ok(w.execute(first_request))
        second = self.call("archive-prepare", expected_revision=self.state()["revision"])
        # Verify old-request replay returns its snapshot while the latest pointer stays current.
        self.assertNotEqual(first["snapshot"], second["snapshot"])
        self.assertEqual(first, w.execute(first_request))
        self.assertEqual(self.state()["export"]["snapshot"], second["snapshot"])

    def test_permission_paths_resolve_subdirectory_worktree(self) -> None:
        """Resolve required permission roots from the Git worktree, not its subdirectory.

        Raises:
            AssertionError: An asserted lifecycle or boundary invariant does not hold.
        """
        # Create a nested caller directory and resolve its lifecycle permission paths.
        nested = self.root / "nested"
        nested.mkdir()
        paths = w.permission_paths(self.req("resume", worktree=str(nested)))
        # Require the worktree binding path and exclude a fabricated nested store.
        self.assertIn(str(self.root / ".task/.bindings"), paths)
        self.assertNotIn(str(nested / ".task/.bindings"), paths)
