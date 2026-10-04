# Task-workspace contract

**Status: Local implementation and PR corrections are available. Exact-candidate local test and scoped review results are retained in E3/E4; earlier results do not validate changed code. Actual host/provider acceptance, exact-candidate human acceptance and runtime/Security certification remain open. [E3/E4]**

AGENT-1 adds a local workflow guardrail: automatic issue workspace creation, attachment and readiness checks on supported Codex paths. Step 3 implements and tests this contract. It does not implement the Control Plane, authenticated Role authority, complete confidentiality, the paper reference checker or the full launcher.

## Scope and governing inputs

The 2026-10-03 amendment in **SPEC**, current **L1/LM**, and the explicit workspace decisions govern this increment. Their identities and retained bytes are in [the baseline register](baseline.md). **V0** controls interpretation; **V2** supplies repository placement; **V6/RC-07** governs scoped context and verified archival; **RC-09** governs evidence/privacy. This document selects concrete local mechanics under that scope, not new organizational policy.

Additional reading routes, relative to the shared vault:

- `Specifications/Runtime/Context Packages.md` (`GUIDE-CONTEXT`): minimum sufficient inputs, provenance and staged disclosure.
- `Assurance/Conformance/R6-C03.md`, `R6-C04.md`, `R6-C11.md`, `R6-C13.md`: isolation, contract integrity, completion and evidence scenarios. These are defined expectations, not passed tests.

The first supported platform is one local non-bare Git repository and its linked worktrees on macOS. Separate clones, remote hosts, synchronization, exposed services and background scheduling are excluded. Generic task use must not require this team's private vault. A2/R2, 8 provisional AGENT-1 points and 29 milestone points remain the planning baseline. A materially different host architecture requires visible re-estimation. [SPEC, L1, LM]

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

Existing manually shared workspaces, including AGENT-1, require `adopt`, not `create --force`. Under the issue lock, inventory and hash existing bytes, preserve roadmap format/content, assign existing note owners explicitly, and record adoption provenance. Do not synthesize old events. Unexpected files or unresolved ownership stop adoption for review. No startup path empties or reinitializes an existing directory.

## Core interface and state

Implement `operations/memory/task_workspace.py` with a Python standard-library API and matching command interface. The reusable entry point is `execute(request: dict) -> dict`; CLI reads one bounded UTF-8 JSON object from stdin, or accepts exactly one `--request-json` argument, and emits one JSON object to stdout. The argument route makes bootstrap callable without shell redirection. Host-specific input never enters the core unchanged. Command names below are operations in the request, not shell fragments.

Every request has `schema_version: 1`, `operation`, `request_id` and explicit `worktree`. **Bootstrap exception:** `register` takes `main_worktree` and no `repo_id`; it creates or validates registration and returns the acquired `repo_id`. A pre-registration `diagnose` omits `repo_id` and returns `REGISTRATION_REQUIRED` or the stored identity. Every issue operation then supplies `repo_id`, `host`, `session_id`, and applicable `issue_id`, `participant_id`, `binding_generation`, `expected_revision` and operation arguments. IDs are opaque bounded strings except validated issue IDs; filesystem names derived from session IDs use SHA-256, never raw host identifiers. Persist idempotency by request ID plus canonical request digest. Same ID/same request returns the prior result; same ID/different request is rejected. Do not record arbitrary raw request bodies.

Responses contain `ok`, `code`, identifiers, current `revision`, `binding_generation`, allowed reference descriptors and a bounded diagnostic/recovery action. No private note body is returned by status/diagnostic operations. Suggested CLI exits: `0` success, `2` invalid input, `3` conflict/not-ready, `4` I/O/provider/recovery failure. These exits are **not** the Codex hook wire protocol.

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

Terminal recording for completed AGENT-1 requires references to exact human acceptance, actual required merge and completion of issue obligations. The utility validates supplied identities/evidence and provider status; it cannot manufacture or independently grant human acceptance. Terminal state still forbids ordinary task edits until explicit reopen, which invalidates cleanup eligibility. Cancellation preserves its recorded disposition/history. [SPEC, RC-07]

## Ownership, context and participant lifetime

One coordinator owns `roadmap.md`, shared summary and packet assignment. Participants own distinct named notes, recorded in control state, for example `context/step-03.md`. Every note/update carries author/participant, source references and digests, applicability, status, and superseded revision when relevant. No private reasoning is required. Ownership transfers are explicit, revision-checked and recorded.

