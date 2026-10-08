# Task-workspace usage

For issue start/resume and assigned steps, follow the [canonical contributor
procedure](contributor-workflow.md). This guide describes lifecycle mechanics;
`active` disposition, present storage and readiness do not approve implementation.
New roadmap templates start **Proposed / awaiting explicit approval**. Attach and
resume preserve existing roadmap bytes, including recorded progress and approval.

## Usage

Complete the [Python and Poetry setup](development.md), then run `make check-local`
from the repository root. Without Make, run `poetry run python scripts/dev.py check-local`.
Tests create temporary non-bare repositories and linked worktrees. They do not clean
or adopt an active task workspace. Python's standard library is the only runtime
dependency. Use the platform filesystem requirements in the
[contract](task-workspace.md#registration-and-validation); native Windows requires local NTFS.

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
   source path. For managed notes use payload-relative locators such as
   `roadmap.md` or `context/<name>.md`. Absolute paths through an issue symlink
   are rejected by the external source reader. Every required file is checked
   again by `ready`. The master explicitly owns coordination; an assigned step
   attaches to its own installed packet using its actual host/session identity.
4. Use `read`, then `acknowledge` with its exact `packet_digest`. This establishes
   delivery facts only. Call `ready` to verify current readiness; the next covered
   tool also checks it. After required source edits, request a coordinator refresh,
   then read and acknowledge the replacement digest before dependent work. Scope replacement
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

Construct bootstrap commands with the shared formatter. It selects the native
Windows encoded request route for Codex/Cursor and preserves POSIX shell quoting
for Unix hosts and Claude's Bash tool. Pass the result through the native host's
[documented tool route](host-hooks.md#setup-and-bootstrap). The core request still
binds the exact worktree, host, session and issue. Wrappers, redirection,
substitutions and unrelated commands are not bootstrap exceptions.

```python
from agent_company.adapters.common import bootstrap_command

command = bootstrap_command(request, host=request["host"])
```

The helper uses this installed checkout's interpreter and entry point. Run it in
the intended worktree's Poetry environment. Do not substitute a global interpreter
or manually rewrite quoting. The Windows entry decodes one bounded canonical
URL-safe base64 JSON request and calls the same lifecycle implementation; encoding
does not confer permission or change the recovery allowlist.

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

Filesystem support and host support are separate checks. Windows uses a native
NTFS implementation; Linux and macOS use the POSIX implementation. A filesystem
test does not demonstrate an installed host's trust or callback behavior. WSL
results establish the tested Linux environment, not native Windows support.

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
