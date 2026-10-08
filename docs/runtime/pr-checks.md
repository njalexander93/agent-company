# Pull-request pre-merge checks

**All five checks must pass.** Open a failed check for its failing step, command
log and evidence artifact. The [PR Pre-Merge Check workflow](../../.github/workflows/pr-checks.yml)
runs on pull requests targeting `main`, including documentation-only changes.
Bugbot is an independent check, outside these five jobs.

The workflow uses the interpreter in [`.python-version`](../../.python-version),
Poetry 2.4.2 and locked dependencies. A newer run for the same pull request cancels
an in-progress run. Normal cancellation stops further validation work; artifact
retention steps still attempt to preserve available diagnostic evidence.

## What runs

| Check | Runner | Passing result |
| --- | --- | --- |
| `Code Quality Check` | Ubuntu 24.04 | Configuration, formatting, lint and types all pass. |
| `Linux Tests (Unit/Integration)` | Ubuntu 24.04 | Unit and integration suites both pass. |
| `Windows Tests (Unit/Integration)` | Windows 2025 | Unit and integration suites both pass. |
| `MacOS Tests (Unit/Integration)` | macOS 15 | Unit and integration suites both pass. |
| `Test Coverage Check` | Ubuntu 24.04 | Complete successful native Windows/Linux suites combine to at least **80%** coverage. |

Quality and the three OS jobs can run concurrently. Quality steps run in order:
`validate-config`, `format-check`, `lint`, then `type-check`. Later steps still run
after an earlier failure unless cancelled; the failure still fails the job.
Each OS similarly runs `test-unit`, then `test-integration`, including integration
after a unit failure. The matrix does not cancel peer jobs on failure.

Coverage waits for all three OS jobs. Unless cancelled, it runs even when an OS
job fails so missing or failed Windows/Linux suite evidence produces an explicit
failure. It combines both suites from Windows and Linux. macOS coverage is
retained for diagnosis but is not a combination input; its test check must still
pass independently. Coverage success alone does not mean all five checks passed.

The matrix tests the pinned development interpreter. It does not establish every
interpreter in the declared runtime range, every Linux distribution, or installed
host-hook delivery. See [development limits](development.md#source-layout) and
[host integration evidence](host-hooks.md#validation-and-remaining-evidence).

## Reproduce locally

1. Follow [worktree setup](development.md#set-up-each-worktree). Run commands from
   the repository root with the locked Poetry environment.
2. Run the existing local checks:

   ```sh
   poetry run python scripts/dev.py check-local
   ```

   This still runs configuration, formatting, lint, types and the full test suite
   on the current OS. It stops at the first failure and saves evidence in ignored
   `.coverage.local/`. **Combined Windows/Linux coverage remains pending.**
3. Reproduce quality steps individually. Run all four to inspect independent
   failures, as the workflow does:

   ```sh
   poetry run python scripts/dev.py validate-config
   poetry run python scripts/dev.py format-check
   poetry run python scripts/dev.py lint
   poetry run python scripts/dev.py type-check
   ```

4. Reproduce split native collection on both Windows and Linux from the same
   clean, unchanged commit. Use separate external output directories per suite:

   ```sh
   poetry run python scripts/dev.py test-unit --platform-coverage --evidence-dir <platform-directory>/unit
   poetry run python scripts/dev.py test-integration --platform-coverage --evidence-dir <platform-directory>/integration
   ```

   Run integration even if unit fails, then preserve both exit results. A later
   successful suite does not erase an earlier failure.
5. Put the complete Windows and Linux directories under one external artifact
   directory. Preserve every suite's hidden `.coverage` file and `manifest.json`.
   From a clean checkout of that same commit, combine them into another external
   output directory:

   ```sh
   poetry run python scripts/combine_coverage.py <artifact-directory> --output-dir <output-directory>
   ```

   Replace angle-bracket paths with actual paths; quote paths containing spaces.
   The combiner rejects missing, failed, duplicate or incomplete native suites,
   dirty checkouts, changed source bytes and mismatched revisions. Existing full
   `test` or `check-local` evidence remains supported: each platform supplies
   either one successful full suite or both successful split suites.

The [command runner](../../scripts/dev.py), [combiner](../../scripts/combine_coverage.py)
and [coverage policy](../../pyproject.toml) are canonical. Coverage includes
branches and uses one 80% floor across the production package. Platform collection
suspends the per-run floor so Windows-only and POSIX-only code can be combined.
The strict `test`, `check` and `ci` commands retain the single-run 80% floor.

## Evidence and retries

- The coverage job summary records the **PR head**, **PR base**, **tested merge
  commit**, run attempt and native-test/coverage results. Keep the workflow-run
  link and all five check results with those identities. A passing older run does
  not validate a newer candidate.
- `python-quality` contains `config/`, `format/`, `lint/` and `types/` evidence.
  Each command manifest records the OS, interpreter, commit, clean/dirty status,
  tracked-source digest, commands and exit codes. Start/end fields detect changes
  during validation; logs retain command output.
- `python-tests-linux`, `python-tests-windows` and `python-tests-macos` each contain
  `unit/` and `integration/`. Each suite has its own manifest, log, `tests.xml`,
  `coverage.xml` and hidden `.coverage` database when collection completes.
- `python-combined-coverage` contains combined reports and `combined.json`,
  including the measured result and required floor. Incomplete runs may retain
  only diagnostic output; missing artifacts are not passing evidence.
- Artifacts have **14-day retention**. Stable native artifact names are scoped
  to the workflow run. A partial rerun replaces that platform's suite evidence
  and can reuse successful same-revision peer artifacts from an earlier attempt
  of the same run. Earlier attempt logs remain available in Actions.
- Rerun a failed job when peer artifacts remain available. If inputs have expired
  or are missing, rerun the native jobs and coverage, or all jobs. Never combine
  artifacts from a different candidate or substitute local success for the
  current pull-request result.

## Tests for the checking tools

These integration tests protect the validation machinery itself:

- [test_pr_workflow.py](../../tests/integration/tooling/test_pr_workflow.py)
  checks the five-job configuration and continuation conditions. It exercises
  collector arguments with injected subprocess failures and checks that later
  collectors run, failures remain recorded and suite evidence stays separate.
  It does not execute GitHub's scheduler; hosted runs provide that evidence.
- [test_pre_commit.py](../../tests/integration/tooling/test_pre_commit.py)
  installs real Git hooks in disposable clones with the locked tools. It checks
  successful commits, rejection of formatting/lint/type errors, partial staging,
  restoration of unstaged edits and missing-environment failures. It validates
  local commit hooks, not GitHub branch enforcement.
- [test_platform_coverage.py](../../tests/integration/tooling/test_platform_coverage.py)
  exercises coverage transport, combination and evidence rejection, including
  incomplete suites and changed checkout bytes. Synthetic coverage and platform
  labels test those rules; they do not prove tests ran on native Windows/Linux.
  Native job artifacts supply that separate evidence.

## Merge enforcement and human review

Creating these checks does not make them required branch checks. **Requiring all
five named checks is a separate, human-owned setup step after merge.** Do not
report them as required until the live branch rule confirms it.

At delivery, inspect the current rules for `main` and the current candidate's
results. Record pending enforcement separately from test results. Preserve other
required checks, including independently configured Bugbot requirements. Follow
[submission readiness and PR verification](contributor-workflow.md#8-submit-and-verify-delivery):
finish work and available checks before opening a review-ready PR; PR-triggered
checks can honestly remain pending after submission. Passing checks do not
authorize merge: [human PR review and human-controlled merge](../framework/baseline.md#authoring-and-publication-rules)
still apply.
