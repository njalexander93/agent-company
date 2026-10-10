# Local host hooks

The lifecycle and ticket-first startup contract are runtime-neutral. This checkout
implements three native adapters over that shared core:

| Host | Project configuration | Python entry | Participant host |
| --- | --- | --- | --- |
| Codex | `.codex/hooks.json` | `agent_company.adapters.codex` | `codex` |
| Claude Code | `.claude/settings.json` | `agent_company.adapters.claude` | `claude-code` |
| Cursor Agent | `.cursor/hooks.json` | `agent_company.adapters.cursor` | `cursor` |

The integration targets local macOS, Linux and native Windows checkouts with an
editable Poetry installation. Native Windows requires Git for Windows and local
NTFS storage. Generic Claude chat, Cowork, cloud/remote agents and Cursor Tab are
not covered. Platform tests and installed-host acceptance are separate requirements. Repository hooks are cooperative guardrails, not a sandbox or execution authority.
A successful local protocol test does not prove that a host loaded or trusted its configuration.

## Native protocols and failure semantics

Ticket-read protocol sources checked on 2026-10-09; launcher and shell references checked on 2026-10-07:

- [Claude Code hook reference](https://code.claude.com/docs/en/hooks): stdin JSON carries
  `session_id`, `cwd`, and `hook_event_name`; tool events add `tool_use_id`, `tool_name`, and
  `tool_input`. `PostToolUse` reports successful execution with `tool_response`;
  `PostToolUseFailure` carries `error` and interruption information. Pre-tool denial uses
  `hookSpecificOutput.permissionDecision`; an empty decision preserves normal permission
  checks. `UserPromptSubmit` can return `decision: "block"`. Exit 2 blocks supported blocking
  events. Other errors, malformed responses, or host timeouts can be non-blocking.
  See [input](https://code.claude.com/docs/en/hooks#common-input-fields),
  [pre-tool decisions](https://code.claude.com/docs/en/hooks#pretooluse-decision-control), and
  [exit semantics](https://code.claude.com/docs/en/hooks#exit-code-output).
- [Cursor hook reference](https://prod.cursor.com/docs/hooks): `conversation_id` is stable
  across turns; `generation_id` is not. Generic `preToolUse`/`postToolUse` carry the actual
  `tool_use_id`. `postToolUse.tool_output` is a JSON string; the documented Shell example
  contains `exitCode`. Admission uses `permission`; prompt submission uses `continue`.
  Session start and compaction cannot enforce blocking. `failClosed: true` blocks hook
  failures; exit 2 also blocks. Other failure exits otherwise fail open. Project hooks run
  from the project root. See [configuration](https://prod.cursor.com/docs/hooks#configuration),
  [events](https://prod.cursor.com/docs/hooks#hook-events), and
  [failure options](https://prod.cursor.com/docs/hooks#per-script-configuration-options).

The implementation's choices below are intentionally narrower than those host protocols.

## Setup and bootstrap

For issue sequencing, approval and assigned step threads, use the
[canonical contributor procedure](contributor-workflow.md). Hook setup supplies
local lifecycle integration, not proposal approval.

1. Install this checkout's editable package and locked tools with Poetry. Hooks
   use a fixed invocation-local Git alias to run `adapters/launch.sh` from the
   repository root, including when the host starts in a nested directory.
   The launcher selects `.venv/bin/python` on POSIX or
   `.venv/Scripts/python.exe` under Git for Windows, then the selected host adapter.
   Missing setup exits 2. There is no global interpreter fallback or persistent
   Git alias. See [Git shell-alias behavior](https://git-scm.com/docs/git-config#Documentation/git-config.txt-alias).
2. Review and activate project hooks using the host's own controls. This change does not alter
   global settings or grant trust. Keep existing permission prompts and restrictions enabled.
   Record loading/activation evidence separately from observed callbacks. An enabled
   definition alone is insufficient evidence of trusted execution. If trust or callback
   delivery is unverified, retain that limit; a successful manual lifecycle call does
   not demonstrate a native callback or identify a runtime defect.
3. Submit exactly one `Task: ISSUE-ID` line. The selected adapter records the
   identifier without requiring local registration. Read that exact Linear ticket
   through the runtime's supported provider integration. The adapter admits this
   narrow read before readiness and verifies its native completion. See
   [ticket-read protocols](#ticket-read-protocols). A new Task line fences ordinary
   tools even when the session was previously ready. While that read is pending,
   Claude Code also admits a read-only preparation phase: `ToolSearch` loading
   only configured `get_issue` schemas, and `Read` or a plain `cat` of exactly
   `<checkout>/docs/runtime/contributor-workflow.md` or `<checkout>/AGENTS.md`.
   These calls get no lifecycle decision and write nothing, so the selected
   ticket read remains the first issue-provider operation. A session already bound
   to another issue may submit a Task line too: the adapter records the new issue as
   a pending assignment and the binding stays on the old issue until the verified
   read moves it (step 5). If that read or the move fails, the old binding remains;
   a Task line for the bound issue returns to it and drops the unfinished switch's
   lookup correlation. The legacy preassigned attach route still refuses a switch
   with `BINDING_CONFLICT`.
4. Recover with `agent_company.adapters.common.bootstrap_command(request, host)`
   from the intended worktree's Poetry environment. On POSIX and Claude's Bash
   tool it emits the exact `shlex.join` lifecycle invocation. Native Windows
   Codex/Cursor use a quoted PowerShell call to `adapters/bootstrap.py` with one
   canonical encoded JSON argument. This avoids native argument differences
   between Windows PowerShell 5.1 and PowerShell 7. The decoded request uses the
   same lifecycle implementation and checks. Include the adapter's host value and
   actual session identity. Claude uses `session_id`; Cursor uses
   `conversation_id` as lifecycle `session_id`. An unready session may run
   `diagnose` in its pre-registration shape, or in its issue-level shape with
   `repo_id`, `issue_id` and optional `binding_generation` for the issue in its
   recorded assignment. Both shapes are read-only and accept no other fields.
   For a recorded participant with a packet, the issue-level result also returns
   `packet`, its committed reference list exactly as stored, and `sources`, the
   aligned list of each reference's `recorded_sha256`, `current_sha256` (`null`
   when unreadable) and `available`. A stranded session cannot hash files itself,
   because the hook denies every ordinary tool while it is unready.
   The issue coordinator may also run one `scope` before readiness: a self-refresh
   whose `target_participant` is its own key and whose `packet` repeats its current
   packet (the issue-level `diagnose` `packet`) with the same references in the same order, changing only each `sha256`
   to a digest, with no `owned_paths` and no other fields. It recovers a coordinator
   whose own governing source changed (`SOURCE_STALE`): copy `packet` and replace
   each `sha256` with the aligned `sources[*].current_sha256`. The core then clears the
   acknowledgment, so readiness still needs `read`, `acknowledge` with the returned
   `packet_digest`, then `ready`. A scope that repeats unchanged digests is
   accepted and bumps the revision but does not restore readiness; the failed
   `read` lists the changed references in `stale`. The rule is shared by every native host.
   An explicit `rebind` (`new_issue_id`, optional `new_binding_generation`,
   `evidence`, and `issue_uuid` naming the target) is admitted when its
   `new_issue_id` is the recorded Task issue, or when it leaves the recorded issue
   for a target that already has committed state. When the session has a live
   binding, the rebind's `issue_id` must name that bound issue. A subagent never runs it.
5. A successful matching issue-read completion invokes the shared startup orchestrator. It derives the
   main worktree from Git, registers or resumes, creates a new issue with the observed
   master as coordinator, installs an initial reader packet, reads its source bytes,
   acknowledges the exact digest and verifies `ready`. Existing roadmap bytes,
   coordinator and packet survive repeated starts. The core `update` that commits a
   coordinator's `roadmap.md` moves only that coordinator's own `roadmap.md` packet
   reference to the committed digest and clears its acknowledgment. The coordinator
   then runs the admitted `read` and `acknowledge` bootstrap commands; the next
   covered tool verifies readiness. Reader packets are unchanged. Out-of-band edits
   remain conflicts.
   When the session's binding names another issue, the orchestrator moves it with
   core `rebind` instead of `create`, `resume` or `join`, citing the verified ticket
   identity as evidence. An absent issue is created with the session as coordinator.
   An existing issue still needs an assignment for the session or its own earlier
   participation, otherwise `SCOPE_MISSING`. Pending work on the old participant, or
   participants still attached to an old issue it coordinates, refuse the move with
   `PENDING_OPERATION`. The old issue keeps its payload and coordinator; only this
   participant is detached.
   Governing files come from the selected checkout; task bytes come from the main
   worktree's canonical issue directory. A new session without an assignment
   joins as a roadmap-only reader; it cannot replace another coordinator or edit
   coordinator-owned notes. Coordinator handoff still requires the recorded owner
   and explicit transfer. Assigned steps attach with their verified actual
   host/session identity and own scope. Follow [explicit lifecycle setup](task-workspace-usage.md#explicit-setup-and-bootstrap)
   for recovery. The recovery operation/field allowlist and assignment checks live in
   `adapters/common.py`. Wrappers, chaining, redirection, alternate interpreters,
   conflicting worktrees, and cross-host/session bootstrap requests receive no exemption.

### Ticket-read protocols

Every supported adapter follows the same sequence: record the requested ticket,
validate its provider read and completion, then automate local registration,
attachment, source reading, acknowledgment and readiness. Native event names and
provider envelopes are adapter details.

| Runtime | Ticket-read routing | Completion and identity |
| --- | --- | --- |
| Codex | Exact `mcp__codex_apps__linear_get_issue` with `{id: ISSUE-ID}`. | `PreToolUse` and `PostToolUse` correlate the native `tool_use_id`; read `tool_response`. |
| Claude Code | Configured `linear` or `linear-server` MCP `get_issue`, or an explicitly mapped Desktop Linear connector, with `{id: ISSUE-ID}`. | `PreToolUse` and `PostToolUse` correlate the native `tool_use_id`; read `tool_response`. MCP metadata requires Claude Code 2.1.274 or later; named-server definition scopes are `user`, `project`, `plugin` and `sdk`. Desktop mappings require `claudeai`, `dynamic` or `sdk`. |
| Cursor Agent | Generic `preToolUse` records `MCP:get_issue`, the exact arguments and native call ID; `beforeMCPExecution` checks the configured `linear` or `linear-server` name and official HTTPS MCP URL. | Both pre-hook validations are required. Generic `postToolUse` settles the matching native call using JSON `tool_output`. `afterMCPExecution` does not establish startup readiness. |

The mappings follow the [Claude hook reference](https://code.claude.com/docs/en/hooks#pretooluse-input)
and [Cursor hook reference](https://cursor.com/docs/hooks). Configured server names
select an integration; a name alone does not authenticate a provider. Keep host
permissions and trusted configuration in effect. Cursor's two pre-hooks can arrive
in either order. Completion must identify the admitted native call, so an old
MCP-specific completion cannot settle a fresh retry. The Cursor adapter accepts
`https://mcp.linear.app/mcp` and its `/readonly` endpoint; missing or different
server metadata is an unsupported provider configuration, not a missing ticket.
After a local startup failure, Cursor retains the Task assignment and allows a
fresh lookup with either pre-hook order.

#### Claude Desktop connector mapping

Local Claude Code sessions in Desktop can expose a connected Linear server as
`mcp__<connector-uuid>__get_issue`. Verify the connector is Linear in Desktop's
connector settings before mapping its ID. A UUID-shaped name alone does not
identify Linear. Merge this environment setting into the ignored
`.claude/settings.local.json`, preserving any existing settings:

```json
{
  "env": {
    "AGENT_COMPANY_CLAUDE_LINEAR_CONNECTOR_ID": "<verified-linear-connector-uuid>"
  }
}
```

Use the exact lowercase UUID from that connector's settings URL. Reload the
Claude Code session so the hook process receives the environment setting.
This is local routing configuration, not an OAuth credential or a grant of
Claude permissions. Never populate it from a tool argument or accept arbitrary
UUIDs as Linear. Missing or malformed mappings leave opaque connectors blocked.

The tool name and native `mcp_server.name` must match that configured ID, and
`mcp_server.source` must be `claudeai`, `dynamic` or `sdk`. Desktop 2.1.293
was observed reporting `sdk` for its Linear connector; the other two are
documented remote configuration sources in [Anthropic's provenance contract](https://code.claude.com/docs/en/agent-sdk/typescript#mcpserverprovenance).
Missing or other provenance remains unsupported. The exact-ticket argument,
native call correlation, provider response validation and startup readiness
checks still apply. Before that read, `ToolSearch` has only a narrow
preparation exception: a `select:` query naming nothing but configured
`get_issue` tools (`mcp__linear__get_issue`, `mcp__linear-server__get_issue` or
the mapped connector's), or a keyword query of at most 128 letters, digits,
spaces, `_`, `+` or `-` that contains `get_issue`. Either form allows only
`query` and an integer `max_results` from 1 to 20, and the whole query is at
most 256 characters. A `Read` or `Bash` call
qualifies only when it names exactly `<checkout>/docs/runtime/contributor-workflow.md`
or `<checkout>/AGENTS.md`; the comparison is lexical, no path entry may be a
symbolic link or junction, and `Bash` must split to exactly `cat` plus that
literal path. Everything else stays denied until the ticket read. This does not
add support for cloud/Cowork workspaces.

For a successful Claude `PostToolUse`, Desktop can supply `tool_response` as a
content-block list. The adapter restores its success envelope before invoking
the shared parser; it still requires exactly one text block containing the
requested issue and a canonical immutable UUID. Failed native calls retain the
separate `PostToolUseFailure` path. On 2026-10-09, the local Desktop 2.1.293
AGENT-2 session exercised both callbacks with `sdk` provenance and this list
shape, ending in `TASK_WORKSPACE_READY`. This establishes that observed local
path, not cloud/Cowork support or another platform's installed-host coverage.

The provider parser accepts two explicit input contracts: the observed Codex
connector `CallToolResult` containing one JSON-text issue with shorthand `id` and
immutable `uuid`, or a normalized issue object with shorthand `identifier` and
UUID `id`. Both must match the requested ticket and contain a valid UUID. The
normalized object is a tested adapter contract; it is not evidence that an
installed native Linear MCP server returned that shape. Unexpected provider
shapes produce a specific invalid-response diagnostic without creating a workspace.

Typed missing-ticket errors and the observed Codex missing-reference envelope
stop without workspace creation. The latter identifies `INVALID_ARGUMENT` with
one exact structured error: `invalid_request`, status 400, the provider's
missing-reference message and a request ID. Generic invalid arguments remain
provider errors. Authentication, permission, network and invalid-response failures
retain their distinct diagnostics. Provider text is data; it grants no execution
authority. The orchestrator reads and hashes actual assigned source bytes before
acknowledgment.

[Codex documents native hook decisions for calls nested in JavaScript code
mode](https://learn.chatgpt.com/docs/hooks#tool-calls-from-code-mode). A visible
execution wrapper does not by itself identify the native callback shape. If a
runtime exposes only an opaque wrapper, that wrapper gets no pre-readiness tool
exemption.

Tests exercise these protocols with synthetic events. Installed configuration,
callback delivery and real provider round trips require separate evidence for
**each** runtime. Another runtime can implement the same shared startup contract
through its own verified adapter; it does not inherit support from these three.

### Codex bootstrap tool input

The Codex adapter accepts only `Bash` or `exec_command` for bootstrap. Supply the canonical
command from step 4 as `command` (Bash) or `cmd` (exec_command). Explicitly set `login: false`
and `shell: "/bin/sh"` on POSIX. Native Windows accepts `shell: "powershell.exe"`
or `shell: "pwsh.exe"` with the helper's encoded command. Run without a TTY and omit the `tty` field entirely; even `tty: false`
is outside the accepted field allowlist.

For example, the `exec_command` tool input must have this shape, replacing the placeholder
with the exact command from step 4 (use the corresponding Windows shell there):

```json
{
  "cmd": "<canonical lifecycle command from step 4>",
  "login": false,
  "shell": "/bin/sh"
}
```

The only accepted input fields are `command`, `cmd`, `login`, `shell`, `workdir`,
`yield_time_ms`, `max_output_tokens`, `sandbox_permissions`, `justification`, and `prefix_rule`.
Use one command field. These requirements come from the
[Codex bootstrap parser](../../src/agent_company/adapters/codex.py); they do not change host
permission requirements. Claude and Cursor use their own native tool-input shapes.

### Bounded hook processes

Git must be available to the host. If Git cannot start or find the repository,
the launcher does not run and Git's failure status is preserved. The host's own
error policy then applies; do not assume exit 2. Once the launcher starts, missing
environment or Python process failure maps to exit 2. Cursor's configured
`failClosed` gates still apply. See the native failure semantics above.

When manually wrapping a hook in PowerShell `-Command`, append
`exit $LASTEXITCODE` to preserve its native exit status. A bare wrapper converts
native exit 2 to outer exit 1; those are different host outcomes. The platform
tests check both cases separately. See Microsoft's
[PowerShell process exit rules](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe#-command).
This test wrapper does not change the project hook commands or prove an installed
host's shell behavior. Claude's documented default uses Git Bash when installed;
this checkout requires Git for Windows. See Claude's
[command hook fields](https://code.claude.com/docs/en/hooks#command-hook-fields).

A supervisor reads bounded input and executes the selected adapter in a child
process. At the deadline it kills and waits for that worker before returning a
failure response. A timed-out worker cannot continue lifecycle writes after the
supervisor reports the timeout. The shared lifecycle transaction handles interrupted
writes on the next operation. This preserves each host's response and exit conventions;
it does not change the host's own timeout or fail-open behavior.

### Claude foreground Bash

Claude's project settings set `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`. The
[official environment reference](https://code.claude.com/docs/en/env-vars#variables) documents
that this disables automatic backgrounding, the background tool parameter, and Ctrl+B.
The adapter requires that inherited value before ordinary Bash admission. It does not set it
inside the hook to fabricate proof of the parent runtime's configuration.

## Implemented coverage

These rules are implemented in `adapters/common.py`, `adapters/claude.py`, and `adapters/cursor.py`:

| Boundary | Claude Code | Cursor Agent |
| --- | --- | --- |
| Identity | Exact native session ID. A hook carrying `agent_id` with a valid `session_id` is a subagent keyed `<session_id>/agent/<agent_id>`; `subagent_id`, `parent_conversation_id`, background markers and `agent_id` without `session_id` are rejected | Exact conversation ID; require any session ID to match; one workspace root |
| Ordinary tools | Read, Write, Edit, Glob, Grep, NotebookEdit; foreground Bash under the setting above. `ToolSearch`, `Skill` and `SendMessage` are readiness-checked no-effect operations with no pending entry; a tool they surface is evaluated on its own call. A subagent's `SubagentHandback` is the same kind of no-effect operation and is admitted only under a child identity | Read, Write, Edit, Grep, Delete; Shell with supported foreground arguments |
| Linear provider after readiness | Configured connector only (same server name and `mcp_server.source` checks as the ticket read): `get_issue` (any ID), `get_user`, `list_users`, `list_issue_statuses`, `list_comments`, `save_issue`, `save_comment` (a subagent child gets the read operations only). Each call is pending work under its native `tool_use_id`, settled by `PostToolUse` (any non-null `tool_response`, including Desktop's content-block list) or `PostToolUseFailure` | No native provider admission; `MCP:` tools are denied |
| Ticket-first startup | Exact configured Linear issue read before readiness; matching native completion runs shared startup | Exact Linear issue read with generic call-ID and MCP server checks before shared startup |
| Admission | Deny on failed readiness outside the ticket-read/recovery exceptions; otherwise preserve native permission decisions | Allow only the particular validated ticket-read, recovery or ordinary tool call |
| File-tool completion | Supported success/failure event and required payload settle the observed call | Same; native failure type is checked |
| Shell completion | Successful Bash result with string stdout/stderr, `interrupted: false`, and no async markers | Successful Shell result with integer `exitCode` and no async markers |
| Ambiguous shell result/failure | Retain pending operation and report recovery | Same; booleans are not exit codes |
| Child/provider/background tools | Deny unsupported tool names, unconfigured servers, other provider operations (including document/archive operations) and background identities. A ready parent's foreground `Agent` call (no `run_in_background: true`, no `isolation`) is pending work under its `tool_use_id`; `Task`, `TaskOutput`, `TaskStop`, `SpawnAgent` and `Agent` from a child are denied. `SubagentStart` joins the child (restricted, below); `SubagentStop` is an observation | Same; `subagentStart` also denies; remote sessions denied |
| Session start | Advisory recovery/readiness context; no readiness grant | Advisory context only |
| Compaction | PreCompact/PostCompact observations only; no context or decision output | Advisory context only |
| Stop/session end | Optional observation only | Optional observation only; no automatic follow-up |

### Claude subagent child route (restricted)

This route is implemented and covered by synthetic protocol tests, plus one observed
local Desktop session (AGENT-34, 2026-10-09, macOS): `SubagentStart` fired before the
child's first tool call and delivered the fixed `additionalContext`; the child's
`PreToolUse`/`PostToolUse` carried the parent `session_id` with `agent_id`, so the child
was keyed and settled under its own participant while the parent stayed ready;
`SubagentStop` fired at the end of each child turn; the `Agent` `tool_input` carried
`description`, `prompt` and `model`. That session also showed the child's
`SubagentHandback` being denied as an unsupported tool, which is why it is now admitted
for child identities. One local session is not platform coverage: the route stays
**restricted**, and Linux, Windows and cloud/Cowork hosts remain unverified. On
`SubagentStart` the adapter requires the parent binding (host plus raw `session_id`)
to be ready and one admitted parent `Agent` call to be pending. It then runs core
`join` for the child session (a roadmap-only reader packet, `issue_uuid` checked)
followed by `read`, `acknowledge` and `ready`, and records the child's issue
assignment for its bootstrap gate. Its `additionalContext` contains fixed text with the
child's participant key and packet digest only. Otherwise it returns a non-blocking
`systemMessage`, because `SubagentStart` cannot block; the unbound child's first tool
is then denied. Child tool calls are admitted and settled under the child session.
A child gets only the Linear read operations (`get_issue`, `get_user`, `list_users`,
`list_issue_statuses`, `list_comments`); its `save_issue` and `save_comment` are denied
with `HOST_UNSUPPORTED_PROVIDER`, so provider writes stay with the parent.
Before readiness the child's lifecycle bootstrap admits only the recovery shapes
(`read`, `acknowledge`, `resume` and the like), never `scope` or `create`; the core
refuses a child `scope` (`NOT_OWNER`). The coordinator may `scope` the child's key with
owned `context/` paths. `SubagentStop` records an observation and never detaches. No
field links a `SubagentStart` to a specific `Agent` call, so the check is "some admitted
parent `Agent` call is pending", not an exact pairing.

A second local Desktop session (AGENT-34 round 2, 2026-10-10, macOS, hooks enabled)
showed the route failing under concurrency: a ready parent issued three `Agent` calls
within six seconds; all three were admitted as pending work, but only one child was
joined. The other two never received a binding, so every one of their tool calls,
including `SubagentHandback`, was denied with `BINDING_MISSING` and their reports were
lost. A single `Agent` call afterwards worked. The likely cause is concurrent hook
processes (sibling `SubagentStart` joins and the parent's own tool events) advancing the
issue revision between the adapter's state read and the child's `join`, or lock waits
exhausting the 2-second runner deadline. `SubagentStart` fires once and cannot block,
so a child that misses its join cannot recover: the parent must dispatch a fresh
subagent. The adapter therefore re-reads the committed revision immediately before each
`join` attempt and retries `REVISION_CONFLICT` and `BUSY` up to 8 times within a
1-second budget that leaves room inside the runner deadline. If the join still fails,
the advisory `systemMessage` names the last code and the child stays unbound. The core
checks only the packet digest on `acknowledge`, so revision drift between the child's
`read` and `acknowledge` does not refuse it; a refused `REVISION_CONFLICT` is retried
once after a fresh `read`. The retry is covered by synthetic tests only; it has not yet
been observed with concurrent live `Agent` calls.

The same session also showed that this Desktop build's `Agent` tool has no
`run_in_background` parameter: a supplied `run_in_background: "true"` string reached
the hook as an ordinary foreground call and was admitted. The denial of a boolean
`true` stays in the adapter but is unexercised on this host. An `Agent` call with
`isolation: "worktree"` was denied as designed.

Claude discards `systemMessage` and `continue` from both compaction events.
[PreCompact](https://code.claude.com/docs/en/hooks#precompact) supports blocking, but this
adapter adds no compaction blocking policy.
[PostCompact](https://code.claude.com/docs/en/hooks#postcompact) has no decision control.
Handled compaction events return `{}`, including identity/observation failures. Recovery
context uses `SessionStart` or normal pre-tool denials; compaction never grants readiness
or settles pending tools. Existing malformed-input and process-failure exits still apply.

The tool IDs used for pending work are supplied by the host. Generated request UUIDs are
transaction identities, never substitutes for missing session or tool IDs. Exact completion retries
are idempotent. Missing or malformed identity fails admission. No native adapter substitutes
`codex` for another host, invokes provider writes, settles ambiguous work, or runs cleanup on stop.

The Claude provider allowlist checks the server, provenance and operation name only.
Field-level rules, such as an assignee equal to the initiating human or the target state
name, remain in the contributor procedure's read-back rule; provider success is not
lifecycle authority. Denials name the admitted next operation for their code; see
[explicit setup](task-workspace-usage.md#explicit-setup-and-bootstrap). A coordinator
denied with `SOURCE_STALE` recovers through issue-level `diagnose` and the self-refresh `scope` built from its `sources` (setup step 4),
then `read`, `acknowledge` and `ready`; a reader asks the coordinator to re-scope it.

Unsupported MCP/provider routes must use a separately verified provider workflow. No native
archive-provider coverage is claimed here. Interrupted or ambiguous shell work remains pending:
inspect the actual process outcome before an explicit lifecycle recovery/settlement. A stop event,
empty result, or timeout is not that evidence. Shell commands remain cooperative tool calls; these
hooks do not prove that arbitrary commands did not launch their own independent child processes.

## Avoiding duplicate imported hooks

Cursor's [third-party hook reference](https://prod.cursor.com/docs/reference/third-party-hooks)
says Claude project hooks are imported when its third-party-import setting is enabled, which is
the default. Both imported and native matching hooks can run.
The [Cursor environment contract](https://prod.cursor.com/docs/hooks#environment-variables)
provides `CURSOR_VERSION` to hook processes.

The Claude shell entry and Python main exit silently when that variable is present, before reading
input or touching lifecycle state. Only the native Cursor adapter owns those events. This is a
no-decision duplicate skip, not an unconditional permission grant. No global import setting is
changed. Cursor uses generic pre/post hooks for ordinary tools and native call identity.
Its MCP-specific pre-hook additionally checks ticket-read server routing; the
MCP-specific post-hook does not duplicate completion or lifecycle mutation.

## Validation and remaining evidence

Disposable tests exercise both translators, core mutations, malformed envelopes, native response
shapes, duplicate callbacks, host separation, strict bootstrap rejection, and imported-hook skipping.
All three adapters have ticket-first startup regression coverage. Native protocol
tests use synthetic events; they are not actual host callbacks.

Before claiming actual-host acceptance, use a disposable registered repository and retain:

1. The installed host version and evidence that these exact project hook definitions loaded once.
2. An observed native session/tool ID pair and a readiness denial before acknowledgment.
3. A supported foreground call and its matching completion, with no remaining pending entry.
4. A malformed/unsupported call denial and retained state after ambiguous completion.
5. For Cursor with third-party imports enabled, one native mutation and no Claude-host binding.
6. For Claude, inherited background-disable configuration and preserved permission prompting.

Protocol tests do not establish trusted host loading or actual provider interaction.
The PR's validation evidence identifies the native platforms and exact revision tested.
Claude's host-level fail-open cases remain a boundary:
the wrapper converts process failures to exit 2 and the Python deadline precedes the configured
host timeout, but an externally killed or timed-out hook is not guaranteed enforcement.

**Unverified fail-open hypothesis.** In the AGENT-32 Desktop session, `save_issue`,
`list_users` and `list_comments` appeared to complete even though the adapter then denied
every MCP tool after readiness. The code offers no admitting path for them. The only
candidate found is host-side: the project hooks use `timeout: 10`, while the runner's
own deadline is 2 seconds measured inside Python. If launcher start-up, Git and
interpreter start-up plus lock waits exceed the host timeout, Claude Code discards the
hook output and lets the `PreToolUse` call proceed unless the hook sets
`"onFailure": "block"`. This is not established; retain the Desktop hook log or the
transcript's tool-use IDs before relying on it. The recommended repair is explicit
`onFailure` handling in the hook definitions, verified on an installed host. This change
leaves both timeouts unchanged.
