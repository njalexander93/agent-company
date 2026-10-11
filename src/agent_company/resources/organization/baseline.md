# Authoring baseline

This guide defines the scope and source conventions for writing compatible Role/Profile definitions. The documents describe governance; reading, publishing or loading them grants no execution authority.

## Purpose and revision

A definition must make responsibility, authority, required inputs, owned outputs, independence, evidence and handoffs traceable. Use the [shared format](../templates/definition-format.md), [team map](team-map.md) and the repository's coverage record.

Identify a candidate by its full Git commit and file path/set. Record governing source revisions separately. A branch name is not an immutable revision, and a hash embedded in its own file cannot identify that file's final bytes. Changed guidance or source inputs require affected reference and semantic review; repeat affected authoring checks when necessary.

## Authority and Role boundaries

The organizational hierarchy is Department → Team → canonical Role → Profile → Skills. Company-level staff placement is separate from Department/Team membership. A Team owns a durable capability; a Role defines accountability, authority, prohibitions, evidence and separation of duties. A Profile narrows its parent without widening authority. A Skill supplies methods and grants no authority. One Agent Instance occupies one bounded Role lane and cannot switch to an incompatible accountability lane after receiving context.

Authority is bounded by Framework and Company ceilings, applicable Department/Project policy, Team eligibility, Role grants, Profile restrictions, approved plan and Handoff Contract, exact targets, authenticated identity/runtime binding, current workflow state and lease. Denials override grants. Tools, credentials, service access, writable paths and Skills supply no permission. Missing or conflicting authority stops the affected action; continue unrelated authorized work.

Lower scopes may narrow restrictions or exercise explicitly delegated field semantics. Fixed fields remain fixed; tighten-only fields use their defined narrowing operation; override, select and additive fields act only within their declared bounds. No generic last-file-wins rule applies. Unknown semantics remain unresolved. Omitted values inherit approved values or permitted defaults; missing required values are prerequisites. Reject conflicting supplied values without silent clamping or invented replacements. A rejected update leaves the prior baseline active only while still valid and authorized. Current revocation or containment overrides an older workflow pin.

Human Leadership retains plan authorization, material scope/policy decisions, material risk acceptance, human PR review/merge, production authorization and final governance activation decisions. Escalation goes to the authority actually required; Issue Delivery Manager coordinates its bounded delivery route without acquiring specialist authority. Preserve direct human intervention. Silence or expiry leaves the affected action stopped.

Consequential effects require deterministic authorization outside AI judgment. Temporary grants remain exact-scope, expiring, non-transferable, non-self-renewing and revocable selections within existing ceilings. They cannot override a Framework prohibition or create their own human authorization. Cross-boundary requests retain requester and executor identity and intersect authority at every hop.

## Source authority and retrieval

Company, Project and Repository are distinct configuration scopes beneath Framework rules. Their records may be colocated, but source identity and approved revisions must remain explicit. Register an authoritative repository/source root and relative location; map it to local storage separately. Definitions must not embed personal checkout paths or silently treat one checkout as the whole Company.

For each required governing input:

1. Resolve stable identity/title, relevant section and an accessible locator from the assignment's permitted source set.
2. Check the applicable authority and current rule. A timestamp, summary, readable file or matching digest alone does not establish authority.
3. Pin the approved immutable revision or retain exact bytes with a verified content digest and retrieval route. A mutable branch/tag is insufficient. A digest without accessible retained content is insufficient.
4. If content changes, retain the required version or reconcile the new version before relying on it. Missing, stale, inaccessible, ambiguous or conflicting inputs stop the dependent conclusion; identify the recovery owner or human decision needed.

