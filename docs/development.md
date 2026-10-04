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
   poetry sync
   poetry check --lock
   poetry run python --version
   ```

3. Run `make check` before requesting review. Git and a POSIX environment are
   required by the tests. The first supported workspace platform is macOS;
   `fcntl` and directory-descriptor operations are not a Windows port.

`poetry.toml` selects `.venv/` inside each worktree. Task data is shared by issue;
Python environments are separate per worktree. Commit the lock and project
configuration, never environments, caches or task data. Use `poetry sync` for
reproduction; change and review the lock deliberately when updating dependencies.

## Checks

| Command | Checks |
| --- | --- |
| `make format-check` | Ruff formatting without changing files. |
| `make lint` | Ruff errors, warnings, imports, docstrings and logging rules; no automatic fixes. |
| `make type-check` | Mypy on `operations/` and `adapters/`. |
| `make test` | Pytest, branch-aware coverage and the configured 80% coverage floor. |
| `make check` | All four checks. |
| `make format` | Apply Ruff formatting. Review the resulting diff. |

The underlying commands are in the root `Makefile`. Use them directly through
Poetry if Make is unavailable. Pytest uses strict configuration/markers and
importlib collection; tests carry `unit` or `integration` markers. Tests use
temporary repositories and do not adopt or clean an active task workspace.

The policy follows [Lemello's backend configuration](https://github.com/njalexander93/lemello/blob/main/backend/pyproject.toml),
Git blob `a36ab9df41c12abc6d38989362053b47438a8939`. Package paths are adapted to this
repository. Unused application dependencies and asynchronous test plugins are
omitted. Mypy retains Lemello's production-only scope; test annotations aid readers
but are not checked by that command. Runtime validation still checks untrusted
JSON; annotations do not validate incoming data.

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
