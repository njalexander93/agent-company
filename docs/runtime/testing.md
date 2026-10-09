# Testing standards

These are the repository's concrete test rules for AGENT-30. The contributor
procedure owns issue planning and review. [AGENT-15](https://linear.app/ne3ko93/issue/AGENT-15/create-the-independent-testing-procedure)
owns the separate test-author procedure and templates; this guide does not claim
that work is complete.

## Classify the verified boundary

- Put focused behavior tests in `tests/unit/`, mark them `unit`, and name files
  `test_<subject>.py`. A unit may call real cheap helpers. Mock a boundary only
  when it is external, slow, nondeterministic, or needed to produce a rare fault.
  A helper call alone does not make a test integration. Test return values, state,
  required effects, and prohibited effects on failure.
- Put real component, process, persistence, filesystem or host-protocol boundary
  tests in `tests/integration/`, mark them `integration`, and use disposable
  resources. This includes native handles, actual locks, worker cleanup and
  serialized lifecycle transitions. An internal mock cannot prove a native
  boundary. The [Google testing overview](https://abseil.io/resources/swe-book/html/ch11.html)
  distinguishes test scope from resource size.
- Give each collected test exactly one suite marker matching its directory.
  Use function-scoped mutable fixtures by default. Register cleanup as soon as
  a resource is acquired. Teardown after `yield` does not run if setup fails
  before the yield ([pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)).

## Assert contracts

Test valid, invalid, boundary and failure cases for every production function.
Assert the value or state that a caller relies on. For a rejection, assert the
exception type and contractual code, attribute or `errno`; also assert no
forbidden write, process or outside-target effect. Put only the expected failing
operation inside `pytest.raises`. Do not match localized operating-system text.
Use independently chosen expected values and invariants, not a copy of the
implementation algorithm. See [pytest assertions](https://docs.pytest.org/en/stable/how-to/assert.html)
and [Google's unit-test guidance](https://abseil.io/resources/swe-book/html/ch12.html).

Use `monkeypatch` or a signature-constrained double at the lookup point for a
specific failure or external call. Assert interactions only when the interaction
is part of the contract. A fake for persistent storage needs a matching contract
case against the real disposable adapter. Doubles cannot establish the real OS,
provider, or installed-hook behavior ([Python mock](https://docs.python.org/3.14/library/unittest.mock.html),
[Google test doubles](https://abseil.io/resources/swe-book/html/ch13.html)).
No default test may require live provider credentials or production resources.

Use deterministic barriers and monotonic deadlines for concurrency. Reap every
child, release locks and handles, restore environment/cwd, and verify outside
sentinels remain unchanged. Never make one test depend on another's workspace.
For a native skip, provide a concrete reason and matching native evidence. A
portable scenario must run on Linux, Windows and macOS. Do not convert a failure
to an expected failure or rerun it to green without a recorded defect and
reviewed disposition ([pytest skip/xfail](https://docs.pytest.org/en/stable/how-to/skipping.html),
[flaky-test guidance](https://docs.pytest.org/en/stable/explanation/flaky.html)).

## Coverage obligations and exceptions

Every AST-listed production and validation-tooling function has a unit behavior
obligation. Pursue **100% of reachable executable lines and meaningful branch and
exception paths per function**. A passing percentage never substitutes for a
behavioral assertion. Coverage arcs show executed control flow, not correctness
or every exception path ([coverage branches](https://coverage.readthedocs.io/en/7.16.2/branch.html)).
Do not add broad `omit`, `pragma: no cover`, or a lower aggregate floor to
silence a gap. The current `*/__init__.py` omission remains the only configured
production omission. Report unit and integration lines and arcs separately on
each native OS. Enforce each function against its own applicable OS report;
execution on one OS cannot erase another OS's gap. An exact reviewed exception
may cover only its explicitly named platforms. Collect tooling separately so the historical production-only
Linux+Windows aggregate remains comparable at **80% unrounded**. macOS evidence
is required separately; it does not rescue that aggregate.

An exception is a reviewed record, never an implicit missing row. Store it in
the gate's exception ledger with these exact fields:

```json
{
  "id": "EX-001",
  "symbol": "agent_company.module:function",
  "source_path": "src/agent_company/module.py",
  "source_sha256": "64 lowercase hex characters",
  "platforms": ["Linux", "Windows", "Darwin"],
  "missing_lines": [12],
  "missing_arcs": [[11, 12]],
  "exception_paths": ["named contractual exception path, if applicable"],
  "reason": "Why the exact path is unreachable or safely covered only at a native boundary",
  "compensating_case_ids": ["tests/integration/...::test_case"],
  "reviewer": "named independent reviewer or role",
  "reviewed_at": "YYYY-MM-DD",
  "expires_at": "YYYY-MM-DD or null"
}
```

Use empty arrays for inapplicable gap kinds. Identify only the exact function,
source version, platform and missing paths. A source change invalidates the
record until review. Native integration can justify a unit exception but never
counts as unit coverage. A platform-irrelevant path must state why that platform
cannot execute it. An independent reviewer checks that the case actually asserts
behavior. Final human PR review remains the acceptance gate.

## Collection and evidence

Use `python scripts/dev.py test-unit --platform-coverage --evidence-dir OUT/OS/unit`
and the matching `test-integration` command on each native OS. Run both suites,
including integration after unit failure. Keep each suite's `.coverage`, JSON,
XML, JUnit, pytest outcome record, instrumentation receipt, manifest and log.
The collector records the exact Git SHA, tracked bytes, clean start/end status,
coverage configuration digest, pinned tool versions, command result and duration.
The quality gate rejects missing, stale, corrupt, duplicated, failed or
misclassified records. Uncommitted local output is diagnostic only. The
combiner uses only successful Linux+Windows native data for the unchanged
aggregate. A test that passes without child instrumentation fails the child-only
smoke. A killed worker may not save coverage; assert its cleanup externally
([pytest-cov subprocess support](https://pytest-cov.readthedocs.io/en/stable/subprocess-support.html),
[coverage subprocess guidance](https://coverage.readthedocs.io/en/7.16.2/subprocess.html)).
`skip_covered` hides fully covered text rows; it does not skip tests
([coverage configuration](https://coverage.readthedocs.io/en/7.16.2/config.html)).

Pin `core = "ctrace"` alongside subprocess instrumentation. On Python 3.14.8
with coverage 7.16.2, a paired registration test passed with either tracer, but
`sysmon` missed two context-manager arcs that `ctrace` recorded. Retain the
measurement regression before changing the core. This fixes measurement rather
than excluding those paths. See the [supported core setting](https://coverage.readthedocs.io/en/7.16.2/config.html#run-core).

Preserve the original native data. The combiner retains it with `keep=True`.
Report restored child measurement separately from newly added tests. Do not
present a coverage increase caused by the patch as proof of new test behavior.

## Change and review rule

For a feature, bug fix or behavior change, identify its function contracts and
native boundaries before editing tests. Add focused success/failure cases, then
the smallest real-boundary scenarios that expose a cross-component fault. Run
the relevant suite, selected static checks and native matrix. A regression test
must fail for the old defect and pass after repair. Reviewers reject missing
behavioral assertions, broad exception catches, unexplained unit gaps, hidden
skips, stale evidence and missing native cleanup assertions.

The Test Quality Check implements the controls below. Local positive, defective
and repaired fixtures verify the controls. Final native candidate results remain
a separate acceptance requirement:

| Control | Status | Limit |
| --- | --- | --- |
| Six native suite records, child smoke, exact identities, collection and outcomes | Enforced by the quality job | Artifacts attest a tested run; they do not authenticate an untrusted runner. |
| Production Linux+Windows >=80% and per-function reachable obligation with reviewed exceptions | Enforced by the quality job | Coverage does not judge assertion meaning. |
| Focused Ruff `F631,PT010,PT011,PT012,PT026,PT030` and three curated wrong-result/missing-exception/omitted-effect faults | Enforced by the quality job | Static rules and selected faults sample only known hazards. |
| Module/suite trend, changed-code gaps, replay/order check | Advisory | Denominators, renames and scheduling can distort simple comparisons. |
| Broad mutation engine and global mutation score | Deferred | Tool/version/platform cost and equivalent mutations need study. |
| Opaque score, assertion counts, test-count ratios, all PT rules, rerun-to-green, live providers | Rejected | These reward superficial counts, add noise or cross authority boundaries. |

Retain durations and failed/blocked named sub-results. Target no more than 180
seconds of new quality analysis beyond native suites; measure the final candidate
before claiming that bound. Human review still judges requirements, case meaning,
exception legitimacy and native behavior. The decision and source evidence are
in the AGENT-30 research context until they are incorporated into the delivery
record. See [Ruff's rules](https://docs.astral.sh/ruff/rules/) and
[coverage combination](https://coverage.readthedocs.io/en/7.16.2/commands/cmd_combine.html).
