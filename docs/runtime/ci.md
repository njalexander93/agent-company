# Required Python pull-request checks

[Python PR checks](../../.github/workflows/python-pr.yml) runs for every pull
request into `main`, including documentation changes and draft PRs. It uses
Python from `.python-version`, Poetry 2.4.2 and `poetry sync` with the committed
lockfile. Setup failure is a failed check; it does not fall back to another
interpreter or regenerate the lock.

## Checks and platform coverage

| Result | Repository command | Runner |
| --- | --- | --- |
| `Python / validate-config` | `poetry run python scripts/dev.py validate-config` | Ubuntu 24.04 |
| `Python / format-check` | `poetry run python scripts/dev.py format-check` | Ubuntu 24.04 |
| `Python / lint` | `poetry run python scripts/dev.py lint` | Ubuntu 24.04 |
| `Python / type-check` | `poetry run python scripts/dev.py type-check` | Ubuntu 24.04 |
| `Python / tests (linux)` | Platform test command below | Ubuntu 24.04 |
| `Python / tests (windows)` | Platform test command below | Native Windows Server 2025 |
| `Python / tests (macos)` | Platform test command below | macOS 15 |
| `Python / combined coverage` | Combination command below | Ubuntu 24.04 |
| `Python / required` | Require all preceding job groups to succeed | Ubuntu 24.04 |

All three native test jobs must pass. Windows and Linux coverage data jointly
meet the configured whole-package floor (currently 80%, including branches).
macOS supplies a separate required test result and diagnostic coverage; it is
not a substitute for either native input. Runner image updates can change
installed tools; manifests record the observed OS, architecture and interpreter.
This matrix does not establish Debian, Fedora or every supported Python version.

These checks validate local product behavior in disposable test repositories.
They need no Linear credentials, real task data or trusted Codex hooks. They do
not prove live host/provider acceptance or close AGENT-1's remaining obligations.

## Local reproduction

1. Follow [development setup](development.md#set-up-each-worktree).
2. Run `poetry run python scripts/dev.py check-local` for daily work. This runs
   configuration, formatting, lint, types and tests on your OS. It reports the
   combined coverage gate as pending; that message is not a test failure.
3. To reproduce a CI test job on an unchanged, clean checkout, run:

   ```sh
   poetry run python scripts/dev.py test --platform-coverage --evidence-dir .coverage.platform
   ```

4. Collect the Windows and Linux evidence directories from the **same commit**.
   Put each complete directory, including its hidden `.coverage` database and
   `manifest.json`, under `.coverage.inputs/windows` or `.coverage.inputs/linux`.
   On a clean checkout of that same commit, run:

   ```sh
   poetry run python scripts/combine_coverage.py .coverage.inputs --output-dir .coverage.combined
   ```

The combiner rejects missing or duplicate platforms, failed tests, different
commits, different tracked bytes and dirty or changed checkouts. Single-platform
`test`, `check` and `ci` remain strict diagnostics that apply the complete-package
floor to that one run. Use `check-local` for the normal local feedback loop and
the combined result for cross-platform acceptance.

## Merge enforcement and revision identity

The main ruleset requires the stable `Python / required` status from GitHub
Actions alongside existing checks. The final job runs even after a dependency
fails or is skipped, and succeeds only when quality, every native test job and
combined coverage succeed. It has no checkout and no token permissions.
Do not rename this job without updating the rule in the same rollout.

The workflow tests GitHub's pull-request merge commit, including the base branch,
using the default `pull_request` checkout. The final job summary records the PR
head, base and tested merge SHA. The per-job manifests record the actual tested
SHA and tracked-content digest. A new push starts a new run and cancels obsolete
work for that PR. The strict status rule requires an up-to-date branch. Old
results do not substitute for checks on the current revision. See GitHub's
[pull-request event](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
and [required-check troubleshooting](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).

Preserve the existing human review policy, stale-review dismissal, resolved
review threads, squash-only merge and empty bypass list. The workflow never
approves, merges, enables auto-merge or publishes a package. Its only token
permission is `contents: read`; checkout credentials are not persisted. It uses
`pull_request`, never `pull_request_target`, to execute candidate code.

## Evidence and troubleshooting

1. Open the failed job and reproduce its named repository command. Formatting
   and lint are check-only. Apply `format` deliberately, inspect the diff, and
   push the repair. Fix type errors in the configured production scope.
2. Each attempt retains `quality-<task>-<attempt>`, `tests-<platform>-<attempt>`
   and `combined-<attempt>` artifacts for 30 days. Test artifacts include logs,
   JUnit results, coverage XML, the hidden coverage database and the manifest.
   Setup failures may have only Actions logs. Cancellation can prevent upload.
3. Successful Windows/Linux jobs also publish `native-windows` and `native-linux`
   inputs scoped to that workflow run. A retry replaces only that platform's
   input. Attempt-specific diagnostic artifacts remain intact. The combiner
   downloads only the current run's native inputs and checks their provenance.
4. For transient runner, dependency-download or artifact-service errors, use
   **Re-run failed jobs** on the same run. It can reuse a successful sibling's
   native input. If artifacts expired or an input is absent, **Re-run all jobs**.
   Never copy coverage from another revision, lower the floor or bypass the gate.
5. If a check is missing, inspect the event, merge conflicts, fork-workflow
   approval and skip directives in the commit message. This workflow has no
   path filters. A skipped workflow must not be mistaken for a successful gate.
6. Keep a delivery record containing PR head/base/tested SHAs, run URL, attempt,
   actual platforms, per-check outcomes and combined percentage. For deliberate
   failures, record the failing file/defect and failing run, then link the repair
   commit and passing run. Download evidence before its retention expires when
   longer retention is needed. Record blocked merge state without attempting a
   merge. Never use human-review or rule bypasses as a test technique.

See [artifact behavior](https://github.com/actions/upload-artifact/tree/v4.6.2)
and [rerunning workflows](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs).
