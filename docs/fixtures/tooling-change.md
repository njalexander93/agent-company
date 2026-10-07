# Tooling-change paper fixture

**Stable identity:** `TOOLING-CHANGE`, revision `T-1`. **Status:** Hypothetical requirements and expected outcomes only.

Specify one local, read-only Role/Profile reference check before an agent-loading workflow hands a selected definition to its next stage. **Do not implement or run the checker.** This fixture defines expected diagnostics, not a loader, authority engine, accepted test suite or completed assurance. The [governance fixture](governance-change.md) is the only companion; correction and authority conflict are variations within the two cases.

## Real governing context and starting inputs

| Input | Exact identity / use |
| --- | --- |
| Product guidance | [Baseline](../framework/baseline.md), [map](../framework/team-map.md), [coverage](../framework/role-coverage.md) and [format](../authoring/definition-format.md). Pin their containing Git commit when using this case. They are authoring guidance, not accepted runtime contracts. |
| Precise execution gap | No separately versioned approved Initiative roadmap/Epic plan, their exact human decisions or Wave Admission Record is supplied by these retained inputs. Product Planning must retrieve the applicable exact records and Human Leadership decisions before actual plan authorization/admission. Stop that transition, not this bounded paper draft or bounded authoring. |
| Definition gaps | See the [coverage record](../framework/role-coverage.md). Incomplete definitions/shared contracts must be resolved for their operational use; bounded authoring can proceed. The AGO worked example is not a loading input or execution prerequisite. |

