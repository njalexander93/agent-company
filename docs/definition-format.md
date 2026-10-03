# Shared Role and Profile format

**Define bounded responsibilities, not personalities.** These Markdown templates describe a Role and a Profile for review. They do not grant execution authority, implement Skills or finalize a machine/runtime schema. A Profile only narrows its canonical parent Role. A Skill supplies methods and grants no authority. [V1, Hierarchy rule; V3, Policy before personality; L1/LM — see Sources below.]

## Authoring route

1. Identify the canonical Role and owning unit from accepted organizational sources. Framework authors use the [entry point](README.md) and [team map](team-map.md). Company authors use accessible framework contracts and company-owned governing material; the team's private vault is not a prerequisite.
2. Resolve the assignment's permitted governing sources, exact versions and relevant sections. Use the [source-reference contract](#source-reference-contract). Required scope, acceptance criteria and prohibitions must survive summaries. Read only context permitted for this assignment and disclosure stage.
3. Copy the appropriate template. Replace every placeholder with sourced content or an explicit disposition from [Completeness and gaps](#completeness-and-gaps). Link shared rules instead of recreating them.
4. Check the draft against [Review and maintenance](#review-and-maintenance). Record field completeness, reference verification and semantic review separately. A structurally complete document can still misstate authority.

For the fresh-author trial, the frozen packet supplies product guidance and explicitly permitted governing records. **SPEC is research provenance, never required fresh-author reading.** Research notes, upstream author reasoning and conversations are not implicit inputs. The single Agent Governance Officer example is deliberately absent until the fresh author creates it; this format supplies no completed Role-specific answer. [Baseline, confirmed decisions D01/D02; RC-07, Context rules.]

## Role template

Use canonical identity from the governing register. The headings below are human-readable fields, not serialized keys. Identify the exact definition revision in its review record by immutable version or full commit and file path; a mutable branch name is insufficient.

```markdown
# <Canonical Role name>

## Identity and owning unit
- Canonical identity: <registered ID/name and source reference; display alias if used>
- Owning unit: <accepted Team/Department or Company-level placement; source>
- Definition status and revision: <draft/review disposition; exact review-record link>

## Responsibility
<Bounded objective, participation trigger and accountable scope; governing references.>
<Distinguish this responsibility from adjacent Roles and protected decisions.>

## Required inputs
| Input and relevant scope | Required / conditional trigger | Exact revision and accessible reference | Entry check and missing/stale/conflict route |
| --- | --- | --- | --- |
| <Plan, assignment, candidate/baseline, contract or source fact> | <Required, or explicit condition> | <Source/evidence reference> | <Check, stop boundary and destination> |

## Owned outputs and write boundaries
| Output | Permitted action and exact target scope | Excluded writes / adjacent owner | Governing reference |
| --- | --- | --- | --- |
| <Artifact or evidence owned by this Role> | <Inspect/propose/write/etc.; bounded paths or targets> | <What this Role must return to another owner> | <Source and section> |

## Shared-policy references
<Applicable shared permission, decision, context and outcome rules: source + section.>
<Identify conditions and human-reserved decisions needed for this responsibility.>

## Prohibitions
<Concrete forbidden actions and scope; cite the canonical rule for each.>
<Where a prohibited or ambiguous request goes; no implied permission from tools.>

## Independence and permitted context
- Required context: <facts, contracts and exact revisions; reference input rows>
- Initial disclosure: <permitted artifacts; prohibited upstream context>
- Later disclosure: <trigger, permitted evidence and provenance requirements>
- Separation: <required Role/instance/attempt separation and write boundaries>
<Applicable shared context contract and handling of factual safety hazards.>

## Completion evidence
| Obligation / acceptance criterion | Required artifact or check | Exact subject/version and outcome evidence | Validator / next gate |
| --- | --- | --- | --- |
| <Scoped obligation> | <Evidence expected, not a fabricated result> | <Required identity, result and locator> | <Source-defined recipient/gate> |
<Define evidence needed for applicable completed, blocked, failed, declined or
cancelled outcomes by reference to the shared outcome contract. Record unresolved
findings and limitations. Distinguish expected evidence from actual evidence.>

## Handoff and escalation destinations
| Trigger | Destination Role / decision authority | Bounded payload and required evidence | Next decision / gate |
| --- | --- | --- | --- |
| <Normal completion, correction, missing input or authority conflict> | <Source-backed destination> | <Input/output revisions, outcome, unresolved findings, provenance> | <Applicable shared contract> |

## Needed Skill capabilities
| Needed method and when used | Existing Skill reference/version or explicit gap | Authority boundary |
| --- | --- | --- |
| <Method capability needed for the responsibility> | <Verified reference, or needed—not implemented; owner/next action> | <Role/Profile scope still controls> |

## Governing sources and open gaps
<Use the source-reference contract; map substantive claims to source sections.>
<For each gap: affected field/path, known drafting owner or decision destination,
next action, and readiness effect. Use an explicit justified disposition if none.>
```

### Role field guidance

| Field | Adequate content |
| --- | --- |
| Identity / owning unit | Preserve canonical identity and accepted placement. A display title does not create a Role or change authority. Staff placement need not invent a Team. |
| Responsibility | State what event calls for this Role, what it must produce and where its accountability ends. “Be thorough” is not a boundary. |
| Required inputs | Name necessary facts and artifacts, versions, conditions and entry checks. “Relevant context” alone is insufficient. An untriggered conditional input needs a recorded reason; an unknown trigger is unresolved. |
| Outputs / writes | Name the artifact, action and bounded target. Separate inspecting, proposing, modifying, approving and publishing. A reusable definition may point to an assignment's exact paths; it must explain how those paths are resolved before work starts. |
| Shared policy / prohibitions | Cite the governing rule and its relevant section. Describe its application without inventing a parallel permission table. Availability of a tool, credential or writable folder supplies no authority. |
| Independence / context | State who must be separate and what each stage may receive. A separate title alone does not prove independence. Keep accepted facts available without importing prohibited upstream reasoning. |
| Completion evidence | Give observable exit criteria, exact artifact identity, results and unresolved findings. “Done,” confidence, or the existence of a file cannot replace required evidence or independent review. |
| Handoff / escalation | Identify the receiving responsibility and decision actually needed. Include correction and blocked routes, not only success. An unassigned protected decision is a gap; do not invent a human assignee or quorum. |
| Sources / Skills | Sources justify duties and boundaries. Skills describe methods. Reference only needed capabilities and distinguish available, verified Skills from future work. |

For example, “write only assigned test fixtures; return production defects to their owner” expresses a boundary; “help improve quality” does not. The actual assigned paths and governing test contract must still be supplied. This illustrates field quality, not a completed definition. [V8, shared authority and test-surface boundaries.]

## Profile template

**Pin one canonical parent and describe the narrowing.** Parent duties, prohibitions, evidence and independence requirements remain applicable. A Profile cannot add authority, waive a parent requirement, widen writes or admit context the parent excludes. More context is not automatically permitted context. [V1, Hierarchy rule; V3, Least privilege; V8/V9, Profile boundaries.]

```markdown
# <Profile name>

## Canonical parent Role
<Canonical identity, accessible definition locator, exact parent revision and
relevant sections. State Profile status and exact candidate review-record link.>
<Inherit the parent's owning unit, duties, policies, prohibitions, independence,
completion evidence and handoff rules; identify any unresolved parent draft.>

## Specialization
<Domain, artifact class, technology or Project; participation trigger.>
<Explain the subset of parent responsibility covered and the excluded surfaces.>

## Required context
| Context and relevant section | Required / conditional trigger | Exact reference/version | Permitted disclosure stage and missing-input route |
| --- | --- | --- | --- |
| <Specialized governing facts/artifacts> | <Requirement or condition> | <Accessible verified identity> | <Parent-compatible stage and route> |

## Additional constraints
| Parent boundary and reference | Profile restriction | Why this is narrowing | Evidence / handoff effect |
| --- | --- | --- | --- |
| <Responsibility, targets, tools, writes or context> | <Subset or stricter condition> | <Show compatibility> | <Additional requirement; retain parent obligations> |
<Explicitly retain unchanged boundaries by parent section reference.>

## Needed Skill capabilities
<Needed method + trigger + verified Skill reference/version or explicit
implementation gap. Reuse the Role's capability-reference fields.>
<Loading a Skill neither widens this Profile nor changes parent authority.>

## Governing sources and open gaps
<Source references for specialization and constraints; inherited parent references
must resolve. Record affected scope, owner/destination, next action and readiness
for unresolved inputs. Identify the relevant coverage entry.>
```

A parent reference must resolve to content, not merely a name. If the parent definition is assigned downstream, record its draft/version or absence and the owning issue. Stable accepted sources can support drafting, but parent compatibility remains unverified until the exact parent is reconciled. Do not present that Profile as accepted or ready for execution. [L1/LM; baseline, known drafting gaps.]

A narrower artifact review still needs every assigned interface covered; separate component passes do not imply interface coverage. Deferred taxonomy is not an accepted Profile list. Use detailed accepted sources when the high-level diagram is incomplete. [V9, Multi-domain aggregation; V11, detailed design deferral.]

## Completeness and gaps

**Every required field needs substantive content or a justified disposition.** Apply these distinctions to both templates:

| Disposition | Meaning and required explanation |
| --- | --- |
| Complete | A reader can identify the duty, boundary or evidence without the author's help, and trace it to accessible governing content. Placeholders and headings alone do not count. |
| Not applicable — reason/source | An applicable rule or scope makes this field's particular obligation irrelevant. Name that rule and scope. Never use this to hide a required responsibility or missing evidence. |
| Intentionally absent — reason/source | The accepted boundary deliberately excludes a capability or output. For example, no implementation writes for a repository-read-only review. Record the explicit exclusion; a blank cell is ambiguous. |
| Conditional | State the trigger and required input/evidence if triggered. Record whether it applies to the selected path and why. Uncertainty about a required trigger blocks the affected conclusion. |
| Known drafting gap | Accepted meaning exists, but its downstream definition/contract is unfinished. Name the owning issue, next action and reconciliation gate. Continue independent drafting; do not claim the absent artifact exists. |
| Unknown / conflicting / unavailable | Identify the missing decision, stale version, inaccessible source or conflicting sections; state the affected path and decision destination. Stop that conclusion and continue unaffected work. Do not resolve authority by inference or recency. |

A source can be readable and correctly hashed yet lack governing authority. An authoritative source can be unavailable to the assigned author and therefore unusable for the affected step. Neither source verification nor a populated template establishes review acceptance. [V0; V3, Least privilege; RC-07, Context rules and Human boundary/failure.]

## Source-reference contract

Each definition uses a common register instead of creating a competing source inventory. For this starter pack, use the [baseline register](baseline.md#governing-source-register) and its [retained-version retrieval route](baseline.md#source-authority-and-retrieval). A company may use its own accessible register and retained sources with these same fields.

| Reference part | Required content |
| --- | --- |
| Identity | Stable registered source ID and title; distinguish a short local citation key from the canonical ID. |
| Applicable section | Heading, clause or bounded passage supporting the specific claim. “See policy” is insufficient. |
| Accessible locator | Repository path, document URL or registered root-relative location the intended reader can resolve. Name the root/retrieval route; avoid an author's machine-specific absolute path. |
| Verified version / content identity | Immutable revision or retained exact bytes with a verified digest. Record which content the digest identifies; a historical original-file hash may differ from the current record's bytes. |
| Common register / retrieval | Link the exact register entry and the route to reopen that version. Inherit identity details from that entry only when the mapping is unambiguous and retrievable. A digest without retained content is insufficient. |
| Authority and review status | Cite the acceptance/current-interpretation record that makes this source applicable. Separately identify the definition's actual review disposition and exact reviewed candidate, or mark review pending. |

A compact citation may read: `<source ID / title>, §<section>; <register-entry link> (locator, verified revision and retained retrieval); authority: <applicable decision reference>`. Fill these fields from verified content. Do not use this placeholder as a real source.

Resolve each substantive claim to its section, reopen the permitted version, compare its identity and check current applicability. A register entry is provenance, not permission to read all its contents. Keep permitted source packets scoped; the task workspace is working data, not governing authority. [Baseline, Source authority and retrieval; RC-07, Context rules/Archive.]

## Shared rules and needed methods

**Reference canonical shared rules.** V3 governs authority and independence; RC-02 governs decision rights and transition evidence; RC-07 governs context and receipt progression. Received artifacts, accepted evidence, execution authorization, publication and activation remain distinct. A completion receipt is validated for an exact transition; it cannot satisfy every later gate. These are paper contract requirements here, not claims of implemented enforcement.

[AGENT-6](https://linear.app/ne3ko93/issue/AGENT-6/define-shared-permissions-and-governance-revision-decisions) owns shared permission rows and exact-revision governance decisions. [AGENT-7](https://linear.app/ne3ko93/issue/AGENT-7/define-handoff-context-and-outcome-contracts) owns handoff, context and outcome contracts. Reference accepted source rules while those drafts develop; reconcile their exact revisions before the affected definitions are accepted. This format does not fill unknown decisions, invent approval machinery or serialize runtime receipts. [LM; L6/L7.]

Needed starter methods can include source/reference verification, bounded drafting, independent behavioral testing, scoped artifact review, security assessment, and evidence/handoff preparation. Select only those needed by the definition and cite the governing responsibility. Record a verified existing Skill locator/version or **needed—not implemented**, with an owner/next action. These capability descriptions are not Skill IDs, implementations or permission grants. Milestone 2 owns reusable Skills/procedures. [L1/LM; V1; V8/V9.]

## Review and maintenance

1. **Fields:** Check every Role field and each Profile's parent, specialization, context and additional constraints. Resolve all placeholders. Inspect justified dispositions and required/conditional inputs.
2. **Sources:** Reopen each required reference at the recorded identity. Check its section, applicability and reader access. Preserve unavailable/conflicting-source blockers rather than treating a link or digest check as acceptance.
3. **Meaning:** Compare duties, writes, prohibitions, evidence and destinations with accepted sources and the team map. For every Profile restriction, show compatibility with the exact parent. Check staged context and adjacent owners. Required independent review remains separate from this author check.
4. **Coverage and fixtures:** Reconcile affected entries in the [single coverage record](role-coverage.md) and expectations in the [governance-change](fixtures/governance-change.md) and [tooling-change](fixtures/tooling-change.md) fixtures. Keep required, conditional and outside-scope participation consistent with the selected fixture dispositions; preserve expansion triggers and assigned downstream work. Linked artifacts do not establish trial completion, runtime success or final acceptance.
5. **Changes:** When a source rule or parent changes, identify all affected Roles/Profiles, policy references, coverage entries and fixture expectations. Retain the changed source identity; update affected artifacts in the same review or explicitly linked work with an owner and readiness effect. Recheck references and semantics; repeat affected authoring/review trials. A material candidate change requires applicable renewed review/decision, not transferred approval.
6. **Evidence:** Record the exact candidate, checks, failures/repairs, unresolved gaps and actual reviewer decisions at a durable evidence locator. Apply the baseline's human-reviewed GitHub PR requirement. Paper expectations remain separate from actual receipts, runtime tests, human acceptance and activation.

The authoring trial, independent example review and integrated acceptance remain downstream. No AGO example, answer key, Skill implementation or runtime test result is supplied by this document. [Baseline, confirmed decisions and acceptance evidence; L6; RC-02/RC-07.]

## Sources

Keys below resolve to stable IDs, titles, locators and verified retained content identities in the [common baseline register](baseline.md#governing-source-register). Internal retrieval instructions remain in the [entry point](README.md). Use only the records/sections permitted for the assignment.

| Key | Sections supporting this format |
| --- | --- |
| L1 / AGENT-1 | Scope: Role/Profile fields, source-access boundary, exclusions; Acceptance Evidence. |
| LM / Milestone 1 — Usable organization and governance draft | Ownership and shared rules; Dependencies and drafting overlap; Completion and limits. |
| V0 / CURRENT-INTERPRETATION | Current interpretation; Later walkthrough context. |
| V1 / SPEC-ORGANIZATION | Hierarchy rule; Coordination and separation rules; Authority and runtime boundary; selected owning-unit charter. |
| V3 / SPEC-PRINCIPLES | Independent assurance; Evidence over confidence; Explicit ownership and handoffs; Least privilege; Policy before personality. |
| V5 / RC-02 | Component responsibilities table; Change Publisher; Evidence/failure. |
| V6 / RC-07 | Context rules; Receipt progression; Archive; Human boundary/failure; Wave integration. |
| V8 / DISC-L2719 | Decision: shared Test Developer authority and Profile narrowing. |
| V9 / DISC-L2753 | Decision: shared Code Reviewer authority; Multi-domain aggregation rule. |
| V11 / DISC-L2981 | Decision — detailed design deferred to a Spike. |
| L6 / AGENT-6 | Permission/revision-decision fields; Acceptance Evidence. |
| L7 / AGENT-7 | Minimum handoff fields; Acceptance Evidence. |

**Delivery provenance only:** SPEC R07/R08/R19/R21 and D01–D03 informed this delivery. The baseline states the applicable audience, trial and PR decisions directly. SPEC and research reasoning are excluded from the fresh-author packet.
