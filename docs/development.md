# Python development

Use **Python 3.14.8** and **Poetry 2.4.2**. The exact interpreter requirement is
declared in `pyproject.toml` and `.python-version`. `poetry.lock` pins development
dependencies. The workspace utility uses only the Python standard library at
runtime.

## Set up each worktree

1. Install the required interpreter and Poetry. This team's source-build location
   is `/opt/python/3.14.8/bin/python3.14`. Use the absolute path to your Python
   3.14.8 installation when it differs.
2. From the worktree root, create its own ignored environment:

   ```sh
   poetry env use /opt/python/3.14.8/bin/python3.14
   make install
   make validate-config
   poetry run python --version
   ```

3. Run `make check` before requesting review. Git and a POSIX environment are
   required by the tests. The first supported workspace platform is macOS;
   `fcntl` and directory-descriptor operations are not a Windows port.

`poetry.toml` selects `.venv/` inside each worktree. Task data is shared by issue;
Python environments are separate per worktree. Commit the lock and project
configuration, never environments, caches or task data. Use `poetry sync` for
reproduction; change and review the lock deliberately when updating dependencies.
`make install` also installs `agent_company` in editable mode from `src/`. Run it
after switching to this layout or creating a new worktree. Source edits then use
the same package import without copying files into the environment.

## Source layout

```text
src/agent_company/
  lifecycle/task_workspace.py       # shared lifecycle implementation and JSON entry point
  adapters/codex.py                  # Codex hook input/output translation
  resources/task_workspace/          # reusable roadmap and context templates
tests/
  lifecycle/                        # lifecycle behavior and failure checks
  adapters/                         # Codex protocol and bootstrap checks
```

Templates are loaded through Python package resources. The Codex adapter imports
the shared lifecycle module; it does not modify Python's import path or maintain
a second lifecycle implementation. The current hook commands still depend on a
configured contributor checkout and its `.venv/`.

Windows, macOS, Debian, Ubuntu and Fedora are future platform targets. Debian and
Ubuntu require separate validation, and each host/platform combination needs its
own evidence. This source move establishes no additional operating-system or host
support. CLI/setup commands, other host adapters and platform implementations will
be added when implemented; empty placeholders are not part of this layout.

## Editor setup

The tracked `.vscode/settings.json`, `.vscode/extensions.json` and `.editorconfig`
adapt the Python settings from [Lemello at the reviewed reference commit](https://github.com/njalexander93/lemello/tree/3ea241ceca93a6eb93f88c66a82940f49f47d19c).
Open the worktree root in VS Code and install its recommended extensions. Select
the root `.venv` interpreter if the editor already remembers another environment.
Use Poetry for dependency management.

Python uses four spaces, Ruff formatting and import/fix actions on explicit save.
Pytest discovers root `tests/`; Pylance resolves `src/`; mypy uses the root project
configuration. Pylance type checking is disabled to avoid a second type policy.
Makefile recipes use tabs, the editor shows a 100-column guide, and Markdown
preserves trailing spaces. Local VS Code configuration is ignored except for the
two shared settings/recommendation files.

## Checks

| Command | Checks |
| --- | --- |
| `make` or `make help` | List development commands without changing files. |
| `make install` | Sync locked dependencies and install the editable package. |
| `make validate-config` | Validate project metadata and lock consistency. |
| `make format-check` | Ruff formatting without changing files. |
| `make lint` | Ruff errors, warnings, imports, docstrings and logging rules; no automatic fixes. |
| `make type-check` | Mypy on production code under `src/agent_company/`. |
| `make test` | Pytest, branch-aware coverage and the configured 80% coverage floor. |
| `make check` or `make ci` | Configuration, formatting, lint, types and tests. |
| `make format` | Apply Ruff formatting. Review the resulting diff. |
| `make build` | Build a local wheel under `dist/`; no upload or release. |
| `make clean` | Remove generated build/test/tool output and Python caches; keep `.venv/` and `.task/`. |

The underlying commands are in the root `Makefile`. Use them directly through
Poetry if Make is unavailable. Pytest uses strict configuration/markers and
importlib collection; tests carry `unit` or `integration` markers. Tests use
temporary repositories and do not adopt or clean an active task workspace.

The policy follows [Lemello's backend configuration](https://github.com/njalexander93/lemello/blob/3ea241ceca93a6eb93f88c66a82940f49f47d19c/backend/pyproject.toml),
Git blob `a36ab9df41c12abc6d38989362053b47438a8939`. Package paths are adapted to this
repository. Unused application dependencies and asynchronous test plugins are
omitted. Mypy retains Lemello's production-only scope; test annotations aid readers
but are not checked by that command. Runtime validation still checks untrusted
JSON; annotations do not validate incoming data.

The Makefile adapts Lemello's help, dependency setup, validation and cleanup
pattern to this single Python package. It has no web-server, frontend, database
or container targets. Coverage remains one aggregate check across the production
package; splitting test directories does not change the policy.

`make build` packages the Python modules and workspace template resources into a
local wheel. It does not supply a standalone installer, installed CLI, host setup
or a supported end-user distribution. The release pipeline, artifact promotion
and publication remain separate Controlled Runtime work. `make ci` is a local
command alias; it does not install a GitHub Actions workflow or branch rule.

Required pull-request checks and branch-rule integration are tracked separately
in [AGENT-24](https://linear.app/ne3ko93/issue/AGENT-24/establish-required-python-checks-before-merging-pull-requests).
Local results do not establish GitHub enforcement or human acceptance.

## Hook interpreter and trust

Complete environment setup **before** reviewing or trusting project hooks. Every
hook invokes the current Git worktree's `.venv/bin/python` with quoted absolute
paths. There is no system-interpreter fallback. An absent executable produces
`TASK_WORKSPACE_SETUP_REQUIRED`; host failure behavior still limits enforcement.

The strict lifecycle bootstrap also requires that exact worktree interpreter.
See the [bootstrap instructions](task-workspace.md#explicit-setup-and-bootstrap)
for its canonical command grammar and the contract's host acceptance limits.
Rebuilding an environment or passing local tests does not trust hooks or prove
desktop callback delivery.
