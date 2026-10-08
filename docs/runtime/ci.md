# Python pull-request checks

**Read `Python gate` for the aggregate result.** It passes only when all quality
checks, native test jobs and combined coverage pass. A failed, cancelled or
skipped dependency cannot produce a passing gate. Open the failing job for its
command log and evidence artifact.

The [Python PR workflow](../../.github/workflows/python-pr.yml) runs on pull
requests targeting `main`, including documentation-only changes. A newer run for
the same pull request cancels an in-progress run. The workflow uses the interpreter
in [`.python-version`](../../.python-version), Poetry 2.4.2 and locked dependencies.

## What runs

| Check | Runner | Result required by `Python gate` |
| --- | --- | --- |
| `Python validate-config` | Ubuntu 24.04 | Project/lock and pre-commit configuration are valid. |
| `Python format-check` | Ubuntu 24.04 | Ruff formatting passes without edits. |
| `Python lint` | Ubuntu 24.04 | Ruff lint passes without fixes. |
| `Python type-check` | Ubuntu 24.04 | Mypy passes for the configured production scope. |
| `Python tests (ubuntu-24.04)` | Native Ubuntu | All applicable tests pass; collect Linux coverage. |
| `Python tests (windows-2025)` | Native Windows | All applicable tests pass; collect Windows coverage. |
| `Python tests (macos-15)` | Native macOS | All applicable tests pass; retain diagnostic coverage. |
| `Python combined coverage` | Ubuntu 24.04 | Combined native Windows and Linux coverage reaches **80%**. |

The matrix tests the pinned development interpreter. It does not establish every
interpreter in the declared runtime range, every Linux distribution, or installed
host-hook delivery. See [development limits](development.md#source-layout) and
[host integration evidence](host-hooks.md#validation-and-remaining-evidence).

## Reproduce locally

1. Follow [worktree setup](development.md#set-up-each-worktree). Run commands from
   the repository root with the locked Poetry environment.
2. Run the local checks:

   ```sh
   poetry run python scripts/dev.py check-local
   ```

   This runs configuration, formatting, lint, types and tests on the current OS.
   It saves logs, test results and coverage in ignored `.coverage.local/`.
   **The combined coverage gate remains pending.** A failed test still fails.
3. Reproduce one failing quality job with its task name, for example:

   ```sh
   poetry run python scripts/dev.py lint
   ```

4. To reproduce native collection, run this on both Windows and Linux from the
   same clean, unchanged commit. Use a separate external evidence directory on
   each machine:

   ```sh
   poetry run python scripts/dev.py test --platform-coverage --evidence-dir <evidence-directory>
   ```

5. Put the complete Windows and Linux evidence directories under one external
   artifact directory. Preserve each hidden `.coverage` file and `manifest.json`.
   From a clean checkout of that same commit, combine them into another external
   output directory:

   ```sh
   poetry run python scripts/combine_coverage.py <artifact-directory> --output-dir <output-directory>
   ```

   Replace the angle-bracket paths with actual paths; quote paths containing spaces.
   The combiner rejects missing or duplicate platforms, failing test evidence,
   dirty checkouts, changed source bytes and mismatched revisions. macOS data is
   retained for diagnosis but is not an input to this combination.

The [command runner](../../scripts/dev.py), [combiner](../../scripts/combine_coverage.py)
and [coverage policy](../../pyproject.toml) are canonical. Coverage includes
branches and uses one 80% floor across the production package. Platform collection
suspends the per-run floor so Windows-only and POSIX-only code can be combined.
The strict `test`, `check` and `ci` commands retain the single-run 80% floor.

## Evidence and retries

- The gate summary records the **PR head**, **PR base**, **tested merge commit**,
  run attempt and group results. Keep the workflow-run link with those identities.
  A passing older run does not validate a newer candidate.
- Command artifacts include logs and a manifest with the actual OS, interpreter,
  commit, clean/dirty status, tracked-source digest, commands and exit codes.
  Start/end fields show whether the candidate changed during validation.
- Native artifacts also contain test XML, coverage XML and the raw coverage
  database. `python-combined-coverage` contains the combined reports and
  `combined.json`, including the measured result and required floor.
- Artifacts have **14-day retention**. Stable native artifact names are scoped
  to the workflow run. A partial rerun replaces its own artifact and can reuse
  successful same-revision Windows/Linux artifacts from an earlier attempt of
  that run. Earlier attempt logs remain available in Actions.
- Rerun a failed job when its retained peer artifacts are still available. If
  inputs have expired or are missing, rerun the native jobs and dependent coverage
  job, or rerun all jobs. Never combine artifacts from a different candidate or
  substitute local success for the current pull-request result.

## Merge enforcement and human review

The workflow emits `Python gate`; creating that check does not make it a required
branch check. **Adding it to the branch rule is a separate, human-owned setup
step after merge.** Do not report it as required until the live rule confirms it.

At delivery, inspect the current rules for `main` and the current candidate's check
results. Record pending enforcement separately from test results. Preserve other
required checks and follow [PR verification](contributor-workflow.md#pr-preparation-and-verification).
Passing checks do not authorize merge: [human PR review and human-controlled
merge](../framework/baseline.md#authoring-and-publication-rules) still apply.