Packet manifests list reference ID, source locator, content digest/version, authority class, required/optional status, inclusion reason and allowed reader/stage. Required governing sources remain independently retrievable; summaries do not replace them. Refresh allowed references on resume/compaction and after material source changes. A stale required reference denies readiness for affected work. A packet checksum does not prove semantic completeness. [RC-07, GUIDE-CONTEXT]

Fresh-author and independent-review participants receive explicit allowlists. Do not expose the whole roadmap, events stream, archive, upstream author notes or research by default. An authorized factual safety notice can be added with provenance without disclosing upstream conclusions. Hook output contains only fixed control text, safe IDs and packet digests; task text is retrieved as data through scoped reads. Do not interpolate roadmap/note/prompt content into developer-level `additionalContext`.

Host session identifiers are opaque. Root participant identity is `(repo_id, host, session_id, generation)`. A child also needs a verified distinct child identity and its own scope. Parent issue inheritance does not mean parent packet inheritance. Native SubagentStart supplies a parent session ID and child agent ID; the documented PreToolUse shape does not establish that child ID for later calls. **Do not guess the child from cwd, timing, agent type or transcript content.** Step 3 must verify an unambiguous installed-host mapping; see the integration gate below.

`Stop`/Interrupt are observations, not detachment. `SessionEnd` marks a session-end observation but does not prove children or shell processes ended. Track pending supported tool IDs and returned asynchronous execution handles. Explicit detachment requires no outstanding tools/children and a host confirmation or accountable coordinator reconciliation. A missing PID, expired heartbeat or elapsed time is insufficient. Unknown participants retain the issue and prevent cleanup. No recurring liveness service is introduced.

## Coordination and crash recovery

Use a persistent per-issue lock in `.control/issues/<ID>/`, shared by create/attach/update/archive/cleanup. A process-held OS lock (Python `fcntl` on this platform) releases when its process exits; never delete/recreate the lock file to break a lock. Bound waits and return `BUSY`; hooks must return a denial before their own host timeout. Acquire two issue locks for rebind in lexical ID order. Setup registration has its own short-lived setup lock.

Maintain an integer payload revision, binding generations, per-file digests, event sequence/head, ownership, participant records and pending transactions in issue control state. Sessions do not hold locks across model/provider calls. Archive exports are snapshots; provider I/O occurs outside the lock and must be compared again at verification/cleanup.

For a payload mutation, stage the new file plus required event and expected state in a recoverable transaction before publication. Flush staged bytes and intent, then atomically replace the payload and state under lock. On interruption, reconcile old/new digests and transaction ID, finishing exactly once or returning `RECOVERY_REQUIRED`; never report partial state as ready. Test failures at every persistence boundary. This is a local transaction protocol, not an atomic transaction with Linear.

Out-of-band edits are unsupported concurrent writes. Detect manifest mismatch before ready/write/archive/cleanup; preserve changed bytes and return `UNTRACKED_CHANGE`. An explicit coordinator reconciliation imports the inspected change at a new revision. Do not silently overwrite it with template or older staged bytes.

## Events and retention

`events.jsonl` is a **local diagnostic stream**, not the full authority ledger. Write UTF-8 JSON, one complete newline-terminated object per event. The coordinated writer assigns sequence and identifiers. Required workspace-transition events commit with their local operation; optional host observations may fail without stopping unrelated work. [RC-09; SPEC event refinement]

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

**Selected first bridge: foreground Codex connector calls plus deterministic local verification.** Python command hooks have no established access to this task's connector session. Do not invoke imagined Python MCP functions, scrape credentials or start a second Codex model from a hook.

The available tool declarations provide:

- `mcp__codex_apps__linear_save_document({issue, title, content})` to create a document, or `{id, content}` to update it.
- `mcp__codex_apps__linear_get_document({id})` for read-back.
- `mcp__codex_apps__linear_get_issue({id})` and `linear_list_documents` for identity/recovery lookup.

Step 2 successfully read AGENT-1 and the milestone through those connectors. Save is declared callable but **no archive write/read-back round trip was performed**. The command-hook process cannot directly call these model tools. Current official hooks also describe `mcp_tool` handlers, and the installed generated configuration type includes them. That establishes a potential bridge, not a connected server name, sequencing facility or working archive transaction. Concurrent matching hooks cannot implement ordered save/read-back. [H1, E1]

### Concrete archive sequence

