# Local host hooks

This checkout supplies three native entry points over one lifecycle core:

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

Protocol sources checked on 2026-10-06; launcher and shell references checked on 2026-10-07:

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

1. Install this checkout's editable package and locked tools with Poetry. Hooks
   use a fixed invocation-local Git alias to run `adapters/launch.sh` from the
   repository root, including when the host starts in a nested directory.
   The launcher selects `.venv/bin/python` on POSIX or
   `.venv/Scripts/python.exe` under Git for Windows, then the selected host adapter.
   Missing setup exits 2. There is no global interpreter fallback or persistent
   Git alias. See [Git shell-alias behavior](https://git-scm.com/docs/git-config#Documentation/git-config.txt-alias).
2. Review and activate project hooks using the host's own controls. This change does not alter
   global settings or grant trust. Keep existing permission prompts and restrictions enabled.
3. Submit exactly one `Task: ISSUE-ID` line. The adapter records an explicit assignment for
   that host/session. It uses only an existing coordinator assignment or an explicitly registered
   startup packet; it does not invent scope or infer authority from prose.
4. Recover with `agent_company.adapters.common.bootstrap_command(request, host)`
   from the intended worktree's Poetry environment. On POSIX and Claude's Bash
   tool it emits the exact `shlex.join` lifecycle invocation. Native Windows
   Codex/Cursor use a quoted PowerShell call to `adapters/bootstrap.py` with one
   canonical encoded JSON argument. This avoids native argument differences
   between Windows PowerShell 5.1 and PowerShell 7. The decoded request uses the
   same lifecycle implementation and checks. Include the adapter's host value and
   actual session identity. Claude uses `session_id`; Cursor uses
   `conversation_id` as lifecycle `session_id`.
5. Register/resume, read the assigned packet, and acknowledge its digest before ordinary tools.
   The recovery operation/field allowlist and assignment checks live in `adapters/common.py`.
   Wrappers, chaining, redirection, alternate interpreters, conflicting worktrees, and
   cross-host/session bootstrap requests receive no recovery exemption.

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
| Admission | Deny on failed readiness; otherwise return no permission decision | Return allow only for the particular validated recovery or admitted tool call |
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
changed. Cursor registers generic pre/post hooks only, avoiding a second shell/file admission path.

## Validation and remaining evidence

Disposable tests exercise both translators, core mutations, malformed envelopes, native response
shapes, duplicate callbacks, host separation, strict bootstrap rejection, and imported-hook skipping.
The existing Codex behavior remains covered by its regression suite. Native protocol tests use
synthetic events; they are not actual host callbacks.

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
