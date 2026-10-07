# Governance-change paper fixture

**Stable identity:** `GOVERNANCE-CHANGE`, revision `G-1`. **Status:** Hypothetical requirements and expected outcomes only.

This case adds one evidence requirement to an internal Issue Delivery Manager contract. It supplies an assignment context, not an AGO Role definition or an answer key. No candidate policy, approval, receipt, activation or runtime result exists here. The companion [tooling fixture](tooling-change.md) is the only other fixture. Correction and authority-conflict variations remain inside these two cases.

## Real governing context and starting inputs

| Input | Exact identity / use |
| --- | --- |
| Product guidance | [Baseline](../baseline.md), [map](../team-map.md), [coverage](../role-coverage.md) and [format](../definition-format.md). Pin their containing Git commit when using this case. They are authoring guidance, not accepted runtime contracts. |
| Parent execution gap | The permitted retained inputs do not provide a separately versioned approved Initiative roadmap/Epic plan, their exact human decisions or a Wave Admission Record. If the walkthrough advances to actual plan authorization/admission, Issue Planner must retrieve these through Product Planning and Human Leadership. Stop that transition until exact inputs exist. This gap does **not** prevent bounded paper authoring. |
| Definition gaps | See the [coverage record](../role-coverage.md). Incomplete definitions/shared contracts must be resolved for their operational use; bounded authoring can proceed. The AGO worked example is not a loading input or execution prerequisite. |