1. `archive-prepare` requires coordinator ownership and expected revision. Freeze a snapshot and request ID under lock. For terminal collection, `seal:true` requires all other participants detached and no outstanding ordinary tools/children; it detaches the coordinator from payload work and records that fact before snapshotting. The session keeps a maintenance-only binding for this issue. Include cleanup eligibility/disposition intent in that snapshot before hashing. Do not mark it archived. Return an export file and exact document title/content plus issue UUID; export paths are local data, never executable commands.
2. The foreground coordinator uses the connected Linear tool to save exactly that request into an issue-parented document. Use a title containing issue ID and snapshot digest. Preserve previous verified snapshots; never replace their sole recovery bytes with a partial new version. On uncertain save outcome, search/read matching documents before retrying creation. Do not retry blind and infer which duplicate is authoritative.
3. Read the returned document ID through `linear_get_document`. Pass the observed ID, URL, parent issue identity, provider `updatedAt`, and read-back content to `archive-verify`. A provider response imported by the foreground bridge is attributable evidence, not authenticated runtime authority. Retain its origin/request identity; a hand-edited success flag is not accepted.
4. The verifier parses the structured payload, decodes every file, checks byte lengths/digests, validates the repository/issue/snapshot manifest, and compares against the frozen snapshot. Reacquire the issue lock and require the current payload revision/digests to match. A provider-normalized Markdown wrapper may differ; decoded archived bytes may not.
5. Record the verified locator, provider version, payload digest, manifest digest, event prefix/head, snapshot revision and read-back time outside the disposable directory. This receipt authorizes only the specified local eligibility check; it is not human acceptance. If the receipt/snapshot is stale, keep the workspace.

The archive document contains a readable outcome/goal/constraints/progress/blockers/source/handoff history plus a versioned structured manifest and reconstructable payload. For exactness, encode UTF-8 roadmap/context/event bytes in base64 inside a fenced JSON object. Each entry carries relative path, byte length, SHA-256 and disclosure scope. Digests use original bytes. Sensitive material must be excluded before export, not merely encoded. Research stays at its durable vault source; large external evidence needs a verified durable reference and scope.

Use a conservative 256 KiB export-document limit as a **local implementation limit**, not a claimed Linear limit. Larger payloads require numbered issue documents plus a root manifest of document IDs/digests and read-back of every part. If the provider rejects size/content, preserve local data and return `ARCHIVE_PENDING`; never truncate or silently omit meaningful history. Step 3 must measure actual connector behavior and test multipart reconstruction.

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

## Codex adapter contract

Implement `adapters/openai/task_workspace_hook.py` as a thin JSON stdin/stdout adapter. Configure synchronous project hooks in `.codex/hooks.json`; no background handlers. Resolve the reviewed script from the Git worktree root with safe quoting and run from subdirectories correctly. Trust is required for both the project layer and exact hook definition. Do not install/trust broad global hooks or bypass trust. A changed hook definition requires legitimate review again. [H1]

Use finite adapter deadlines shorter than host deadlines: ordinary hooks at most 5 seconds with a 10-second host timeout; lock waits at most 1 second. SessionEnd/Interrupt do bounded local observations only, within the host's short limit. No provider network work in a hook. Catch expected parsing/I/O/lock errors and emit the event's valid denial. An interpreter crash, missing script, timeout or disabled/untrusted hook cannot be made fail-closed by Python code that never runs.

