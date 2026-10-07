# Python development

Runtime metadata accepts **Python >=3.12,<3.15**. The reproducible development interpreter is **3.14.8**, pinned in `.python-version`; use **Poetry 2.4.2**. A development pin is not the runtime compatibility range. `pyproject.toml` declares the range and `poetry.lock` pins development dependencies. Declaring the range does not replace validation on each supported interpreter. The workspace utility uses only the Python standard library at runtime.

## Set up each worktree

1. Install Python 3.14.8 and Poetry 2.4.2. Select that interpreter explicitly;
   use its absolute path if `python3.14` on your PATH resolves to another version.
2. From the worktree root, create its own ignored environment:

   ```sh
   poetry env use python3.14
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
  `tests/` at commit time. They do not fix, format or stage files.
- Mypy runs `make type-check` on every commit, including deletion-only changes.
  It receives no staged filenames and checks the whole configured `src/` scope.
- Tests and coverage run through `make check` before review, outside automatic
  pre-commit.

For commit checks, the framework temporarily hides unstaged tracked edits and
restores them afterward. Untracked files remain visible to full-scope mypy.
The setup command `--all-files` checks matching files
in the working tree; it is not a staged-only check.
See [pre-commit staged-content behavior](https://pre-commit.com/#pre-commit).

If a check fails, fix the reported problem, review the diff, stage only intended
changes and retry. Use `make format` explicitly when formatting is needed.
Run `make check` before requesting review even when the commit hooks pass.

An intentional bypass such as `git commit --no-verify` is not acceptance. Record
its reason and any failures in the review handoff, then complete `make check`.
Local hooks provide feedback; they do not enforce merges or install required
server checks. See [Git's pre-commit contract](https://git-scm.com/docs/githooks#_pre_commit).

## Source layout

```text
src/agent_company/
  lifecycle/task_workspace.py       # shared lifecycle implementation and JSON entry point
  adapters/                         # native host adapters over the shared lifecycle
  resources/task_workspace/          # reusable roadmap and context templates
tests/
  unit/                             # pure helper/parser behavior
  integration/                      # stateful lifecycle, Git and host-protocol checks
  support.py                        # shared stateful test support
  types.py                          # shared test types
```

Templates are loaded through Python package resources. Native adapters import
the shared lifecycle module; they do not maintain
a second lifecycle implementation. The current hook commands still depend on a
configured contributor checkout and its `.venv/`.

The implemented local filesystem scope is macOS. Windows, Debian, Ubuntu and Fedora are future platform targets. Debian and
Ubuntu require separate validation, and each host/platform combination needs its
own evidence. The package layout establishes no additional operating-system support. The [host guide](host-hooks.md) describes available native adapters and their limits. Installed-host trust and callback delivery require separate evidence; protocol tests do not establish them. Empty platform placeholders are not part of this layout.

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
| `make test` | Pytest, branch-aware coverage and the configured 80% coverage floor. |
| `make check` or `make ci` | Configuration, formatting, lint, types and tests. |
| `make format` | Apply Ruff formatting. Review the resulting diff. |
| `make build` | Build a local wheel under `dist/`; no upload or release. |
| `make clean` | Remove generated build/test/tool output and Python caches; keep `.venv/` and `.task/`. |

The underlying commands are in the root `Makefile`. Use them directly through
Poetry if Make is unavailable. Pytest uses strict configuration/markers and
importlib collection; tests carry `unit` or `integration` markers. Tests use
temporary repositories and do not adopt or clean an active task workspace.

Mypy checks production code, not test annotations. Runtime validation still checks untrusted JSON; annotations do not validate incoming data.

The Makefile provides help, dependency setup, validation and cleanup
for this single Python package. It has no web-server, frontend, database
or container targets. Coverage remains one aggregate check across the production
package; splitting test directories does not change the policy.

`make build` packages the Python modules and workspace template resources into a
local wheel. It does not supply a standalone installer, installed CLI, host setup
or a supported end-user distribution. The release pipeline, artifact promotion
and publication remain separate Controlled Runtime work. `make ci` is a local
command alias; it does not install a GitHub Actions workflow or branch rule.

CI/release-pipeline and branch-rule integration remain separate work. Local results do not establish GitHub enforcement or human acceptance.

## Hook interpreter and trust

Complete environment setup before reviewing or trusting project hooks. Follow [Host hooks](host-hooks.md) for each native configuration, interpreter requirements and trust behavior. Use the [workspace bootstrap](task-workspace-usage.md#explicit-setup-and-bootstrap) for the shared lifecycle interface. A local test pass or rebuilt environment does not grant hook trust or prove installed-host callback delivery.
