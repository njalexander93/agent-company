# Python development

Runtime metadata accepts **Python >=3.12,<3.15**. The reproducible development interpreter is **3.14.8**, pinned in `.python-version`; use **Poetry 2.4.2**. A development pin is not the runtime compatibility range. `pyproject.toml` declares the range and `poetry.lock` pins development dependencies. Declaring the range does not replace validation on each supported interpreter. The workspace utility uses only the Python standard library at runtime.

## Set up each worktree

1. Install Python 3.14.8 and Poetry 2.4.2. Select that interpreter explicitly;
   use its absolute path if `python3.14` on your PATH resolves to another version.
2. From the worktree root, create its own ignored environment:

   ```sh
   poetry env use /absolute/path/to/python3.14
   poetry sync
   poetry run python scripts/dev.py validate-config
   poetry run python --version
   ```

3. On Windows, pass the quoted path to `python.exe` to `poetry env use`.
   Use a local NTFS checkout. On Linux and macOS, use a local filesystem with
   the permissions and locking described in the [workspace contract](task-workspace.md#registration-and-validation).
4. Run `poetry run python scripts/dev.py check-local` before requesting review.
   Tests require Git. Make is optional; the Python command works across platforms.

`poetry.toml` selects `.venv/` inside each worktree. Task data is shared by issue;
Python environments are separate per worktree. Commit the lock and project
configuration, never environments, caches or task data. Use `poetry sync` for
reproduction; change and review the lock deliberately when updating dependencies.
`poetry sync` also installs `agent_company` in editable mode from `src/`. Run it
after switching to this layout or creating a new worktree. Source edits then use
the same package import without copying files into the environment.

## Source documentation and logic comments

These rules apply to every Python module in production code, scripts and tests.
Authors apply them while writing or changing code. Reviewers inspect every changed
Python file for compliance before the PR is ready for human review.

- Give every module a docstring describing its responsibility and boundary.
- Give every named function, method and class a meaningful Google-style docstring.
  Include `Args`, `Returns` and `Raises` sections whenever the corresponding
  contract exists. Describe actual inputs, results and failure conditions.
- Organize every multi-step function into commented logical blocks. Put a concise
  one- or two-line comment before each distinct phase, even when an experienced
  developer could infer that phase from the code. Read together, the comments
  should describe the function as pseudocode.
- A logical block includes selecting an input source, validating a boundary,
  applying defaults, choosing a branch, transforming data, performing I/O,
  updating state, handling an error, constructing output and cleanup. A branch,
  loop, `try`/`except`, asynchronous step, transaction boundary or lifecycle
  transition begins a new commented block.
- Group related statements under one block comment. Keep the comments synchronized
  with behavior. Describe the phase's purpose rather than repeating assignments.
- A single-expression helper needs only its docstring when it performs one
  operation without a branch, side effect or second phase. Do not add a comment
  that merely repeats that docstring.

Ruff checks the configured docstring rules. Passing lint does not establish that
contracts are complete or that logical-block comments explain the code. Authors
and reviewers must inspect those qualities explicitly. For a documentation-only
Python change, verify that executable behavior remains unchanged; comments and
docstrings must not conceal a logic change.

## Supported development environments

Linux, Windows and macOS are equal supported development environments. Run local
checks on the developer's actual OS and record that OS in the evidence. CI must
supply the required native evidence from all three operating systems for the same
candidate. A local pass on any one OS does not replace the other two native runs.
See [testing standards](testing.md) for platform applicability and
[PR checks](pr-checks.md) for the independent native checks and aggregate gate.

## Local commit checks

Humans and agents use the same setup. After selecting the worktree interpreter,
run these commands from the repository root:

```sh
poetry sync                       # also available as make install
poetry run pre-commit install
poetry run pre-commit run --all-files
```

The tracked configuration alone does not install a Git hook. Install it in each
clone without `--overwrite` or changes to global `core.hooksPath`. Linked
worktrees normally share the Git hooks directory, so installation affects those
worktrees too; each still needs its own configured environment.
The installed runner references the installing environment. If that environment
is removed, reinstall from a retained checkout with a configured environment.
See [pre-commit installation](https://pre-commit.com/#usage) and
[Git worktree details](https://git-scm.com/docs/git-worktree#_details).

The three hooks in [the local configuration](../../.pre-commit-config.yaml) reuse
the locked Poetry tools and [project rules](../../pyproject.toml):

- Ruff lint and format checks examine staged Python files under `src/` and
  `tests/` and `scripts/` at commit time. They do not fix, format or stage files.
- Mypy runs `poetry run python scripts/dev.py type-check` on every commit, including deletion-only changes.
  It receives no staged filenames and checks the whole configured `src/` scope.
- Tests and coverage run through `poetry run python scripts/dev.py check-local` before review, outside automatic
  pre-commit.

For commit checks, the framework temporarily hides unstaged tracked edits and
restores them afterward. Untracked files remain visible to full-scope mypy.
The setup command `--all-files` checks matching files
in the working tree; it is not a staged-only check.
See [pre-commit staged-content behavior](https://pre-commit.com/#pre-commit).

If a check fails, fix the reported problem, review the diff, stage only intended
changes and retry. Use `poetry run python scripts/dev.py format` explicitly when
formatting is needed. Run `check-local` before requesting review even
when the commit hooks pass.

An intentional bypass such as `git commit --no-verify` is not acceptance. Record
its reason and any failures in the review handoff, then complete `check-local` and the required platform validation.
Local hooks provide feedback; they do not enforce merges or install required
server checks. See [Git's pre-commit contract](https://git-scm.com/docs/githooks#_pre_commit).

## Source layout

```text
src/agent_company/
  lifecycle/task_workspace.py       # shared lifecycle implementation and JSON entry point
  lifecycle/_filesystem*.py         # platform filesystem implementations
  adapters/                         # native host adapters over the shared lifecycle
  resources/task_workspace/          # reusable roadmap and context templates
tests/
  unit/                             # pure helper/parser behavior
  integration/                      # stateful lifecycle, Git and host-protocol checks
  support.py                        # shared stateful test support
  types.py                          # shared test types
scripts/dev.py                      # portable contributor command runner
```

Templates are loaded through Python package resources. Native adapters import
the shared lifecycle module; they do not maintain
a second lifecycle implementation. The current hook commands still depend on a
configured contributor checkout and its `.venv/`.

The workspace targets macOS, native Windows and Linux. Native Windows uses local
NTFS and does not require WSL. Windows-mounted paths in WSL are not interchangeable
with a Linux filesystem; keep Linux worktrees in the Linux filesystem. See
[Microsoft's filesystem guidance](https://learn.microsoft.com/en-us/windows/wsl/filesystems).
Debian, Ubuntu and Fedora require separately identified evidence. A container run
establishes its tested userland, not a different native kernel or installed host.

Record the exact operating system, interpreter and tested revision in the PR's
validation evidence. Do not infer a completed platform matrix from package
metadata. The [host guide](host-hooks.md) records
host integration limits. Installed-host trust and callback delivery require
separate evidence; protocol tests do not establish them.

## Editor setup

The tracked `.vscode/settings.json`, `.vscode/extensions.json` and `.editorconfig` define shared editor behavior. Open the worktree root in VS Code and install its recommended extensions. Select
the root `.venv` interpreter if the editor already remembers another environment.
Use Poetry for dependency management.

Python uses four spaces, Ruff formatting and import/fix actions on explicit save.
Pytest discovers root `tests/`; Pylance resolves `src/`; mypy uses the root project
configuration. Pylance type checking is disabled to avoid a second type policy.
Makefile recipes use tabs, the editor shows a 100-column guide, and Markdown
preserves trailing spaces. Local VS Code configuration is ignored except for the
two shared settings/recommendation files.

## Checks

Use the [testing standards](testing.md) for classification, assertion quality,
native scenarios, per-function obligations, and coverage exceptions.

| Command | Checks |
| --- | --- |
| `make` or `make help` | List development commands without changing files. |
| `make install` | Sync locked dependencies and install the editable package. |
| `make validate-config` | Validate project metadata, lock consistency and hook configuration. |
| `make format-check` | Ruff formatting without changing files. |
| `make lint` | Ruff errors, warnings, imports, docstrings and logging rules; no automatic fixes. |
| `make type-check` | Mypy on production code under `src/agent_company/`. |
| `make test-unit` | Run `tests/unit` without imposing aggregate coverage on the subset. |
| `make test-integration` | Run `tests/integration` without a subset coverage gate. |
| `poetry run python scripts/dev.py test-tooling --evidence-dir OUT` | Collect validation-tool coverage separately from production coverage. |
| `make test` | Pytest, branch-aware coverage and the configured 80% coverage floor. |
| `make check-local` | Configuration, formatting, lint, types and all tests on this OS; save coverage for combination. |
| `make check` or `make ci` | Strict single-run checks, including the whole-package 80% coverage floor. |
| `make format` | Apply Ruff formatting. Review the resulting diff. |
| `make build` | Build a local wheel under `dist/`; no upload or release. |
| `make clean` | Remove generated build/test/tool output and Python caches; keep `.venv/` and `.task/`. |

Every Make target delegates to [scripts/dev.py](../../scripts/dev.py). For any
command in the table, use `poetry run python scripts/dev.py <target>` on Windows
or when Make is unavailable. The same command implementation runs on every OS. Pytest uses strict configuration/markers and
importlib collection; tests carry `unit` or `integration` markers. Tests use
temporary repositories and do not adopt or clean an active task workspace.

Use `check-local` for daily development. It stores logs, tests and coverage in
ignored `.coverage.local/` and explicitly reports that the combined coverage gate
is pending. `--evidence-dir <path>` chooses another output directory. A failing
test or changed checkout still fails the command.

POSIX cannot execute the Windows backend, so its single-run whole-package
coverage can fall below 80% even when every applicable test passes. The strict
`check`, `ci` and `test` commands retain that floor; they do not silently omit the
other platform's code. Acceptance requires successful native Linux, Windows and
macOS runs from the same unchanged candidate. The historical aggregate additionally combines the Linux and
Windows production reports with
`poetry run python scripts/combine_coverage.py <artifact-directory> --output-dir <output>`.
The combination requires separate unit and integration records from each of those
two operating systems. It
verifies the source revision, clean start/end state, pinned tools, configuration,
child instrumentation, real branch databases, successful collection/outcomes and
artifact hashes. The combining checkout must also remain clean and unchanged while
applying the configured 80% floor to the complete package.
It preserves each platform's original data and coverage context.

Mypy checks production code, not test annotations. Runtime validation still checks untrusted JSON; annotations do not validate incoming data.

The Makefile provides help, dependency setup, validation and cleanup
for this single Python package. It has no web-server, frontend, database
or container targets. Coverage remains one aggregate check across the production
package; splitting test directories does not change the policy.

`make build` packages the Python modules and workspace template resources into a
local wheel. It does not supply a standalone installer, installed CLI, host setup
or a supported end-user distribution. The release pipeline, artifact promotion
and publication remain separate Controlled Runtime work. `make ci` is a local
command alias; it does not install a workflow or branch rule.

The [PR pre-merge checks guide](pr-checks.md) describes the five GitHub checks, native
unit/integration suites, artifacts and retries. Requiring all five checks in the
branch rule is a separate human-owned setup step after merge. Local results do
not establish GitHub enforcement or human acceptance.

## Hook interpreter and trust

Complete environment setup before reviewing or trusting project hooks. Follow [Host hooks](host-hooks.md) for each native configuration, interpreter requirements and trust behavior. Use the [workspace bootstrap](task-workspace-usage.md#explicit-setup-and-bootstrap) for the shared lifecycle interface. A local test pass or rebuilt environment does not grant hook trust or prove installed-host callback delivery.