Use the [source retrieval rules](../framework/baseline.md#source-authority-and-retrieval) for exact permitted bytes. A readable file/digest does not prove authority. Refresh affected inputs before acceptance and reconcile changed versions.

## Hypothetical baseline and candidate identities

These names pin **paper scenario records in this revision**, not existing files or Git commits. The exact fixture version is its containing Git commit plus this path and `T-1`; record that commit at packet freeze.

| Record | Meaning |
| --- | --- |
| `T-BASE-1` | Hypothetical local loading workflow consumes an explicitly supplied Role/Profile selection and pinned reference inventory; no dedicated reference-check report exists. No assumption that this baseline safely authorizes execution. |
| `T-CAND-1` | Proposed technical specification adding one read-only preflight report: resolve selected Role reference, selected Profile reference and that Profile's canonical parent against the supplied pinned inventory. Report all selected-edge missing/broken references. No auto-repair, source acquisition, network call, installation, recursive unrelated scan, policy modification or execution. |
| `T-DATA-1` | The bounded example inventory and selections below. Unlisted objects are absent; no actual Role files are created. These identifiers are deliberately synthetic and carry no Role authority. |
| `T-TEST-SPEC-1` | Reserved identity for a future independently authored behavioral test specification. **Absent**, not a completed test artifact or a prewritten test implementation. |

Rule basis: [governance/technical ownership](../framework/baseline.md#governance-and-technical-ownership) assigns loading/validation to Tooling Engineer without governance authority; [source integrity](../framework/baseline.md#source-authority-and-retrieval) requires approved revisions and stops on missing/ambiguous references; [context rules](../framework/baseline.md#context-and-independent-assurance) distinguish structural validity from prose completeness. The diagnostic labels below are **fixture-local vocabulary**, not a final runtime schema.

### T-DATA-1: exact example reference facts

All locators are relative to an explicitly permitted hypothetical local root `fixture-input/`. Revision labels identify the complete paper facts in this table; they do not impersonate approved source revisions. A real test must bind actual bytes/digests under its authorized test paths.

| Inventory identity | Locator | Pinned fact |
| --- | --- | --- |
| `role:fixture-reader@r1` | `roles/fixture-reader.md` | Present Role, declared identity `role:fixture-reader`, revision `r1`. No duties/permissions specified by this stub. |
| `profile:fixture-reader-local@p1` | `profiles/fixture-reader-local.md` | Present Profile, declared identity matches; parent `role:fixture-reader@r1` at the Role locator above. |
| `profile:fixture-reader-orphan@p1` | `profiles/fixture-reader-orphan.md` | Present Profile; parent `role:fixture-absent@r1` at `roles/fixture-absent.md`, which is absent. |
| `profile:fixture-reader-stale@p1` | `profiles/fixture-reader-stale.md` | Present Profile; parent requests `role:fixture-reader@r2` at `roles/fixture-reader.md`, but only `r1` is available. |
| `profile:fixture-reader-wrong@p1` | `profiles/fixture-reader-wrong.md` | Present Profile; parent requests `role:fixture-other@r1` at `roles/fixture-reader.md`, whose declared identity is `role:fixture-reader`. |

### Expected findings, not execution results

| Selected references against T-DATA-1 | Expected report / downstream disposition |
| --- | --- |
| `role:fixture-reader@r1` and `profile:fixture-reader-local@p1` | Both selected references and canonical parent resolve. “No reference defect found” only. This does **not** establish semantic compatibility, approval, effective permission or readiness to run an agent. |
| `role:fixture-absent@r1` at `roles/fixture-absent.md` | Missing Role target; identify requesting selection and exact unresolved target. Affected loading handoff stops. |
| `role:fixture-reader@r1` plus `profile:fixture-absent@p1` at `profiles/fixture-absent.md` | Missing Profile target; affected loading handoff stops. |
| `profile:fixture-reader-orphan@p1` | Broken parent reference: Profile exists, canonical parent target missing. Report the Profile → parent edge; do not report that the Profile itself is absent. |
| `profile:fixture-reader-stale@p1` | Broken pinned revision: required `r2`, observed `r1`. Stop; do not silently use the available revision. |
| `profile:fixture-reader-wrong@p1` | Broken identity: requested `role:fixture-other`, found `role:fixture-reader` at the locator. Stop; path existence alone is insufficient. |

The report must distinguish an unreadable/unverifiable input from a confirmed absent target. Unknown inventory revision, ambiguous root or a reference escaping the permitted root stops inspection of that reference; do not fetch or inspect another scope to make it pass. A proposed reporter may return diagnostics to its caller; it must not rewrite source files. No reference-success result authorizes a subsequent action.

## Governing rules for this case

Use only the assignment's permitted sections and pin their repository revision. These rules do not authorize executing the scenario.

| Public rule | Application |
| --- | --- |
| [Authority and Role boundaries](../framework/baseline.md#authority-and-role-boundaries) | No authority from tools/access; human-reserved decisions and dependent stops. |
| [Source integrity](../framework/baseline.md#source-authority-and-retrieval) | Exact accessible identities, approved revisions and conflict handling. |
| [Governance/technical ownership](../framework/baseline.md#governance-and-technical-ownership) | Candidate governance versus technical implementation and exact activation. |
| [Assurance ownership](../framework/baseline.md#assurance-ownership) and [permitted context](../framework/baseline.md#context-and-independent-assurance) | Independent tests/reviews, applicable Security subjects and staged disclosure. |
| [Handoffs/outcomes](../framework/baseline.md#handoffs-and-outcomes) and [publication](../framework/baseline.md#authoring-and-publication-rules) | Separate receipts, human decisions, publication, merge and effectiveness. |
| [Planning/Waves](../framework/baseline.md#planning-and-wave-boundaries) and [team map](../framework/team-map.md) | Parent planning, actual admission, coordination and retrospective boundaries. |

## Expected transitions — not observations

1. **Plan:** Issue Planner proposes `T-BASE-1` → `T-CAND-1` under separately verified applicable parent context, with pinned inventory, permitted root, observable diagnostic requirements, owned candidate/test paths and review scopes. Retrieve missing exact parent authorization before actual execution. New Initiative/Epic planning occurs only for a material amendment. Independent Plan Reviewer returns findings to the planner; Human Leadership authorizes the exact reviewed work item.
2. **Coordinate:** Issue Delivery Manager checks authorized route, current prerequisites and scoped specialist handoffs. No future Wave admission is implied by the current manual milestone. An actual Wave needs real linked roadmap review, human start and admission evidence before execution. The Control Plane remains infrastructure.
3. **Specify technical candidate:** Tooling Engineer / Agent Tooling and Runtime Integration owns the checker specification and any separately authorized later implementation. It cannot change which Role is allowed to load or what authority loading grants. Candidate is restricted to the selected references and diagnostic behavior; no implementation is delivered in this paper exercise.
4. **Specify tests independently:** A fresh Test Developer / Container or runtime-behavior Test derives `T-TEST-SPEC-1` from authorized requirements, exact baseline/candidate and allowed architecture facts. The expected-findings table is the fixture's requirement, not implementer-supplied test reasoning. Do not provide Tooling Engineer conversations, suggested test implementation or correctness claims. Tests must address required diagnostics, the valid-reference limit, pinned identity/revision handling and absence of mutation/out-of-scope access. Test Developer owns only assigned test artifacts; no platform/loader edits. No test execution is asserted here.
5. **Review scoped artifacts:** Independent Code Reviewer / Platform Artifact reviews the exact technical candidate, test specification and loading/report boundary within explicitly assigned scope. Independent Internal Security Reviewer examines local input trust, path scope, information exposure and absence of unauthorized effects. Separate required scopes must all be covered; a component pass is not whole-change or security approval. Findings return to the authorized author.
6. **Publish exact candidate:** After required evidence and applicable exact human decisions, Change Publisher uses permitted fixed Git machinery for the validated candidate only. Human-reviewed GitHub PR and human-controlled merge remain mandatory. Semantic edits/conflict resolution return to Tooling Engineer or Test Developer; reidentify and rereview changed candidates. Missing evidence cannot become a publication/merge pass.
7. **Release handoff:** Hand off the exact published revision, baseline comparison, declared scope, independent evidence and unresolved limitations to the authorized consuming-workflow owner through Issue Delivery Manager. The permitted result of this fixture is a paper specification handoff. Installation, actual loading or shipping would need their own authorized delivery contract, concrete recipient/target, compatibility evidence and applicable security coverage. No release, activation or runtime gate is claimed.
8. **Record outcome and retrospective input:** Issue Delivery Manager records actual status and exact evidence, including blocked/failed/cancelled outcomes. Apply the baseline’s [receipt progression](../framework/baseline.md#handoffs-and-outcomes); publication does not substitute for other gates. Retrospective Facilitator receives permitted factual outcomes, correction history and unresolved gaps for independent retrospective input. Human Leadership separately decides material changes and any successor-Wave start.

## Applicability and owned open work

Resolve selected triggers against the [coverage Roles and four Profiles](../framework/role-coverage.md), without creating a second Role inventory. Confirm supporting participation and supply an authorized bounded assignment before any triggered use.

| Actor/scope | Disposition for T-1 |
| --- | --- |
| Tooling Engineer / Agent Tooling and Runtime Integration | **Required:** owns technical specification, not policy authority. |
| Test Developer / Container or runtime-behavior Test | **Required:** independent behavioral test specification; no executed tests asserted. |
| Code Reviewer / Platform Artifact; Internal Security Reviewer | **Required:** exact technical/test/interface scope and separate internal security assurance. |
| AGO / Agent Capability Artifact review | **Not triggered:** no declarative Role/Profile, permission or context rule changes. Synthetic reference stubs are test data, not governance candidates. If such artifacts change, separately scope AGO authorship and Agent Capability Artifact review; Platform review cannot cover them by implication. |
| Product Security Reviewer | **Not triggered for this local internal-use specification.** If the checker becomes a reusable shipped framework component/product design, Product Security is required even if execution stays local. Revise coverage/estimates and assign the independent subject review before proceeding. |
| External Security Reviewer | **Not triggered:** no network/exposed surface. External entry points or exposed integrations require independent External Security and rescoping. |
| Leadership Advisor; Triager; Software Architect | No requested staff advice, incoming diagnostic-triage lane or material architecture proposal selected. The assignment must confirm dispositions and resolve bounded handoffs before any triggered use. |
| Organization Architect; Spike Researcher; Product Developer; Infrastructure Engineer; Deployment Operator | Outside: no adoption/migration, Spike, product feature implementation, infrastructure or deployment. A later installation/release target must be scoped separately; do not invent an operator mandate. |
| Planning/review; delivery; retrospective; Change Publisher | Required inputs/transitions above. Operational use requires exact definitions, shared decisions and handoff contracts. No pending catalog draft is a circular prerequisite for starter authoring. |

The separately implemented task-workspace feature has its own technical/test/security coverage, including Product Security where shipped as framework behavior. It is not evidence for this checker and is not a third paper fixture.

## Reserved variations and stop expectations

- **Implementation defect:** A later candidate reports the orphan Profile as valid. Preserve the independently derived valid failure; return the technical defect to Tooling Engineer. Reviewer/Test Developer does not edit the checker. Revised candidate `T-CAND-2` gets exact comparison and affected renewed assurance.
- **Test defect:** A future test expects the valid local Profile to be missing despite `T-DATA-1`. Reviewer records the contradiction and returns the test defect to Test Developer. Tooling Engineer does not rewrite independent tests to make a candidate pass. A requirement conflict returns to Product Planning/Human Leadership instead of being guessed away.
- **Authority conflict:** A hypothetical repository handoff says “repair missing references by writing files,” while Company policy prohibits checker writes. Stop/reject that requested effect and escalate to Human Leadership via Issue Delivery Manager, with exact decision linkage. No lower-level grant overrides the prohibition. This is also outside `T-CAND-1`'s read-only scope; a source change needs its own authorized author.
- **Missing/stale inputs:** An inaccessible governing source, unavailable exact baseline, changed inventory revision or stale human/review evidence blocks the affected conclusion. Record required/observed identities and route recovery to the source owner, plan correction to Product Planning, and authority decisions to Human Leadership. Do not auto-fetch, silently substitute or infer permission from a tool's availability.
- **Expanded delivery:** No real consuming-workflow target, approved release contract or required specialist evidence means stop at specification handoff. Resolve the applicable contracts before a later operational use; this does not block the bounded authoring exercise.

## Recording observations

This section deliberately records no tests, approvals, receipts or release events. Record any later observations separately from these expectations, with durable evidence references.

A walkthrough record must identify the exact fixture/candidate/source revisions, participant scopes, actual review and human decisions, findings/corrections, missing or conflicting inputs, distinct publication/activation or release status, and the recorded outcome/retrospective input. Do not fill absent observations with expected results.