Pin the [public governing rules](#governing-rules-for-this-case) and permitted Company inputs at exact revisions. Refresh affected sources before acceptance; record changed identities and reconcile meaning. Hash equality proves content identity, not acceptance.

## Selected hypothetical delta

The following labels identify **scenario records defined by this text**, not real files, Git revisions or accepted policies. The immutable identity of this fixture is its containing Git commit plus this path and revision; record that commit when freezing a packet.

| Scenario record | Definition |
| --- | --- |
| `G-BASE-1` | Hypothetical internal Company Issue Delivery Manager contract. Its completion evidence already binds the exact Work Item/plan revision, outcome and required receipts under the baseline’s handoff/outcome rules. It has no separately enumerated source-status table. All existing duties, prohibitions, authority and context limits remain inherited. |
| `G-CAND-1` | Same contract plus **one required completion-evidence table**: for every required governing input, give registered source ID, pinned revision/content identity and a status of verified-current, missing, stale or conflicting. Any missing/stale/conflicting required input identifies the affected stopped transition and escalation destination. The table contains references/status, never raw secrets or upstream private reasoning. |
| `G-REQ-1` | Hypothetical Human Leadership requirement to add exactly that table in one candidate contract. It is a proposed scenario premise; no real human authorization is claimed. Before actual work, replace it with authenticated exact-scope requirements and authorization. |

**Mechanism:** Direct versioned candidate Role-contract authorship, already supported by the [governance ownership rule](../baseline.md#governance-and-technical-ownership). The baseline supplies [source pinning](../baseline.md#source-authority-and-retrieval), [evidence and stale-input rules](../baseline.md#handoffs-and-outcomes); the delta makes their application explicit in one Role's completion evidence. It introduces no new field-resolution algorithm, schema, technical validator, loader or live-policy edit. Do not invent a machine manifest field or assume an implementation exists. If an eventual runtime cannot carry this evidence through its existing contract mechanism, stop and separately scope Tooling Engineer work.

## Governing rules for this case

Use only the assignment's permitted sections and pin their repository revision. These rules do not authorize executing the scenario.

| Public rule | Application |
| --- | --- |
| [Authority and Role boundaries](../baseline.md#authority-and-role-boundaries) | No authority from tools/access; human-reserved decisions and dependent stops. |
| [Source integrity](../baseline.md#source-authority-and-retrieval) | Exact accessible identities, approved revisions and conflict handling. |
| [Governance/technical ownership](../baseline.md#governance-and-technical-ownership) | Candidate governance versus technical implementation and exact activation. |
| [Assurance ownership](../baseline.md#assurance-ownership) and [permitted context](../baseline.md#context-and-independent-assurance) | Independent tests/reviews, applicable Security subjects and staged disclosure. |
| [Handoffs/outcomes](../baseline.md#handoffs-and-outcomes) and [publication](../baseline.md#authoring-and-publication-rules) | Separate receipts, human decisions, publication, merge and effectiveness. |
| [Planning/Waves](../baseline.md#planning-and-wave-boundaries) and [team map](../team-map.md) | Parent planning, actual admission, coordination and retrospective boundaries. |

## Expected transitions — not observations

1. **Plan:** Issue Planner uses verified applicable parent context to propose the narrow `G-REQ-1` work item, input versions, paths, assurance and stops. Initiative/Epic Planner involvement is an input requirement; a new planning lane is required only for a material parent amendment. Independent Plan Reviewer supplies findings without editing the plan. The planner repairs defects. Human Leadership authorizes the exact reviewed plan before a governance authoring lane starts. Paper context does not supply parent authorization.
2. **Coordinate:** Issue Delivery Manager checks current prerequisites and prepares the bounded AGO handoff. Control Plane is the source-defined validation boundary, not a Role or proof of implemented enforcement. No Wave is invented; if using one later, its real exact-roadmap review, human start and admission evidence must precede its execution.
3. **Author candidate:** AGO receives authorized human requirements, pinned baseline, necessary facts and candidate-only write scope. It proposes the exact `G-BASE-1` → `G-CAND-1` contract delta. It cannot modify the active policy governing its own lane, sealed controls or its capability envelope. Its authored candidate goes to independent assurance.
4. **Assure independently:** Code Reviewer / Agent Capability Artifact reviews the exact contract diff against requirements and governing sources. Internal Security Reviewer separately assesses evidence disclosure, trust and authority. Their initial packets exclude author reasoning and conclusions; permitted facts and safety hazards remain available. Findings are separate artifacts; missing or inconclusive required review blocks progression.
5. **Decide exact governance:** After required independent evidence, Human Leadership decides whether to approve or reject the exact candidate digest and its proposed activation scope. A revision invalidates transfer of approval; reassess and renew affected evidence/decisions. No author, reviewer or publisher substitutes for this reserved decision. Record the actual decision subject/version if this is ever performed.
6. **Publish source:** Change Publisher may mechanically publish only the exact validated, authorized candidate through its permitted Git route. It cannot make semantic edits or resolve conflicts by invention. Apply the baseline's mandatory GitHub PR, exact-candidate human review and human-controlled merge gates. If a PR is opened earlier to present the candidate for decision, opening it is proposal publication only: it neither supplies approval nor permits merge/activation. Preserve assurance-before-human-approval-before-activation ordering.
7. **Separate activation:** Source-control publication/merge does not make policy effective. Only after exact human activation authorization and required publication/merge gates may trusted deterministic machinery apply that approved digest to the authorized target. Recheck applicability/current prohibitions and verify the result. Missing machinery, identity, scope or evidence stops activation; this fixture proves none of them. AGO and Change Publisher cannot activate by declaring success.
8. **Record and learn:** Issue Delivery Manager records completed, blocked, failed, declined or cancelled outcome with exact candidate, decisions, review findings and distinct publication/activation status. Receipt progression is claimed → durably received → validated → accepted for its exact transition. Retrospective Facilitator receives permitted factual outcome/evidence for independent retrospective input. Recommendations do not change policy or start a successor Wave; Human Leadership retains that separate decision.

## Applicability and owned open work

These dispositions resolve the selected delta against [coverage entries 01–22 and four Profiles](../role-coverage.md). Confirm supporting participation before operational use; these expectations are not acceptance.

| Actor/scope | Disposition for G-1 |
| --- | --- |
| Test Developer / Container or runtime-behavior Test | **Not triggered** by a prose completion-evidence requirement with no runtime behavior change or technical enforcement claim. Independent artifact/security review still applies. If acceptance changes to verifying actual runtime enforcement/behavior, stop and add this lane before claiming that outcome. |
| Tooling Engineer / Agent Tooling and Runtime Integration; Platform Artifact review | **Not triggered:** no technical/platform artifact changes. Unsupported evidence transport or new validation behavior triggers a separately authorized technical lane and applicable tests/review. |
| AGO; Agent Capability Artifact review; Internal Security | **Required**, with separate authorship and assurance boundaries above. |
| Product Security | **Not triggered for this internal Company-only contract paper delta.** A reusable shipped framework/product governance behavior or design change requires Product Security, revised coverage and estimates before proceeding. Internal Security cannot satisfy it. |
| External Security | **Not triggered:** no perimeter, network entry point or exposed integration. Exposure requires revised scope and independent External Security review. |
| Leadership Advisor; Triager; Software Architect | No human-requested advice, incoming diagnostic triage or material architecture proposal is selected. The assignment must confirm these supporting dispositions and bounded handoffs if triggered; no mandate is inferred. |
| Organization Architect; Spike Researcher; Product Developer; Infrastructure Engineer; Deployment Operator | Outside: no setup/migration, research Spike, product implementation, infrastructure or deployment. Trusted governance activation is not invented as a Deployment Operator assignment. Rescope before adding these subjects. |
| Planning/review; delivery; retrospective; Change Publisher | Required inputs/transitions as above. Operational use requires exact definitions, permission decisions and handoff/context/outcome contracts. These known drafts do not block the starter trial. |

## Reserved variations and stop expectations

- **Correction:** Independent review finds `G-CAND-1` omitted the stopped-transition/destination columns, or admitted private author reasoning. Return the contract defect to AGO; reviewer writes findings only. A corrected `G-CAND-2` needs a new exact diff and affected renewed assurance/human decision. A future test defect goes to Test Developer, not AGO or reviewer.
- **Authority conflict:** In this same case, a hypothetical lower-level handoff permits raw credential text as evidence while Company policy prohibits agent raw-secret access. Stop that disclosure and reject the conflicting candidate; retain non-secret source/rule references. Issue Delivery Manager coordinates escalation to Human Leadership with exact decision linkage. Do not treat a Company decision as permission to override the Framework raw-secret prohibition.
- **Missing/stale input:** A required source, baseline digest, human decision or review cannot be retrieved or no longer matches. Stop the dependent conclusion; report identity, affected transition, needed correction and responsible destination. Product Planning resolves plan context, source owner restores content, Human Leadership resolves authority, and the relevant author repairs artifacts. Unrelated bounded drafting may continue.
- **Unsupported mechanism or scope expansion:** Do not convert the paper assumption into claimed runtime support. Return to planning, scope technical ownership/assurance, and obtain applicable authorization before that path. No invented execution receipt unblocks it.

## Recording observations

Expected outcomes above are **not** test results. When performing a walkthrough, record observations separately in a durable scoped evidence record. Authoring usability checks and independent example review require separate evidence; neither establishes integrated workflow acceptance.

A walkthrough record must identify the exact fixture/candidate/source revisions, participant scopes, actual review and human decisions, findings/corrections, missing or conflicting inputs, distinct publication/activation or release status, and the recorded outcome/retrospective input. Do not fill absent observations with expected results.