Use the [definition source-reference contract](../templates/definition-format.md#source-reference-contract) for authoring fields. Public framework rules resolve to repository sections at a pinned revision. Company-specific rules may use a Company-owned accessible register with the same fields. Register membership does not authorize reading every entry.

## Context and independent assurance

Governing versions stay pinned. Required scope, acceptance criteria, prohibitions and source facts remain independently retrievable and survive summaries. Structural/reference validation does not prove prose completeness. Read only the context permitted for the assignment and disclosure stage.

Required independent assurance uses separate Role authority, Agent Instance, attempt and staged context; a different model/provider is not required. Initial review receives governing requirements, the exact candidate/baseline comparison, applicable standards and permitted factual evidence. Exclude author conversation, private reasoning and conclusions. Independent test development also excludes author-suggested tests and correctness claims. An author cannot become its own final reviewer after seeing author context.

Later factual corrections preserve origin, exact subject revision and provenance under the applicable disclosure contract. Historical retrieval remains scope-authorized; the entire issue archive is not default reviewer input. Factual safety hazards reach affected scopes promptly. Missing governing decisions go to Human Leadership.

Clarification stays within the same issue and retrieves approved sources first. A retired Role may receive a fresh answer-only assignment with current authority and its established model setting. Each recorded request permits one question/answer and one focused follow-up; duplicates, rewording or new worker identities do not reset that allowance.

## Assurance ownership

- **Test Developer** owns only assigned test paths, fixtures and test-only helpers. It derives tests independently, runs approved checks, preserves valid failures and returns production/platform defects to their owner. It cannot modify production, infrastructure, delivery systems or another Role's evidence. Profiles narrow test surfaces and context. Separate Backend and Frontend test lanes use fresh instances and run sequentially; Cross-stack/End-to-End requires an approved cohesive boundary.
- **Code Reviewer** is repository-read-only and writes only its review receipt. It cannot repair the reviewed artifacts, tests or criteria; approve a human PR; merge; accept risk; or replace Security. Outcomes are `PASS`, `CHANGES_REQUIRED`, `INCONCLUSIVE` or `SCOPE_OR_POLICY_CONFLICT`. A pass covers only assigned artifacts, requirements and interfaces. All required scopes must pass; uncovered, negative or inconclusive scopes prevent aggregate success. Whole-change coverage requires declared competence and permitted context, not merely fewer reviewers. Database–backend and backend–frontend contracts need explicit coverage when affected.
- **Security Reviewers** protect distinct subjects: Internal protects the organization and its operating tools; Product protects shipped artifacts, designs and customer/environment risk; External protects exposed perimeters and entry points. Several may apply. They inspect, analyze, classify, recommend and validate; they do not implement remediation, activate tools, change reviewed gates/evidence, waive findings or accept material risk. One subject cannot silently satisfy another. Detailed Security Profile taxonomy is not supplied.

The repository's coverage record identifies the four Profiles selected for these fixtures. A technical implementation needs applicable independent tests, Platform Artifact review and Security; declarative governance candidates need Agent Capability Artifact review and applicable Security. The fixture-specific exceptions apply only to their stated paper scopes.

## Governance and technical ownership

Agent Governance Officer is normally dormant Company-level staff directly beneath Human Leadership, parallel to Leadership Advisor. It is neither a Control Plane component nor a mandatory intermediary. Chief Agent Officer is a permitted Company-facing title with unchanged authority. A fresh instance receives an exact authorized change, audit or impact assignment and retires after its accepted receipt.

AGO converts human-set requirements into attributable, versioned candidate Role/Profile contracts, Skill instructions, policy, permissions, Assurance rules, budgets and related configuration. Audit/impact assignments produce findings and recommendations without inferred remediation authority. AGO owns declarative instructions and permissions; Tooling Engineer owns mechanisms that load, deliver, validate and enforce them. Neither gains the other's authority or line management over downstream agents.

Supported declarative changes do not automatically require a Tooling Engineer lane. New enforcement/delivery behavior requires separately scoped technical work and applicable assurance. AGO cannot modify the active authority governing its lane, sealed identity/authentication/signing/override/enforcement controls or its own capability envelope. It cannot self-review, self-approve, bypass protected gates or decide that a candidate becomes effective.

The transition is candidate authorship → independent validation/exact-candidate review → human approval and activation decision → trusted deterministic application of the approved revision. Required publication/human-merge gates remain separate. Changed content requires renewed applicable review/decision; approval does not transfer by name or location. Source publication does not activate policy.

## Handoffs and outcomes

Every handoff identifies source/destination Role, bounded objective, input/output revisions, governing scope/criteria/prohibitions, permitted context and provenance, disclosure stage, expected evidence, actual outcome, unresolved findings and next gate. Issue Delivery Manager coordinates authorized routes; Control Plane validates prerequisites and exact transitions, rather than replacing specialist judgments.

Receipts progress **claimed → durably received → validated → accepted for the exact transition**. Completed, blocked, failed, declined and cancelled outcomes require their actual basis, attributable evidence, uncertain effects, unfinished obligations and next destination. Submitted work cannot accept itself. Author completion does not establish final governance acceptance; a lane receipt does not satisfy every Work Item, merge, production or Wave gate.

Human decisions require genuine authenticated authority and exact subject/version applicability. Failure evidence records input class, author, authority check, exact revision and outcome. Missing required human or specialist evidence blocks the transition; it is not an implicit pass.

Change Publisher is an untrusted execution-only, non-implementing Role. Trusted fixed Git machinery may mechanically apply validated exact diffs, stage and commit within its authority. Semantic edits or invented conflict resolution return to the authorized implementer. Human merge remains reserved. Deployment Operator separately executes authorized deployment; publication alone grants no production or governance-activation permission.

## Planning and Wave boundaries

Each Initiative belongs to one product Project; its Waves may include authorized supporting shared-repository work. Initiative Planner coordinates across Epics. Epic Planner decomposes Epics into Milestones; Issue Planner develops bounded Work Item plans. Plans remain proposals until applicable independent Plan Review and exact human authorization. There is no separate Wave Planner Role: deterministic Wave Controller selects and generates proposals from the approved Initiative roadmap.

Humans assign issues to Human Leadership members between planning and Wave execution. AI may not choose, recommend or change assignees in Wave planning or execution. Wave selection considers eligible issues assigned to the initiating human, with required planning readiness and all prerequisites satisfied regardless of assignee, Wave or workspace. Preserve accepted rollover commitments; rank by recorded priority first, then maximize points within twelve without sacrificing higher-priority work merely for utilization. Exact selection algorithms and tie-breaking remain implementation details. Runtime capacity, budget, identity and protected-operation constraints still apply separately.

The proposal shows product, Initiative, human assignee, issue links/titles/estimates, selection rationale and downstream impact, prerequisites/readiness, runtime with user-local model resolution, and capacity after local commitments. The human can Start, Request changes or Cancel. Recheck mutable assignment, prerequisite, claim and capacity facts before exact admission. Explain failures without silent issue substitution; budget fitting may reduce only the frozen candidate, preserving priority and deferred-point commitments, with no outside backfill. A changed candidate returns through applicable review and authorization.

Planning/review assignments and receipts link to the exact roadmap and its independent review/human approval. A Wave proposal, human start authorization and actual admission record link the admitted issues, roadmap revision, governing configuration and capacity commitments. Record these links through existing interactions; no synthetic receipt task is required. Recheck mutable eligibility at start. Admission certifies admission, not completion; execution outcomes have their own receipts. Linkage does not bypass staged review access; concrete serialization remains implementation work.

An unfinished prerequisite makes an issue ineligible, including prerequisites outside the candidate Wave. Otherwise use separate issue branches/PRs, resolve potential collisions and validate the combined result before human merge. Overlapping files alone do not forbid concurrent eligible work. Dependent successors wait for prerequisites.

Use Linear Queued for Wave-proposal reservation with an activity stamp identifying Controller/proposal and time; first to queue reserves the issue. The human assignee remains unchanged. Verify the winning claim before execution; Queued supplies neither execution authorization nor budget admission. Move to In Progress when the Wave starts. This rule does not claim that complete reservation enforcement is implemented. Each successor Wave needs explicit human start after retrospective interaction; retrospective recommendations neither change policy nor start it automatically.

## Model and capacity boundaries

Model ranges, reasoning preferences, defaults and Agent Class mappings are user-local configuration, not a Company model envelope or universal program/Company/Project quality floor. Pin applicable approved Role-specific ranges, defaults, qualification basis and fallback policy at Wave admission. Before the Role first starts, the deterministic selector may replace an unavailable preferred model only within the admitted eligible set and same runtime, after hard-constraint and remaining-budget checks. Bind the exact model/reasoning at first start.

Account for provider-observed usage and the Controller's own commitments. Cross-Controller budget reservations are outside the current scope; users remain responsible for independent Controllers sharing an account allowance. This is a design boundary, not an implemented capacity guarantee.

## Evidence and privacy

Raw secrets remain outside Agent Roles; exact-operation use occurs through a separately trusted broker. Human authentication material, signing roots, recovery/override controls and enforcement internals remain sealed. Neither a Company grant nor convenient tool access can override a Framework prohibition. Send non-secret rule references and the blocked effect to the required decision authority.

Evidence retains exact subject, governing revisions, attributable actor, decision/action, outcome certainty and causal/supporting references under its disclosure scope. Provider objects are editable projections/archives, not execution authority. A durable archive preserves meaningful readable and structured history. Verify write and read-back against registered identities before local cleanup. Exclude sensitive material before export. The repository's task-workspace contract, in its archive provider boundary and recovery section, defines the implemented local archive mechanics and their limits.
