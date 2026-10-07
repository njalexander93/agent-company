# Task-workspace contract

**Local workflow guardrail.** The utility manages issue workspace creation, attachment, scoped readiness, archival and recovery. Installed-host trust/callback coverage and provider acceptance require separate evidence; protocol tests are not runtime or Security certification.

Host adapters call a shared lifecycle core for supported local task actions. It does not implement the Control Plane, authenticated Role authority, complete confidentiality, the paper reference checker or the full launcher.

## Scope and governing inputs

Use the public [context and independence rules](../framework/baseline.md#context-and-independent-assurance), [evidence/privacy rules](../framework/baseline.md#evidence-and-privacy) and [handoff/outcome rules](../framework/baseline.md#handoffs-and-outcomes). This document defines local mechanics, not new organizational authority.

The first supported platform is one local non-bare Git repository and its linked worktrees on macOS. Separate clones, remote hosts, synchronization, exposed services and background scheduling are excluded. A different host/platform architecture needs its own scope and validation.

## Storage and identity

```text
<registered-main-worktree>/.task/
  .control/
    repository.json
    issues/<ISSUE-ID>/       # lock, state, transactions, archive locator
  <ISSUE-ID>/               # actual task bytes
    roadmap.md
    events.jsonl
    context/                # focused Markdown notes, only when useful
<participating-worktree>/.task/
  .repository.json          # local registration reference, no secrets
  .bindings/<session-key>.json
  <ISSUE-ID> -> <registered-main-worktree>/.task/<ISSUE-ID>
```

The main worktree uses its real issue directory; other worktrees expose **only issue-specific links**. Never link the entire `.task` root. `.control` is implementation metadata, outside disposable issue data; it is not the runtime authority ledger. Binding files contain identifiers and scope digests, not task prose. Track reusable code/templates and ignore `/.task/` in every worktree.

### Registration and validation

1. `register` takes an explicit main-worktree path. Resolve it and the caller with Git's `rev-parse --show-toplevel`, `--git-common-dir`, and `worktree list --porcelain`. Use subprocess argument arrays, never shell interpolation. Reject bare repositories and nonregistered worktrees.
2. Record schema version, a generated repository UUID, normalized common-Git path, main-worktree path and filesystem identities. Every attachment revalidates both Git membership and this registration. Branch names and remotes are not identity. Moving/removing the main worktree or replacing the common Git directory requires explicit registration recovery; never silently choose another empty root.
3. Accept issue IDs matching ASCII `^[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,9}$` exactly. This is the first increment's supported subset. Reject lowercase, Unicode lookalikes, whitespace, separators, dots, zero/leading-zero numbers and overlong IDs. Do not normalize unsafe input into a different issue. Bind the provider's immutable issue UUID when known; conflicting UUIDs stop attachment.
4. Validate every path component with `lstat` and directory descriptors. Canonical `.task`, `.control`, issue and context directories must be real directories under the registered root. Reject symlinks, special files and unexpected hard links in canonical payloads/control files. Verify ownership by the current local user and reject group/world-writable control roots. Create local directories/files with private modes (`0700`/`0600`). These checks do not protect against the same privileged user rewriting the store.
5. An existing worktree link is acceptable only if its target resolves exactly to the registered issue directory and remains in this repository. Reject dangling, foreign or substituted links. Never overwrite an existing real directory or link as an attach side effect. Create a missing link atomically; verify it after creation. Link removal unlinks the link only.
6. Payload paths are either `roadmap.md` or `context/<safe-name>.md`; use ASCII names matching `[a-z0-9][a-z0-9._-]{0,79}` with no `..` component. No absolute paths, traversal, nested redirection or arbitrary metadata edits. The core alone writes `events.jsonl` and control state. Use no-follow opens and descriptor-relative operations; a resolve-then-recursive-delete sequence is insufficient.
7. Read local registration from a caller-worktree reference, not a global current-task marker. Registration copies must agree with the canonical repository UUID/path. A copied registration from a separate clone fails validation.

### Permissions and existing manual state

Required access is narrow: the assigned canonical issue directory, its `.control/issues/<ID>` directory, and read access to repository registration. Setup alone needs permission to create registration/parent directories. The local worktree needs its own link/binding paths. Obtain these through supported host permissions; **links confer no permission**. A denial yields `PERMISSION_REQUIRED` with the exact paths and recovery operation. Never change global security settings, bypass hook trust, or request the entire main checkout merely for convenience.

New-issue cleanup examines only explicitly registered candidate IDs for which the caller already has appropriate access. It does not require broad access to other issues. Inaccessible candidates are retained and reported as skipped; creation of an independent authorized issue can proceed.

Existing manually shared workspaces require `adopt`, not `create --force`. Under the issue lock, inventory and hash existing bytes, preserve roadmap format/content, assign existing note owners explicitly, and record adoption provenance. Do not synthesize old events. Unexpected files or unresolved ownership stop adoption for review. No startup path empties or reinitializes an existing directory.

## Core interface and state

The shared core is `src/agent_company/lifecycle/task_workspace.py`, with a Python standard-library API and matching command interface. The reusable entry point is `execute(request: dict) -> dict`; CLI reads one bounded UTF-8 JSON object from stdin, or accepts exactly one `--request-json` argument, and emits one JSON object to stdout. The argument route makes bootstrap callable without shell redirection. Host-specific input never enters the core unchanged. Command names below are operations in the request, not shell fragments.

Every request has `schema_version: 1`, `operation`, `request_id` and explicit `worktree`. **Bootstrap exception:** `register` takes `main_worktree` and no `repo_id`; it creates or validates registration and returns the acquired `repo_id`. A pre-registration `diagnose` omits `repo_id` and returns `REGISTRATION_REQUIRED` or the stored identity. Every issue operation then supplies `repo_id`, `host`, `session_id`, and applicable `issue_id`, `participant_id`, `binding_generation`, `expected_revision` and operation arguments. IDs are opaque bounded strings except validated issue IDs; filesystem names derived from session IDs use SHA-256, never raw host identifiers. Persist idempotency by request ID plus canonical request digest. Same ID/same request returns the prior result; same ID/different request is rejected. Do not record arbitrary raw request bodies.

Responses contain `ok`, `code`, identifiers, current `revision`, `binding_generation`, allowed reference descriptors and a bounded diagnostic/recovery action. No private note body is returned by status/diagnostic operations. Suggested CLI exits: `0` success, `2` invalid input, `3` conflict/not-ready, `4` I/O/provider/recovery failure. These exits are **not** a native host hook response protocol.

| Operation | Required behavior |
|---|---|
| `register`, `adopt`, `diagnose` | Explicit local setup, safe migration, or read-only bounded status. Registration/adoption never infer completion. |
| `bind` | Bind an explicitly named issue and scope to a host session. Existing identical binding is idempotent; conflicting issue requires `rebind`. Initial coordinator is explicitly designated at create/adopt; joining a session does not confer that role. |
| `create` | Under the persistent issue lock, create only if absent and no tombstone/history exists; atomically publish a complete template workspace. Concurrent creators converge; differing identities/scopes conflict. |
| `attach`, `resume` | Validate membership, scope, binding generation and payload; register a participant and preserve bytes. A missing directory with a tombstone enters recovery, never empty creation. |
| `rebind` | Explicit old/new issue and expected binding generation; detach old participation, then bind the new issue. Preserve old issue state. Refuse while participant-owned external operations remain unresolved. Never rebind a parent under active child participants. |
| `scope`, `read` | Coordinator installs a versioned packet manifest; participant reads only permitted references/notes. `read` checks each required digest and scope. Scope changes invalidate prior acknowledgment. |
| `acknowledge`, `ready` | Acknowledge exact packet digest/required-source availability, then check binding, scope, ownership, unresolved transaction and filesystem access. This establishes delivery facts, not comprehension or execution authority. |
| `update` | Change one owned payload file with expected issue revision and old file digest; validate provenance and commit through the transaction writer. Revision conflicts require reread/reapply; never last-writer-wins. |
| `checkpoint` | Coordinator records current goal, constraints, pinned source references, progress, blockers, next handoff and exact candidate/evidence references. PR submission records `in_review` and retains context. |
| `event` | Append one allowlisted bounded event under the same issue lock. Caller cannot impersonate another participant or invent provider acceptance. |
| `outcome` | Coordinator records `active`, `blocked`, `in_review`, or terminal `completed`, `cancelled`, `failed`, with source evidence and expected revision. Failed only means terminal when explicitly abandoned with disposition; retryable failure stays blocked. |
| `detach`, `reconcile-participant`, `transfer-coordinator` | Retire only the identified generation after verifying no pending activity; explicit evidence is required for uncertain participants and ownership transfers. No timeout-based coordinator election. |
| `event-rollover` | Coordinator, expected revision, no pending operations and current verified archive required; retain an immutable segment and start the next globally chained stream. |
| `archive-prepare`, `archive-verify` | Produce immutable export request; verify provider read-back and establish an archive receipt for that snapshot. No local boolean substitutes for read-back. |
| `cleanup-plan`, `cleanup-commit` | Select/recheck only eligible issues; record recoverable intent; remove only the verified unchanged issue payload. Never delete control metadata. |
| `restore` | Verify archive bytes and manifest into staging, then publish under lock and rebind with a new generation. Invalid/unavailable archives stop for recovery. |

### State transitions

Keep three dimensions separate:

- **Issue disposition:** `active ↔ blocked`, `active/blocked → in_review`, `in_review → active` for corrections; explicit evidence permits a terminal disposition. `Stop`, session archival, inactivity and a submitted PR do not imply terminal state.
- **Storage:** `absent → present → archive_pending → archive_verified → cleanup_pending → cleaned`. Any changed payload invalidates the verified snapshot. `cleaned → recovery_required → present` only after verified restore. Transaction uncertainty makes readiness false until recovery resolves it.
- **Participant:** `unbound → attached → ready → detached`; a scope/binding change returns it to `attached`. Lost host signals yield `unknown`, never automatically `detached`. Reattachment uses a new generation and fences stale writes.

Terminal recording for a completed issue requires references to exact human acceptance, actual required merge and completion of issue obligations. The utility validates supplied identities/evidence and provider status; it cannot manufacture or independently grant human acceptance. Terminal state still forbids ordinary task edits until explicit reopen, which invalidates cleanup eligibility. Cancellation preserves its recorded disposition/history.

## Ownership, context and participant lifetime

One coordinator owns `roadmap.md`, shared summary and packet assignment. Participants own distinct named notes, recorded in control state, for example `context/implementation.md`. Every note/update carries author/participant, source references and digests, applicability, status, and superseded revision when relevant. No private reasoning is required. Ownership transfers are explicit, revision-checked and recorded.

Packet manifests list reference ID, source locator, content digest/version, authority class, required/optional status, inclusion reason and allowed reader/stage. Required governing sources remain independently retrievable; summaries do not replace them. Refresh allowed references on resume/compaction and after material source changes. A stale required reference denies readiness for affected work. A packet checksum does not prove semantic completeness.

Fresh-author and independent-review participants receive explicit allowlists. Do not expose the whole roadmap, events stream, archive, upstream author notes or research by default. An authorized factual safety notice can be added with provenance without disclosing upstream conclusions. Hook output contains only fixed control text, safe IDs and packet digests; task text is retrieved as data through scoped reads. Do not interpolate roadmap/note/prompt content into developer-level `additionalContext`.

Host session identifiers are opaque. Root participant identity is `(repo_id, host, session_id, generation)`. A child also needs a verified distinct child identity and its own scope. Parent issue inheritance does not mean parent packet inheritance. A child needs an unambiguous host-to-tool identity mapping before its scope can be trusted. Do not infer it from cwd, timing, agent type or transcript content. See the [host guide](host-hooks.md) for per-host child and coverage limitations.

`Stop`/Interrupt are observations, not detachment. `SessionEnd` marks a session-end observation but does not prove children or shell processes ended. Track pending supported tool IDs and returned asynchronous execution handles. Explicit detachment requires no outstanding tools/children and a host confirmation or accountable coordinator reconciliation. A missing PID, expired heartbeat or elapsed time is insufficient. Unknown participants retain the issue and prevent cleanup. No recurring liveness service is introduced.

## Coordination and crash recovery

Use a persistent per-issue lock in `.control/issues/<ID>/`, shared by create/attach/update/archive/cleanup. A process-held OS lock (Python `fcntl` on this platform) releases when its process exits; never delete/recreate the lock file to break a lock. Bound waits and return `BUSY`; hooks must return a denial before their own host timeout. Acquire two issue locks for rebind in lexical ID order. Setup registration has its own short-lived setup lock.

Maintain an integer payload revision, binding generations, per-file digests, event sequence/head, ownership, participant records and pending transactions in issue control state. Sessions do not hold locks across model/provider calls. Archive exports are snapshots; provider I/O occurs outside the lock and must be compared again at verification/cleanup.

For a payload mutation, stage the new file plus required event and expected state in a recoverable transaction before publication. Flush staged bytes and intent, then atomically replace the payload and state under lock. On interruption, reconcile old/new digests and transaction ID, finishing exactly once or returning `RECOVERY_REQUIRED`; never report partial state as ready. Test failures at every persistence boundary. This is a local transaction protocol, not an atomic transaction with Linear.

Out-of-band edits are unsupported concurrent writes. Detect manifest mismatch before ready/write/archive/cleanup; preserve changed bytes and return `UNTRACKED_CHANGE`. An explicit coordinator reconciliation imports the inspected change at a new revision. Do not silently overwrite it with template or older staged bytes.

## Events and retention

`events.jsonl` is a **local diagnostic stream**, not the full authority ledger. Write UTF-8 JSON, one complete newline-terminated object per event. The coordinated writer assigns sequence and identifiers. Required workspace-transition events commit with their local operation; optional host observations may fail without stopping unrelated work.

Version 1 event fields:

| Field | Meaning / bound |
|---|---|
| `schema_version`, `event_id`, `seq`, `at` | Version 1; UUID; monotonically increasing integer; UTC timestamp. Ordering uses sequence, not wall clock. |
| `repo_id`, `issue_id`, `participant_id`, `generation` | Validated binding; `system` only for core-owned recovery facts. |
| `type`, `outcome`, `required` | Allowlisted type; `ok/denied/failed/unknown`; retention class. |
| `operation_id`, `revision`, `cause_id` | Transaction/request association, payload revision, optional causal ID. |
| `refs` | At most 16 permitted references with kind, safe locator, revision/digest and reader scope. |
| `code`, `summary` | Typed diagnostic and fixed/sanitized summary, at most 512 characters. No free-form tool output. |
| `prev_digest`, `digest` | SHA-256 chain over canonical JSON excluding `digest`; first prior digest is null. |

Canonical JSON uses sorted keys, compact separators, UTF-8 and no NaN/Infinity. Limit an encoded event to 8 KiB. Types cover create/adopt/attach/rebind, scope/acknowledgment, update/checkpoint/outcome, detach/reconciliation, archive preparation, and recovery. Bounded readiness-denial/check/tool-completion observations are optional; never copy tool arguments, prompt bodies, transcripts, provider payloads, secrets or reasoning. Do not hash secrets as a substitute for omitting them.

A reader accepts only complete validated lines and their chain. An interrupted final line is quarantined in control state under the lock. Required transaction recovery reconstructs its exact event once; unprovable required evidence stops dependent mutations. Optional partial diagnostics may be discarded with a recorded loss count. Corruption inside the committed prefix is an integrity error, not permission to truncate history. A chain detects changes relative to a retained head; it does not resist an actor who rewrites the entire store/verifier.

For this increment, retain the complete bounded stream until verified archive and eligible cleanup; no clock-based expiry or background rotation. Cap the stream at 16 MiB: suppress further optional observations with an aggregate loss counter, and require an explicit archived-segment checkpoint before further required writes if space remains insufficient. Never silently drop required or unresolved-failure records. Segments retain sequence ranges/head digests and reconstructable content in the archive. The event reader enforces packet scope; physical same-user file access is not confidentiality proof.

## Archive provider boundary and recovery

**Selected first bridge: foreground Codex connector calls plus deterministic local verification.** Python command hooks have no established access to the foreground connector session. Do not invoke imagined Python MCP functions, scrape credentials or start a second Codex model from a hook.

The available tool declarations provide:

- `mcp__codex_apps__linear_save_document({issue, title, content})` to create a document, or `{id, content}` to update it.
- `mcp__codex_apps__linear_get_document({id})` for read-back.
- `mcp__codex_apps__linear_get_issue({id})` and `linear_list_documents` for identity/recovery lookup.

The command-hook process does not inherit the foreground connector session. Provider work is an explicit foreground operation, never an assumed hook-side transaction. Preserve data when a connector or required observation is unavailable.

### Concrete archive sequence

1. `archive-prepare` requires coordinator ownership and expected revision. Freeze a snapshot and request ID under lock. For terminal collection, `seal:true` requires all other participants detached and no outstanding ordinary tools/children; it detaches the coordinator from payload work and records that fact before snapshotting. The session keeps a maintenance-only binding for this issue. Include cleanup eligibility/disposition intent in that snapshot before hashing. Do not mark it archived. Return an export file and exact document title/content plus issue UUID; export paths are local data, never executable commands.
2. The foreground coordinator uses the connected Linear tool to save exactly that request into an issue-parented document. Use a title containing issue ID and snapshot digest. Preserve previous verified snapshots; never replace their sole recovery bytes with a partial new version. On uncertain save outcome, search/read matching documents before retrying creation. Do not retry blind and infer which duplicate is authoritative.
3. Read the returned document ID through `linear_get_document`. Pass the observed ID, URL, parent issue identity, provider `updatedAt`, and read-back content to `archive-verify`. A provider response imported by the foreground bridge is attributable evidence, not authenticated runtime authority. Retain its origin/request identity; a hand-edited success flag is not accepted.
4. The verifier parses the structured payload, decodes every file, checks byte lengths/digests, validates the repository/issue/snapshot manifest, and compares against the frozen snapshot. Reacquire the issue lock and require the current payload revision/digests to match. A provider-normalized Markdown wrapper may differ; decoded archived bytes may not.
5. Record the verified locator, provider version, payload digest, manifest digest, event prefix/head, snapshot revision and read-back time outside the disposable directory. This receipt authorizes only the specified local eligibility check; it is not human acceptance. If the receipt/snapshot is stale, keep the workspace.

The archive document contains a readable outcome/goal/constraints/progress/blockers/source/handoff history plus a versioned structured manifest and reconstructable payload. For exactness, encode UTF-8 roadmap/context/event bytes in base64 inside a fenced JSON object. Each entry carries relative path, byte length, SHA-256 and disclosure scope. Digests use original bytes. Sensitive material must be excluded before export, not merely encoded. Research stays at its access-controlled durable source; large external evidence needs a verified durable reference and scope.

Use a conservative 256 KiB export-document limit as a **local implementation limit**, not a claimed Linear limit. Larger payloads require numbered issue documents plus a root manifest of document IDs/digests and read-back of every part. If the provider rejects size/content, preserve local data and return `ARCHIVE_PENDING`; never truncate or silently omit meaningful history. Validate actual connector behavior and multipart reconstruction separately from local provider fixtures.

### Cleanup and restore

`cleanup-plan` is a preview. `cleanup-commit` under the same persistent issue lock must freshly verify:

- Recorded terminal disposition and exact outcome evidence; no active, blocked, in-review or uncertain state.
- No attached/ready/unknown participant, pending tool/child or unresolved transaction.
- A successful fresh provider read-back of the referenced archive for this cleanup attempt. The foreground bridge supplies it; no connector access means skip cleanup, not assume the old receipt remains valid.
- Identical current payload bytes, file set, snapshot revision, required event prefix and archive manifest. Required post-snapshot events invalidate eligibility. Archive verification/cleanup bookkeeping lives in persistent control state to avoid a self-invalidating payload snapshot.
- Revalidated repository identity, directory descriptors, safe target and granted permissions.

Write a persistent cleanup intent containing snapshot/manifest identity and the exact directory filesystem identity. Rename only that directory to a unique quarantine child in its already-authorized `.control/issues/<ID>/` directory on the same filesystem, then remove its verified regular files without following links. The lock prevents supported attachment during this transition. Quarantine is recoverable state, never another issue. A failed delete retains the intent and remaining bytes; recovery either completes the verified removal or restores the directory. Unexpected files/path swaps stop removal. Do not issue recursive root deletion.

Keep the tombstone, verified archive locator/manifest, terminal disposition, final cleanup result and recovery generation in `.control/issues/<ID>`. Cleanup bookkeeping survives payload removal. Cleaned links may remain dangling until validated reattachment removes/replaces them; they never authorize empty recreation. Startup only performs collection for already eligible candidates with fresh read-back available; otherwise it reports retention and continues the new issue. There is no remote network archive in SessionEnd or recurring job.

`restore` fetches the root and all parts through the same foreground bridge, validates every digest/path, and reconstructs in a staging directory. Reject duplicate paths, traversal, symlinks, unknown schema, incomplete parts and decompression/size abuse (base64 decoding has explicit bounds). Under lock, require the matching tombstone and no newer payload, publish restored data, append a new lifecycle generation and explicitly reopen. Preserve historical terminal evidence; never overwrite it as though the issue had always been active. Provider loss, tampering, missing tombstone or unknown history yields `RECOVERY_REQUIRED`.

## Host adapters

Native host configuration, event translation, trust and host-specific limitations are documented in [Host hooks](host-hooks.md). Adapters reuse the shared lifecycle core; they must not maintain a competing lifecycle or treat another host's JSON schema as native support.

A hook can deny a covered action only when the host actually invokes and honors it. Disabled/untrusted hooks, missing interpreters, timeout, malformed responses, uncovered paths and same-user filesystem access limit enforcement. Installed-host evidence is required separately for each host and version. Protocol fixtures do not establish that coverage or support for another operating system.

### Bootstrap exceptions

An unready session may use only:

1. Host user-input/permission UI needed to supply identity or grant the exact requested paths.
2. The reviewed lifecycle command's exact `diagnose/register/bind/adopt/resume/restore` invocation for its assigned issue and allowlisted arguments. Allow no shell operators, redirection, command substitution, wrapper interpreters, arbitrary files or additional command. Parse a strict argument vector; reject anything the adapter cannot prove is this command.
3. The exact Linear read/save operations bound to a pending immutable archive/recovery request, with checked issue/document ID and expected payload digest. This exception does not allow general provider edits. If nested tool calls conceal those arguments, use an observable direct call or retain the data.

A sealed maintenance binding permits only the exact archive export/read-back/verify/cleanup operations for its pending request, plus explicit rebind. It cannot read arbitrary task notes or resume product actions; reopen first. A new-issue session may collect an older issue only with an explicit maintenance assignment and the same checks. This avoids keeping a payload participant live merely to finish its archival.

No general shell, file read/write, web search, MCP wildcard or “read-only command” exemption. A supported diagnostic path must explain the next action without recursively calling itself through the gate. PermissionRequest must not automatically grant permissions. Static hook control instructions may explain these routes but cannot promote arbitrary task data to instructions.

### Verification boundaries

Local lifecycle checks must cover worktree sharing/isolation, identity/path validation, scoped context, concurrent writes, crash recovery, pending-operation retention, exact archive reconstruction and cleanup/restore races. Host checks must separately establish actual callback delivery, legitimate trust, allowed bootstrap, denied covered actions, async completion and child identity. Provider checks need real save/read-back and reconstruction in addition to local fixtures.

Retain exact candidate, inputs, failures, repairs, reviewer scope and limitations in the delivery record outside product documentation. Human-reviewed publication does not activate policy or replace exact governance authorization. See the [baseline publication rule](../framework/baseline.md#authoring-and-publication-rules).

For commands, recovery and host limits, see [Task-workspace usage](task-workspace-usage.md#usage).