| Event / inputs | Adapter action and output |
|---|---|
| `SessionStart`: `session_id`, `cwd`, `source` (`startup/resume/clear/compact`) | Restore a known binding and validate scope; initial unknown session stays unbound. Return fixed setup/status text only. For unready or stale state, preserve its precise diagnostic and permit only the documented bootstrap recovery path; ordinary pre-tool denial remains required. Do not stop the lifecycle solely because acknowledgment or resume is needed. Never delete/create a guessed issue. |
| `UserPromptSubmit`: above plus `turn_id`, `prompt` | For an unbound root, parse exactly one standalone `Task: <ID>` line or use pre-established explicit binding. No match stays unbound; multiple distinct/malformed declarations block. Bound sessions need no repeated declaration; a differing declaration requires explicit rebind. Do not search incidental issue mentions or retain the prompt. Return `decision:block`/`reason` on conflicts. |
| `SubagentStart`: parent `session_id`, `agent_id`, `agent_type`, `turn_id` | Inherit issue; attach separate preassigned scope and generation. Missing child mapping/scope marks not-ready. `continue:false` does not stop this event; gate the spawn path beforehand and child tool path afterward only where identity is verified. |
| `PreToolUse`: `session_id`, `cwd`, `turn_id`, `tool_name`, `tool_use_id`, `tool_input` | Resolve verified participant; run `ready` before every covered tool. Deny unknown/stale/mismatched bindings, failed setup, required-source/evidence failure, lock timeout or ambiguous child identity. Record pending supported operation before allow; do not record raw input. |
| `PostToolUse`: tool identity and response | Extract only allowlisted completion/error codes and async handle identifiers; mark pending operations complete where actually complete. Never ingest full response into events. A post-hook cannot undo a tool effect. |
| `PreCompact`, `PostCompact`, compact `SessionStart` | Preserve state and refresh permitted manifest references. Do not regenerate governance from conversation summaries. Missing callbacks cannot erase bindings. |
| `Stop`, `Interrupt`, `SubagentStop`, `SessionEnd` | Record bounded observation and reconcile known operations. Do not infer issue completion or participant retirement from turn stop. No automatic archive/delete and no continuation loop. |

