# Task-workspace contract

**Local workflow guardrail.** The utility manages issue workspace creation, attachment, scoped readiness, archival and recovery. Installed-host trust/callback coverage and provider acceptance require separate evidence; protocol tests are not runtime or Security certification.

Host adapters call a shared lifecycle core for supported local task actions. It does not implement the Control Plane, authenticated Role authority, complete confidentiality, the paper reference checker or the full launcher.

## Scope and governing inputs

**V0** controls governing interpretation; **V2** supplies repository placement; **V6/RC-07** governs scoped context and verified archival; **RC-09** governs evidence/privacy. Their direct records are in the [baseline register](baseline.md#governing-source-register). This document defines local mechanics, not new organizational authority.

Additional reading routes, relative to the shared vault:

- `Specifications/Runtime/Context Packages.md` (`GUIDE-CONTEXT`): minimum sufficient inputs, provenance and staged disclosure.
- `Assurance/Conformance/R6-C03.md`, `R6-C04.md`, `R6-C11.md`, `R6-C13.md`: isolation, contract integrity, completion and evidence scenarios. These are defined expectations, not passed tests.

The first supported platform is one local non-bare Git repository and its linked worktrees on macOS. Separate clones, remote hosts, synchronization, exposed services and background scheduling are excluded. Generic task use must not require this team's private vault. A different host/platform architecture needs its own scope and validation.

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

Packet manifests list reference ID, source locator, content digest/version, authority class, required/optional status, inclusion reason and allowed reader/stage. Required governing sources remain independently retrievable; summaries do not replace them. Refresh allowed references on resume/compaction and after material source changes. A stale required reference denies readiness for affected work. A packet checksum does not prove semantic completeness. [RC-07, GUIDE-CONTEXT]

Fresh-author and independent-review participants receive explicit allowlists. Do not expose the whole roadmap, events stream, archive, upstream author notes or research by default. An authorized factual safety notice can be added with provenance without disclosing upstream conclusions. Hook output contains only fixed control text, safe IDs and packet digests; task text is retrieved as data through scoped reads. Do not interpolate roadmap/note/prompt content into developer-level `additionalContext`.

Host session identifiers are opaque. Root participant identity is `(repo_id, host, session_id, generation)`. A child also needs a verified distinct child identity and its own scope. Parent issue inheritance does not mean parent packet inheritance. A child needs an unambiguous host-to-tool identity mapping before its scope can be trusted. Do not infer it from cwd, timing, agent type or transcript content. See the [host guide](host-hooks.md) for per-host child and coverage limitations.

`Stop`/Interrupt are observations, not detachment. `SessionEnd` marks a session-end observation but does not prove children or shell processes ended. Track pending supported tool IDs and returned asynchronous execution handles. Explicit detachment requires no outstanding tools/children and a host confirmation or accountable coordinator reconciliation. A missing PID, expired heartbeat or elapsed time is insufficient. Unknown participants retain the issue and prevent cleanup. No recurring liveness service is introduced.

## Coordination and crash recovery

Use a persistent per-issue lock in `.control/issues/<ID>/`, shared by create/attach/update/archive/cleanup. A process-held OS lock (Python `fcntl` on this platform) releases when its process exits; never delete/recreate the lock file to break a lock. Bound waits and return `BUSY`; hooks must return a denial before their own host timeout. Acquire two issue locks for rebind in lexical ID order. Setup registration has its own short-lived setup lock.

Maintain an integer payload revision, binding generations, per-file digests, event sequence/head, ownership, participant records and pending transactions in issue control state. Sessions do not hold locks across model/provider calls. Archive exports are snapshots; provider I/O occurs outside the lock and must be compared again at verification/cleanup.

For a payload mutation, stage the new file plus required event and expected state in a recoverable transaction before publication. Flush staged bytes and intent, then atomically replace the payload and state under lock. On interruption, reconcile old/new digests and transaction ID, finishing exactly once or returning `RECOVERY_REQUIRED`; never report partial state as ready. Test failures at every persistence boundary. This is a local transaction protocol, not an atomic transaction with Linear.

Out-of-band edits are unsupported concurrent writes. Detect manifest mismatch before ready/write/archive/cleanup; preserve changed bytes and return `UNTRACKED_CHANGE`. An explicit coordinator reconciliation imports the inspected change at a new revision. Do not silently overwrite it with template or older staged bytes.

## Events and retention

`events.jsonl` is a **local diagnostic stream**, not the full authority ledger. Write UTF-8 JSON, one complete newline-terminated object per event. The coordinated writer assigns sequence and identifiers. Required workspace-transition events commit with their local operation; optional host observations may fail without stopping unrelated work. [RC-09]

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

The archive document contains a readable outcome/goal/constraints/progress/blockers/source/handoff history plus a versioned structured manifest and reconstructable payload. For exactness, encode UTF-8 roadmap/context/event bytes in base64 inside a fenced JSON object. Each entry carries relative path, byte length, SHA-256 and disclosure scope. Digests use original bytes. Sensitive material must be excluded before export, not merely encoded. Research stays at its durable vault source; large external evidence needs a verified durable reference and scope.

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

Retain exact candidate, inputs, failures, repairs, reviewer scope and limitations in the delivery record outside product documentation. Human-reviewed publication does not activate policy or replace exact governance authorization. See the [baseline publication rule](baseline.md#confirmed-authoring-and-publication-decisions).

## Usage

Complete the [Python and Poetry setup](development.md), then run `make check`
from the repository root. Tests create temporary non-bare repositories and linked worktrees. They do
not clean or adopt an active task workspace. Python's standard library is the only
runtime dependency. The first platform remains macOS/POSIX (`fcntl` and directory
file descriptors); Windows ignore rules do not imply a Windows runtime port.

### Explicit setup and bootstrap

1. Call `register` with the caller `worktree` and its explicit Git main worktree.
   Use its returned repository UUID on subsequent requests. Registration checks
   common-Git identity and worktree membership. A copied registration in a foreign
   clone is rejected. Registration never scans or adopts task notes.
2. Name the foreground `host` and `session_id`. The participant key is SHA-256 of
   canonical JSON `[host, session_id]`; `participant_key(request)` computes it.
   `create` explicitly sets `coordinator` to this key and supplies `issue_uuid`.
   Existing manual data requires `adopt`, exact `inventory` hashes, an `owners`
   map for every Markdown file and `evidence`. It preserves existing bytes and
   records only the actual adoption event.
3. A coordinator installs a packet using `scope`, `target_participant`, `packet`
   and optional `owned_paths`. Each packet entry has `id`, `locator`, `sha256`,
   `required`, `authority`, `reason`, `stage`, and `reader`. `reader` is the exact
   participant key. A locator is a safe payload path or an explicit absolute
   source path. Every required file is checked again by `ready`.
4. Use `read`, then `acknowledge` with its exact `packet_digest`. This establishes
   delivery facts only. The next covered tool checks readiness. Scope replacement
   invalidates acknowledgment even if the packet text happens to be unchanged.
5. Updates require the returned `binding_generation`, current `expected_revision`,
   `path`, `old_digest`, UTF-8 `content`, and `provenance` containing `sources`,
   `applicability`, and `status`. Evidence lists use `id`, `locator`, `sha256`.
   No source path or digest is treated as human approval by itself. A `completed`
   outcome additionally verifies local evidence-file bytes for human acceptance,
   merge and obligations, and requires an attributable `linear_get_issue` status
   observation with the matching issue UUID, completed state and completion time.
   This checks supplied evidence; it does not independently authenticate a human
   decision or provider response. Cancellation records an explicit separate outcome.

Construct the core CLI argument vector below with `shlex.join`. Pass it through the native host's documented bootstrap tool route in the [host guide](host-hooks.md). That guide owns shell/tool field requirements; the core request still binds the exact worktree, host, session and issue. Wrappers, redirection, substitutions and unrelated commands are not bootstrap exceptions.

```python
repository_root = Path(absolute_repository_root)
python_path = str(repository_root / ".venv" / "bin" / "python")
absolute_core_path = str(repository_root / "src/agent_company/lifecycle/task_workspace.py")
argv = [python_path, absolute_core_path, "--request-json", json.dumps(request)]
command = shlex.join(argv)
```

The startup prompt accepts exactly one standalone `Task: <issue-id>` line, for example `Task: ISSUE-1`. It
records only that identity. A packet/coordinator assignment is still explicit;
no startup callback infers one from a chat title or grants coordinator ownership.
One-time registration can include an explicit `startup` assignment with exactly
`issue_id`, `issue_uuid`, `coordinator` and `packet`. The next matching Task prompt
automatically creates the workspace and installs that preassigned packet. A new
session joining an existing issue automatically attaches only when the coordinator
already assigned it a packet. No hidden packet or coordinator role is inferred.
Native adapters use explicit bindings and packets; installed-host callback delivery must be verified separately. No full launcher is shipped. See the [host guide](host-hooks.md) for child/spawn restrictions; no unverified child identity may inherit readiness.

The pre-readiness command allowlist is operation-specific. It includes the
original `diagnose/register/bind/adopt/resume/restore` routes plus scoped `read` and
exact-digest `acknowledge`, needed to establish readiness without a general tool
exemption. A maintenance binding permits the specified archive/index/read-back and
cleanup operations. Narrow schemas also permit terminal `archive-prepare`,
coordinator `reconcile-files`, and explicit `rebind`: requiring ordinary readiness
for those repair operations would deadlock recovery. Core ownership, generation,
revision and pending-operation checks still apply. `create` and `scope` are not
unready-session exceptions; the
explicit startup assignment supplies them automatically. Ready sessions can call
the reviewed lifecycle CLI without registering that same local transaction as a
pending external tool. Unknown bootstrap fields are rejected.

`rebind` takes the old issue/generation, `new_issue_id`, optional
`new_binding_generation`, and evidence. The target must already exist with an
explicit assignment. It locks both issues in lexical order, fences old work and
preserves the old payload. Pending operations block it. It does not create a
new assignment or transfer coordinator ownership implicitly.

### Transactions, events and collection

The candidate uses one recoverable intent per payload mutation. It flushes intent,
rolls forward only files matching an old or intended digest, publishes state, and
removes the intent. A retry with the same request ID and identical request returns
the committed result. A changed request with the same ID conflicts, including rebind retries. Unknown
changes stop recovery. `reconcile-files` requires coordinator ownership, expected
revision, exact inspected inventory and evidence. It imports changed Markdown at a
new revision; it cannot remove files or rewrite event history. Initial creation is not ready until its complete state and
required event have committed, even if a crash leaves partial files visible.

Event bodies are fixed lifecycle facts. The `event` operation accepts only
`check`, `tool-start`, `tool-complete`, or `observation`, and an optional typed
`code` (`OK`, `FAILED`, `UNKNOWN`, `INTERRUPTED`); it rejects free-form summaries.
Only `observation` is optional. Near the 16 MiB cap it is suppressed with an
aggregate counter. Ordinary required writes reserve 8 KiB of the 16 MiB active
stream limit for an `archive-prepare` checkpoint event. Each admitted pending tool
also reserves two maximum-sized events for its async transition and completion
(one after the transition). Terminal outcomes require no pending tools. Repeated identical async transitions add no event;
a changed handle conflicts. All other writes respect these reservations; exhausted writes return
`ARCHIVE_PENDING` without changing bytes. A coordinator freezes and verifies a
foreground archive, then calls `event-rollover` with the current expected revision.
The core requires that verified snapshot to match the current complete inventory,
with no pending tools. It retains the exact stream as
`events-<12-digit-first>-<12-digit-last>.jsonl`, resets `events.jsonl`, and appends
the globally chained rollover event in one recoverable transaction. A retry is
idempotent. Optional observations never trigger rotation or external calls.

Segments retain their sequence ranges, head/file digests and provider receipt in
`event_segments`. All segments remain local until eligible verified cleanup.
Readers validate segments in sequence and then the active stream. Reconciliation
cannot rewrite either. Subsequent exports include the segment bytes and receipt
lineage; cleanup and restore cover the whole inventory. The archive schema gains
an optional `event_segments` field only for segmented stores; unsegmented encoder
output shape is unchanged. Total local archive/state limits still apply and can
stop further work with retention. This is bounded retention, not unlimited logging.
An older store already beyond the new reserved threshold may require explicit
capacity recovery; no committed history is truncated to manufacture space.

An interrupted tail can be quarantined only when removing it reproduces the
exact committed manifest; a changed committed prefix is never truncated.
Supported API writes atomically replace the complete event file under the issue
lock, so required-event recovery uses the same transaction as payload changes.
The stream and archive remain coordinator-only unless explicitly in a packet.
Same-user physical file access remains outside this cooperative scope boundary.

`cleanup_candidates` on a new `create` is an explicit list of complete cleanup
requests for separately bound maintenance sessions. Each requires fresh provider
observations for its own cleanup challenge. No directory scan, age cutoff, global
sweep or automatic network request occurs. Inaccessible/ineligible candidates
return retention diagnostics while the new issue remains created.

### Foreground archive bridge

`archive-prepare` freezes the post-event snapshot. `seal:true` retires payload
participation only for an evidenced terminal issue with no other live participants
or pending operations. It returns immutable numbered `parts` with exact `issue`,
`title` and `content` arguments for the foreground Linear connector. Each part has
a readable history fragment plus reconstructable bytes. The default raw chunk is
64 KiB. The 256 KiB document and 32 MiB structured archive caps are **local bounds**,
not measured Linear service limits.

1. Save each exact part through the foreground connector. The covered adapter
   records a save attempt before allowing it. An uncertain save cannot be blindly
   retried. Locate/read the matching issue document through the foreground recovery
   route; ambiguous duplicates retain data.
2. Register the returned ID with `archive-observe-save` using `document_id` and
   `content_digest` (SHA-256 of the exact requested Markdown). This enables only
   that document's direct `linear_get_document` through the maintenance gate.
3. Pass independent get results to `archive-index` as `observations`. Each contains
   `id`, `url`, immutable parent `issue` UUID, `updatedAt`, `content`,
   `origin: linear_get_document`, and the bridge's `request_id`. When Linear returns
   only an issue identifier, resolve it through an actual issue read and retain
   that mapping as evidence. Do not fabricate a UUID from the identifier.
4. Save/get the returned index, then pass the index and every part to
   `archive-verify`. Decoded bytes, ordered part identities, versions, event chain,
   repository/issue and current local revision must all match. Normalized Markdown
   outside the structured JSON is permitted. Imported foreground observations are
   attributable local evidence, not cryptographically authenticated provider facts.
5. `cleanup-plan` returns a new `cleanup_challenge`. Perform new get calls for the
   index and all parts. Set those observations' `request_id` to that challenge.
   `cleanup-commit` requires them, unchanged provider versions and local bytes,
   terminal evidence, and no live/unknown participant or operation.
6. After cleanup, get the same verified documents and use `restore`. The matching
   tombstone is mandatory. Restore preserves historical outcome evidence, appends
   a new generation and reopens explicitly. Missing or corrupt archives never
   produce an empty successful workspace.

Cleanup prunes export bodies and old idempotency response bodies from control
state. The manifest, archive locator/version, ownership, participant fencing and
tombstone survive. Cleanup recovery resumes only the exact verified quarantine
intent. It never recursively removes the task root.

### Host limitations

Follow the [host guide](host-hooks.md) for native configuration, event coverage and trust. Direct adapter subprocess tests do not prove that an installed host loaded the candidate or invoked its callbacks. Child identity, async association and provider observations must be demonstrated on the actual supported path; otherwise retain explicit denial or uncertainty.

OS metadata ignores do not imply runtime support on those operating systems. The utility uses macOS/POSIX filesystem mechanisms; other operating systems require their own implementation and validation.

### Recovery and completion semantics

Codex SessionStart, PreCompact and PostCompact retain precise readiness/error codes in
bounded recovery context. They do not terminate the session merely because it
needs registration, resume, scope repair or acknowledgment. No readiness is granted:
ordinary PreToolUse calls remain denied, and the existing exact bootstrap and
provider exceptions still apply.

Codex PostToolUse settles pending work only from typed completion evidence: an integer
exit code, a boolean MCP/local-tool `isError` result, or a shell error without an
async handle. Null/string metadata does not establish completion; a successful
shell wrapper with an async handle remains pending. A final response may retain
its session handle: completion then takes precedence only after handle validation.
`write_stdin` observations associate through a unique handle within the same
participant. Missing, malformed, conflicting or ambiguous associations retain work. If the host
emits a polling PreToolUse, its transport is recorded separately with a validated
parent relationship. PostToolUse retires that observed transport, while only
explicit completion retires the original process; nonfinal and concurrent polls
cannot strand transport records or settle an unrelated process. Distinct
observations use distinct idempotency keys, while exact repeats remain idempotent.
Pending-operation event reservations remain in force.

Claude Code and Cursor use their own native event and completion contracts in the
[host guide](host-hooks.md). Claude compaction hooks record observations only;
they do not deliver recovery context or grant readiness. Supported startup and
pre-tool responses carry recovery diagnostics. Do not apply Codex's response
fields or completion rules to another host.

At the issue root and directly inside `context/`, inventory excludes only regular,
owned, no-follow validated `.DS_Store` and `._*` files with a nonempty suffix.
These files remain subject to ordinary file safety/size checks; links, hard links,
directories and other dotfiles are not exempt. They are not task archive content.
Verified issue cleanup validates the entire remaining tree before unlinking those
narrow metadata entries alongside the verified payload. It never recursively
removes a metadata directory or follows a metadata link.

Host, provider and native-child behavior requires exact-version validation. Local checks do not authenticate a human decision or prove runtime enforcement.
