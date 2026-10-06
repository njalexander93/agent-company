# Local host hooks

This checkout supplies three native entry points over one lifecycle core:

| Host | Project configuration | Python entry | Participant host |
| --- | --- | --- | --- |
| Codex | `.codex/hooks.json` | `agent_company.adapters.codex` | `codex` |
| Claude Code | `.claude/settings.json` | `agent_company.adapters.claude` | `claude-code` |
| Cursor Agent | `.cursor/hooks.json` | `agent_company.adapters.cursor` | `cursor` |

The supported environment is a local macOS checkout with its editable Poetry installation.
Generic Claude chat, Cowork, cloud/remote agents, Cursor Tab, and other operating systems are
not covered. Repository hooks are cooperative guardrails, not a sandbox or execution authority.
A successful local protocol test does not prove that a host loaded or trusted its configuration.

## Native protocols and failure semantics

Sources checked on 2026-10-06:

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

1. Install this checkout's editable package and locked tools with Poetry. Every configuration
   invokes the quoted `.venv/bin/python` and its host-specific file under `src/agent_company/adapters/`.
   Missing setup exits 2. No system interpreter or shell `PYTHONPATH` fallback is accepted.
2. Review and activate project hooks using the host's own controls. This change does not alter
   global settings or grant trust. Keep existing permission prompts and restrictions enabled.
3. Submit exactly one `Task: ISSUE-ID` line. The adapter records an explicit assignment for
   that host/session. It uses only an existing coordinator assignment or an explicitly registered
   startup packet; it does not invent scope or infer authority from prose.
4. Recover with the exact canonical command produced by
   `shlex.join([PYTHON, str(LIFECYCLE), "--request-json", json.dumps(request)])`.
   `PYTHON` is the checkout's `.venv/bin/python`; `LIFECYCLE` is
   `src/agent_company/lifecycle/task_workspace.py` under that checkout.
   Include the adapter's host value and actual session identity in the request.
   Claude uses `session_id`; Cursor uses `conversation_id` as the lifecycle `session_id`.
5. Register/resume, read the assigned packet, and acknowledge its digest before ordinary tools.
   The recovery operation/field allowlist and assignment checks live in `adapters/common.py`.
   Wrappers, chaining, redirection, alternate interpreters, conflicting worktrees, and
   cross-host/session bootstrap requests receive no recovery exemption.

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
| Session/compaction | Advisory context; no false readiness grant | Advisory context only |
| Stop/session end | Optional observation only | Optional observation only; no automatic follow-up |

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

No trusted host session, provider interaction, global configuration change, or platform-parity test
was performed by the local protocol checks. Claude's host-level fail-open cases remain a boundary:
the wrapper converts process failures to exit 2 and the Python deadline precedes the configured
host timeout, but an externally killed or timed-out hook is not guaranteed enforcement.