**Pre-tool denial must be valid JSON and exit 0:**

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"TASK_WORKSPACE_NOT_READY: BINDING_MISSING. Supply one Task: ISSUE-ID binding."}}
```

Successful readiness returns `{}` with exit 0, leaving normal tool permissions intact. Never return a blanket permission approval. `PreToolUse` does not support `continue:false`, `stopReason`, `suppressOutput` or `permissionDecision:ask`; those can fail the hook while the tool proceeds. Exit 2/stderr is documented as a blocking route, but use the explicit JSON route consistently. [H1]

Diagnostics distinguish `BINDING_MISSING`, `BINDING_CONFLICT`, `STALE_BINDING`, `SCOPE_MISSING`, `SOURCE_STALE`, `PERMISSION_REQUIRED`, `UNSAFE_PATH`, `BUSY`, `UNTRACKED_CHANGE`, `INTEGRITY_ERROR`, `RECOVERY_REQUIRED`, `ARCHIVE_PENDING` and `HOST_UNSUPPORTED`. Reasons contain safe IDs/codes and exact recovery instructions, never private task contents.

### Bootstrap exceptions

An unready session may use only:

1. Host user-input/permission UI needed to supply identity or grant the exact requested paths.
2. The reviewed lifecycle command's exact `diagnose/register/bind/adopt/resume/restore` invocation for its assigned issue and allowlisted arguments. Allow no shell operators, redirection, command substitution, wrapper interpreters, arbitrary files or additional command. Parse a strict argument vector; reject anything the adapter cannot prove is this command.
3. The exact Linear read/save operations bound to a pending immutable archive/recovery request, with checked issue/document ID and expected payload digest. This exception does not allow general provider edits. If nested tool calls conceal those arguments, use an observable direct call or retain the data.

A sealed maintenance binding permits only the exact archive export/read-back/verify/cleanup operations for its pending request, plus explicit rebind. It cannot read arbitrary task notes or resume product actions; reopen first. A new-issue session may collect an older issue only with an explicit maintenance assignment and the same checks. This avoids keeping a payload participant live merely to finish its archival.

No general shell, file read/write, web search, MCP wildcard or “read-only command” exemption. A supported diagnostic path must explain the next action without recursively calling itself through the gate. PermissionRequest must not automatically grant permissions. Static hook control instructions may explain these routes but cannot promote arbitrary task data to instructions.

### Historical Step 2 feasibility probes, gaps and fallback

| Step 2 evidence as of 2026-10-03 [E1] | What it established then / did not establish |
|---|---|
| Installed `codex-cli 0.155.0-alpha.16.3`; `features list` reports `hooks stable true` | Binary/feature availability. Not that this desktop task loaded or trusted project hooks. |
| `app-server generate-ts --experimental` exposes lifecycle/tool events, command and `mcp_tool` handler types, trust status and `McpServerToolCallParams` | Protocol feasibility. Not runtime event delivery or connector access from a hook. |
| `codex app-server proxy` exits 1: default control socket does not exist | No usable default live-server bridge observed. Do not start a daemon or guess private sockets to bypass this. |
| Linear issue/milestone reads succeeded; save/get document declarations available | Foreground connector route exists; archive creation and exact read-back remain untested. |
| Official documentation [H1] | Trust, event shapes, disabled hooks, failure behavior and coverage. Documentation is not local desktop proof. |

Documented coverage includes Bash/unified exec, apply_patch, MCP and many local functions. Specialized paths may opt out. `write_stdin` does not rerun PreToolUse for a previously allowed command. Thus a shell opened while ready may accept later input without a fresh gate. Track it as live until closed; no rebind or cleanup while unresolved. Do not claim shell-content policy enforcement. Nested code-mode calls, app task tools, collaboration tools and child identity must be measured on this installed desktop.

**Step 3 acceptance gate:** prove real desktop starts, explicit binding, resume, compaction, separate worktree attach and supported tool denial after legitimate hook trust. Prove child identity/scope before accepting native subagent support. Until then deny covered spawning that would create an unidentifiable child; do not claim a nonblocking SubagentStart output prevented it.

If the host cannot supply usable child/tool events, use the delegated simpler-integration fallback: a minimal foreground launcher adapter calling the same core and starting separate explicitly bound Codex sessions with preassigned packets. Keep native subagents disabled on that supported path. This is a concrete fallback to validate, **not an implemented substitute or permission to mark native-subagent acceptance passed**. If hook execution or tool coverage prevents the required guardrail altogether, return the failed evidence to the master, revise the supported entry point/estimate, and validate a minimal launcher gate before proceeding with enforcement claims. Do not silently replace automation with a reminder or build the full product launcher.

Disabled/untrusted hooks, script failures and uncovered tools remain host bypass limits. Same-user direct filesystem access can bypass local ownership/read filters. The supported workflow is cooperative and bounded; neither scoped links nor these hooks prove adversarial isolation. [H1, R6-C03]

## Step 3 implementation and acceptance packet

Owned implementation paths are `.gitignore`, `.codex/hooks.json`, `operations/memory/task_workspace.py`, `adapters/openai/task_workspace_hook.py`, `core/templates/task-workspace/`, `tests/task_workspace/`, plus implementation/evidence updates here. Preserve LICENSE. Keep host-neutral state/provider boundaries; no credentials, personal absolute paths or private-vault prerequisite in product code.

Implement in this order:

1. Registration, validation, adoption, locking and transaction recovery with temporary repositories.
2. Create/attach/resume/binding, ownership and scoped packets; then events/checkpoints.
3. Foreground archive export/read-back verification, cleanup and restore with fake-provider failure injection.
4. Thin adapter and exact bootstrap grammar; then legitimate trusted-host integration and real connector round trip on an authorized disposable fixture.
5. Independent tests/code review and security review of the exact candidate. Repair and repeat affected checks; do not report author's tests as independent review.

Required behavior checks:

| ID | Scenario and observable acceptance |
|---|---|
| T01 | Two processes/worktrees create the same issue concurrently: one canonical payload, both valid views, no lost bytes; different issues never share content/ownership. |
| T02 | Invalid IDs, foreign clone, moved main root, traversal, hard links, symlink swaps and unexpected directories: explicit refusal; outside sentinel files unchanged. |
| T03 | Same-session resume, new same-issue join, rebind and adoption: progress survives, stale generation rejected, no false empty restart. |
| T04 | Concurrent roadmap/note writes with identical old revision: one wins and one conflicts; no lost update. Wrong-owner updates fail. |
| T05 | Failure at transaction staging/flush/rename/state/event boundaries: deterministic recover-or-stop, exactly-once required event, no false readiness. |
| T06 | Concurrent event append, interrupted tail, interior corruption, size cap and synthetic secret markers: complete ordered lines, defined recovery, no forbidden payloads. |
| T07 | Required source stale/missing, packet change, trial/reviewer scopes: affected readiness denied; prohibited notes absent from returned references and hook context. Record physical-access limits. |
| T08 | Active/blocked/in-review/unknown participation, open PR or pending exec: cleanup refuses. Inactive time alone changes nothing. |
| T09 | Archive save failure, uncertain duplicate, mismatched parent, normalization, missing part, wrong digest, changed provider version and changed local bytes: retention; no successful cleanup. |
| T10 | Attach/update racing cleanup; path swap; crash before/after quarantine: lock coordination and tombstone allow correct recovery; never delete another issue or whole root. |
| T11 | Cleaned issue restore, corrupt archive, unavailable provider and repeated restore: verified history/new generation or explicit stop; never empty success. |
| T12 | Actual desktop startup from root/subdirectory, Task binding, missing/conflicting ID, resume/compact, worktree and parent/child mapping: record exact delivered events, outputs and side-effect sentinels. |
| T13 | Covered Bash/patch/MCP/local function/nested code-mode paths; async exec/write_stdin; untrusted/modified/disabled hooks, wrong JSON, timeout, missing interpreter and thrown error: record which calls were blocked and which ran. Expected host failure-open cases are limitations, never passes for fail-closed claims. |
| T14 | Exact bootstrap operations pass; shell metacharacters/wrappers/mixed commands/provider substitution fail. No broad permission approval. |
| T15 | Real foreground Linear archive save + independent get, exact reconstruction and restore. Verify document parent/identity and multipart handling. A mock alone cannot pass provider integration. |
| T16 | Tracked reusable assets remain trackable; `.task` and narrow OS metadata ignore patterns behave in root/nested examples. Product/config files remain eligible. |

Retain candidate SHA, platform/binary version, trusted hook hash, supported-tool matrix, fixture inputs, exact allowed packets, results, failures, repairs and limitations. Independent Test Developer/Code Reviewer and Internal Security coverage are required for executable behavior; add Product Security for shipped framework behavior. Exposed services remain excluded; introducing them changes coverage. These labels refer to required assurance subjects, not fabricated Role execution receipts. [SPEC, LM, V8–V11]

Human-reviewed PR into main remains mandatory. [PR #1](https://github.com/njalexander93/agent-company/pull/1) is published. The active main ruleset was inspected; classic branch-protection visibility remains restricted. Recheck actual candidate-specific gates at publication and before merge. This contract, local tests, provider writes and later merge do not activate policy or replace exact-candidate human acceptance. [R23; shared-vault `Assurance/AGENT-1 Publication Evidence.md`]

## Evidence locators

- **H1:** [Official OpenAI Hooks documentation](https://learn.chatgpt.com/docs/hooks), fetched 2026-10-03: trust/configuration, tool coverage, common/event inputs/outputs, MCP hooks and failure limitations.
- **E1 (historical Step 2):** Shared-vault archive `Sources/Task Context/2026-10-03 - AGENT-1 Step 02 Evidence.zip`, SHA-256 `4ce113d1760d3d792e9d92e769eb572690c026be82ce82e127b961f821c6efbd`. Members `step-02.md`, `proxy.txt`, `protocol/` and `retention-manifest.json` retain the original contract handoff, failed proxy output and generated schemas. These are feasibility evidence for contract commit `7a9f8dc`, not final32 runtime acceptance. Archive retrieval and every retained member digest were verified after the temporary handoff was consumed.
- **E2 (historical implementation/provider evidence):** Shared-vault note `Assurance/AGENT-1 Step 03 Implementation Evidence.md` and archive `Sources/Task Context/2026-10-03 - AGENT-1 Step 03 Evidence.zip`, SHA-256 `65a0ad53ce21a29eab36120f0bfb25ae4b5798152cc9f8a72524d6f932a58df3`. The source candidate is `119896b`; its retained real provider fixture predates the first frozen implementation. Neither is final32 provider acceptance.
- **E3 (final32 local evidence):** Shared-vault note `Assurance/AGENT-1 Step 03 Rollover Evidence.md` and archive `Sources/Task Context/2026-10-03 - AGENT-1 Step 03 Rollover Evidence.zip`, SHA-256 `ac98dd347fbb36b546dc85c8ca3fe83387950d567a96f2ba54df630ceacafa16`. Members `candidate-hashes.json`, `independent-tests.txt`, `reviews.md` and `current-handoff.md` identify `32b5f2d5b637c9cd2edd18011306fa775b5c8ef8`, 54 passing tests and scoped review dispositions. The archive is retained and readable; consumed task handoffs are not required for retrieval.
- **E4 (PR review corrections):** Shared-vault note `Assurance/AGENT-1 PR Review Corrections.md` retains the startup/compaction recovery, async completion and Finder metadata findings, exact repaired candidates, failed/passing regressions, independent review and integration results. It also records the documentation-only equivalence checks and the separate archive-fixture repair. These changed core/adapter bytes require fresh host validation; E3 and the earlier isolated host fixture do not establish it.
- **L1/LM, SPEC, V0/V2/V6/V8–V11, RC-09:** [baseline source register](baseline.md), including retained Step 1 archive identities. Supporting sources remain in the shared vault; this document does not duplicate their authority.


## Candidate usage and measured capabilities

Complete the [Python 3.14.8 and Poetry setup](development.md), then run `make check`
from the repository root. Tests create temporary non-bare repositories and linked worktrees. They do
not clean or adopt this active issue. Python's standard library is the only
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

For a bootstrap command, construct the exact argument vector below with
`shlex.join`; pass it directly to Bash/unified exec with explicit `login:false` and `shell:"/bin/sh"`. Omitted login or another shell is denied.
The adapter compares that canonical shell spelling and checks the embedded
worktree, session and issue. Shell wrappers, redirection, additional commands,
substitution and alternate interpreters are rejected.

```python
python_path = str(Path(absolute_repository_root) / ".venv" / "bin" / "python")
argv = [python_path, absolute_core_path, "--request-json", json.dumps(request)]
command = shlex.join(argv)
```

The startup prompt accepts exactly one standalone `Task: ISSUE-ID` line. It
records only that identity. A packet/coordinator assignment is still explicit;
no startup callback infers one from a chat title or grants coordinator ownership.
One-time registration can include an explicit `startup` assignment with exactly
`issue_id`, `issue_uuid`, `coordinator` and `packet`. The next matching Task prompt
automatically creates the workspace and installs that preassigned packet. A new
session joining an existing issue automatically attaches only when the coordinator
already assigned it a packet. No hidden packet or coordinator role is inferred.
Automatic setup is tested through direct adapter calls; desktop delivery remains
an acceptance gate. The foreground-session fallback
uses the same explicit bindings and packet checks; no full launcher is shipped.
Native child scope is not accepted. Covered native spawning is denied by the
adapter until an unambiguous child-to-tool identity is demonstrated.

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

### Host acceptance status

The adapter's direct subprocess tests exercise the actual JSON wire entry point
and strict CLI gate. They do **not** establish that the desktop loaded or trusted
these hooks. `.codex/hooks.json` is a synchronous project candidate; no global hook
settings or hook-trust bypass is used. Review/trust is still required for the
exact project candidate before a real desktop side-effect test.

| Surface | Current evidence / limitation |
| --- | --- |
| Core and direct adapter | Temporary-repository tests cover sharing, fencing, source scopes, recovery, provider fixtures, retention and bootstrap denial. |
| Bash/patch/MCP/local functions | Adapter emits documented explicit PreToolUse denial. Installed desktop callback delivery remains unverified. |
| Native children/task tools | No verified child-to-tool identity; covered spawning is denied. This does not prove uncovered paths are stopped. |
| Nested code-mode tools | No installed desktop trace. Concealed provider arguments do not receive archive exceptions. |
| Async exec / `write_stdin` | Pending handles retain participation; unknown completion stays pending. Official docs say later stdin does not get a fresh pre-hook. |
| Disabled/untrusted/modified hooks | Official docs say hooks are skipped; no fail-closed claim. |
| Missing interpreter, timeout, malformed output, thrown process error | Host can fail open. Python cannot deny when it never runs. Direct adapter catches expected failures, which is a narrower fact. |
| Stop/Interrupt/SessionEnd | Bounded optional observations; no inferred terminal state or participant retirement, provider work or deletion. |

T12/T13 require actual desktop evidence, not simulated event objects. T15 requires
real save/get/reconstruction evidence in addition to provider fixtures. Independent
security review and exact-candidate human acceptance remain separate gates.

OS ignore references: GitHub's maintained [macOS](https://github.com/github/gitignore/blob/main/Global/macOS.gitignore),
[Windows](https://github.com/github/gitignore/blob/main/Global/Windows.gitignore), and
[Linux](https://github.com/github/gitignore/blob/main/Global/Linux.gitignore) templates.
The selected subset excludes broad installer, shortcut, backup and hidden-file
patterns. Tests confirm root/nested metadata ignores and product/config eligibility.


### Historical implementation and provider evidence [E2]

The first frozen candidate was `cea8b17b0dc53f6b68aec7090ded5e11dc766bca`.
Independent test work added 11 tests and found two defects: interrupted recovery
could overwrite changed bytes when the roadmap disappeared, and optional event
loss was not counted at the size cap. Both were reproduced and repaired before
the first commit. Later Standards, Spec and Internal Security reviews found
checkpoint/provenance archive loss, rebind idempotency, permission diagnostics,
automatic entry, completion validation, overly broad bootstrap, external-edit
reconciliation, lifecycle observations, shell options and cleaned-archive reads.
Author regression tests cover the repairs; final independent disposition belongs
with the exact final candidate, not this initial report.

A pre-first-commit development encoder used a real foreground Linear fixture in an isolated temporary repository, issue
parent `AGENT-1` resolved by `linear_get_issue` to its immutable UUID, two numbered
parts and a root index. Independent `linear_get_document` calls returned the same
structured bytes despite Markdown heading normalization and removal of a trailing
newline. Save responses were truncated and therefore were not used as read-back.
The provider returned an issue identifier, not its UUID; the foreground bridge
retained the explicit get-issue mapping. `updatedAt` differed between save and get,
so the index used the independent get versions. Fresh reads before cleanup matched.
Local cleanup and restore preserved the original roadmap SHA-256.

Retained disposable test documents (not the live roadmap or issue completion):

- [Part 1](https://linear.app/ne3ko93/document/disposable-test-evidence-agent-1-snapshot-e2724ca69d3c)
- [Part 2](https://linear.app/ne3ko93/document/disposable-test-evidence-agent-1-snapshot-eed143b35bd5)
- [Index](https://linear.app/ne3ko93/document/disposable-test-evidence-agent-1-snapshot-460c0d944e2c)

That fixture deliberately used a 4 KiB raw chunk to exercise multipart save/get;
it does not measure Linear's maximum document size. The production default is
64 KiB and the document cap is local. No uncertain-save response or provider
size rejection was induced on the real service; those retention cases use local
fixtures and remain explicitly distinct from provider behavior.

A read-only `codex app-server --stdio` hooks-list probe failed before initialization:
`failed to initialize sqlite state runtime under .../.codex`. The sandbox did not
grant global state writes. No hook trust was changed, no daemon started, and no
host callback was observed. This failed probe establishes no desktop gate coverage.


The subsequent frozen candidate `3d1f8fac3a7d44c0b3e635ed14a5f1935cdf4cb8`
passed 35 tests independently in the Spec and Internal Security reviews. Those
reviews then reproduced unfinished startup packet recovery, terminal reconciliation
and unreachable recovery commands. Later repairs add direct adapter regressions
for those cases. A Product Security review of `cea8b17` confirmed a 278-part export
was refused by the old 258-observation verifier limit; the limit now matches the
32 MiB export bound. Export idempotency retains a reference to immutable snapshot-addressed local
export bytes, not repeated response bodies in state; transactions are size-checked
before publication. These intermediate reviews are historical; final32's bounded
local Product Security assessment is complete with no unresolved concrete finding.
The final32 evidence records Standards, scoped Spec and Internal Security reviews
alongside 54 passing independent tests. This local review completion does not
establish live host/provider acceptance, human acceptance or runtime/Security
certification. [E3]

A second disposable provider upload using readable-history fragments was rejected
by automatic approval review. It produced no provider document. The earlier links
above therefore validate the earlier encoder and compatible reconstruction, not
the final encoder's provider round trip. No retry or indirect upload was attempted.
Approval for the exact stable synthetic export must precede that remaining test.


The unchanged pending `119896b` unsegmented export was regenerated and parsed
locally by final32 with exact snapshot/part/file digests. That compatibility check
uses synthetic observations and is not a provider round trip. Final32's segmented
archives add retained event files and receipt lineage; that live provider case
remains separately unverified. [E3]


### PR review recovery and metadata corrections

SessionStart, PreCompact and PostCompact retain precise readiness/error codes in
bounded recovery context. They do not terminate the session merely because it
needs registration, resume, scope repair or acknowledgment. No readiness is granted:
ordinary PreToolUse calls remain denied, and the existing exact bootstrap and
provider exceptions still apply.

PostToolUse settles pending work only from typed completion evidence: an integer
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

At the issue root and directly inside `context/`, inventory excludes only regular,
owned, no-follow validated `.DS_Store` and `._*` files with a nonempty suffix.
These files remain subject to ordinary file safety/size checks; links, hard links,
directories and other dotfiles are not exempt. They are not task archive content.
Verified issue cleanup validates the entire remaining tree before unlinking those
narrow metadata entries alongside the verified payload. It never recursively
removes a metadata directory or follows a metadata link.

These repairs change core/adapter bytes. The earlier final32 test count and host
fixture identity do not establish acceptance of the new candidate. Actual host,
provider, native-child and human acceptance remain open.
