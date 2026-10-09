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
   tools even when the session was previously ready.
4. Recover with `agent_company.adapters.common.bootstrap_command(request, host)`
   from the intended worktree's Poetry environment. On POSIX and Claude's Bash
   tool it emits the exact `shlex.join` lifecycle invocation. Native Windows
   Codex/Cursor use a quoted PowerShell call to `adapters/bootstrap.py` with one
   canonical encoded JSON argument. This avoids native argument differences
   between Windows PowerShell 5.1 and PowerShell 7. The decoded request uses the
   same lifecycle implementation and checks. Include the adapter's host value and
   actual session identity. Claude uses `session_id`; Cursor uses
   `conversation_id` as lifecycle `session_id`.
5. A successful matching issue-read completion invokes the shared startup orchestrator. It derives the
   main worktree from Git, registers or resumes, creates a new issue with the observed
   master as coordinator, installs an initial reader packet, reads its source bytes,
   acknowledges the exact digest and verifies `ready`. Existing roadmap bytes,
   coordinator and packet survive repeated starts. A coordinator's committed roadmap
   update refreshes only that owned packet reference; out-of-band edits remain conflicts.
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
checks still apply. Other tools, including `ToolSearch`, gain no blanket
exception. This does not add support for cloud/Cowork workspaces.

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
| Identity | Exact native session ID; reject child markers | Exact conversation ID; require any session ID to match; one workspace root |
| Ordinary tools | Read, Write, Edit, Glob, Grep, NotebookEdit; foreground Bash under the setting above | Read, Write, Edit, Grep, Delete; Shell with supported foreground arguments |
| Ticket-first startup | Exact configured Linear issue read before readiness; matching native completion runs shared startup | Exact Linear issue read with generic call-ID and MCP server checks before shared startup |
| Admission | Deny on failed readiness outside the ticket-read/recovery exceptions; otherwise preserve native permission decisions | Allow only the particular validated ticket-read, recovery or ordinary tool call |
| File-tool completion | Supported success/failure event and required payload settle the observed call | Same; native failure type is checked |
| Shell completion | Successful Bash result with string stdout/stderr, `interrupted: false`, and no async markers | Successful Shell result with integer `exitCode` and no async markers |
| Ambiguous shell result/failure | Retain pending operation and report recovery | Same; booleans are not exit codes |
| Child/provider/background tools | Deny unsupported tool names and observed child/background identities | Same; `subagentStart` also denies; remote sessions denied |
| Session start | Advisory recovery/readiness context; no readiness grant | Advisory context only |
| Compaction | PreCompact/PostCompact observations only; no context or decision output | Advisory context only |
| Stop/session end | Optional observation only | Optional observation only; no automatic follow-up |

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
