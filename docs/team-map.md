# Department and team map

**Two peer departments; six teams.** This is a compact map of the accepted organization, not a Role catalog or an authority grant. Read V1 through V0 and the later decisions listed below. Source IDs resolve through the [baseline register and retained-input retrieval](baseline.md#source-authority-and-retrieval).

## Placement and authority

```text
Human Leadership
├── Company-level staff Roles (not a department/team)
│   ├── Organization Architect
│   ├── Leadership Advisor
│   └── Agent Governance Officer
├── Product Management Department
│   ├── Product Planning Team
│   └── Delivery Management Team
└── Engineering Department
    ├── Product Engineering Team
    ├── Engineering Assurance Team
    ├── Security Engineering Team
    └── Platform Engineering Team
```

This grouping of staff Roles is a display aid, not a new organizational unit. Product Delivery is cross-department policy, not a third department. Department Directors remain hypotheses gated by registered Spikes; no Team Manager Role exists in V1. [V1: “Purpose,” “Accepted POC hierarchy,” “Coordination and separation rules.”](#governing-sections)

- **Human Leadership** retains plan authorization, material scope/policy decisions, material risk acceptance, pull-request review and merge, production authorization, and final governance decisions/activation.
- **Organization Architect** provides temporary Company setup/adoption, migration and audit guidance. It produces inventory, structural proposals and outcome reports, coordinates separately scoped work, hands off and retires. It adds no line authority.
- **Leadership Advisor** is Company-level staff with no line authority.
- **Agent Governance Officer (AGO)** is elastic Company-level staff. It authors candidate governance artifacts from human-set requirements and advises Human Leadership. Tooling Engineer retains technical implementation; Human Leadership and trusted machinery retain protected decision/activation authority.

These are placement references, not full staff definitions. [V1: “Accepted POC hierarchy,” “Coordination and separation rules.”](#governing-sections)

**Capability ownership does not grant approval, publication or activation authority.** Teams own durable capabilities; executable accountability belongs to bounded Roles. Profiles narrow Roles. Skills provide methods and grant no authority. Independent assurance and human decisions remain separate from implementation and execution. [V1: “Hierarchy rule,” “Coordination and separation rules,” “Authority and runtime boundary”; RC-02: “Component responsibilities and decision rights.”](#governing-sections)

## Departments

Department summaries aggregate their accepted child charters; they add no department-level executive Role or output contract.

| Department | Owned capability and expected outputs | Adjacent boundaries | Escalation destination |
| --- | --- | --- | --- |
| **Product Management** | Product planning and authorized delivery coordination. Outputs: planning proposals/review evidence, bounded coordination requests, Wave Retrospective Packages and recommendations. | Engineering owns technical implementation and specialist assurance. Coordination cannot perform specialist work or rewrite Project artifacts. | Human Leadership for plan authorization and material scope/policy decisions. During delivery, Issue Delivery Manager coordinates the authorized route; it cannot resolve protected decisions itself. |
| **Engineering** | Product behavior/architecture, independent engineering/security assurance, tooling/infrastructure and execution mechanics. Outputs: scoped implementation or architecture proposals, assurance evidence, tooling/infrastructure changes, authorized publication/deployment outcomes. | Product Management owns planning/coordination. Implementation, independent assurance, publication, human merge and production authorization remain distinct. AGO owns candidate governance authorship, not tooling implementation. | Human Leadership for material scope/policy/risk and protected merge/production decisions. Issue Delivery Manager coordinates bounded Work Item blockers through the authorized route. |

**Sources for both rows:** [V1: “Accepted POC hierarchy,” all six “Team charters,” “Coordination and separation rules,” “Authority and runtime boundary”; RC-02: coordination request and Human Decision rows.](#governing-sections)

## Teams

Escalation destinations below apply V1's rule: route to the authority actually required, preserve direct Human Leadership intervention, and fail the affected scope closed on silence or expiry. They do not create a standing manager chain. Issue Delivery Manager coordination applies to its authorized Work Item scope; Control Plane validation supplies no specialist judgment. [V1: “Authority and runtime boundary”; RC-02: coordination request and evidence/failure.](#governing-sections)

### Product Planning Team

- **Owns:** Initiative, Epic/Milestone and Work Item planning proposals; triage, Spike planning, risk facilitation, dependencies and governed amendments.
- **Outputs:** Scoped plans and amendments, independent Plan Review evidence, triage/research recommendations. Operational audit triage produces event-cited diagnostics, not authoritative evidence or state changes.
- **Boundaries:** Epic Planner is the current title; the former Project Planner is not a second Role. Wave Controller generates Wave proposals from the approved Initiative roadmap. Delivery Management coordinates authorized execution. Plans remain proposals until applicable independent review and human authorization.
- **Escalation:** Human Leadership for plan authorization or material scope/policy decisions; preserve independent Plan Reviewer judgment. Planning does not authorize itself.

**Source:** [V1: “Team charters → Product Planning Team”; DEC-098/099: “Decision as recorded,” “Rationale as recorded”; RC-02: planning proposal row.](#governing-sections)

### Delivery Management Team

- **Owns:** Operation of authorized Work Item Plans and independent Wave retrospectives.
- **Outputs:** Bounded lane-coordination/handoff requests, blockers and escalations; Wave Retrospective Package and recommendations.
- **Boundaries:** Issue Delivery Manager cannot perform specialist work or modify Project artifacts. Retrospective Facilitator cannot modify policy or authorize its recommendations. Control Plane validates readiness/routes. Each successor Wave requires explicit human start after retrospective interaction; retrospective completion does not start it.
- **Escalation:** Human Leadership for decisions beyond the authorized plan, policy changes and successor-Wave start. Specialist judgments remain with the responsible specialist lane.

**Source:** [V1: “Team charters → Delivery Management Team,” “Round 7 workflow interfaces”; DEC-101: “Decision as recorded,” “Rationale as recorded”; RC-02: coordination request row.](#governing-sections)

### Product Engineering Team

- **Owns:** Approved product behavior and material product architecture.
- **Outputs:** Path-scoped Product Developer implementation; Software Architect proposals.
- **Boundaries:** Architecture proposal and implementation are separate Roles. Engineering Assurance and Security Engineering provide independent evidence. Platform Engineering owns tooling, infrastructure and promotion mechanics. Implementers cannot supply their own final assurance, merge or production authorization.
- **Escalation:** Issue Delivery Manager for bounded delivery blockers; Human Leadership for material scope/policy changes or protected decisions. Architecture proposals do not authorize implementation.

**Source:** [V1: “Team charters → Product Engineering Team,” “Coordination and separation rules,” “Authority and runtime boundary”; RC-02: coordination request row.](#governing-sections)

### Engineering Assurance Team

- **Owns:** Independent test and code-review evidence.
- **Outputs:** Test Developer evidence and separate Code Reviewer judgments, including applicable integration-contract review.
- **Boundaries:** Database–backend and backend–frontend connections require their own coverage; component approval does not imply boundary approval. Assurance does not replace implementation, Security review or protected human decisions.
- **Escalation:** Issue Delivery Manager for delivery coordination of findings/blockers; Human Leadership for material scope/policy decisions. Missing required specialist evidence blocks the transition rather than becoming an implicit pass.

**Source:** [V1: “Team charters → Engineering Assurance Team,” “Coordination and separation rules”; RC-02: coordination request and evidence/failure.](#governing-sections)

### Security Engineering Team

- **Owns:** Assurance of three distinct protected subjects: the internal organization, exposed perimeter and shipped product.
- **Outputs:** Independent security findings, classifications, recommendations and validation evidence for every applicable subject.
- **Boundaries:** Internal, External and Product Security Reviewer cannot substitute for or waive one another. Reviewers do not implement remediation, activate tools, change reviewed gates/evidence or accept material risk. Internal Security does not replace Product Security for shipped framework behavior or External Security for exposed surfaces.
- **Escalation:** Human Leadership for material risk acceptance and protected decisions; Issue Delivery Manager coordinates delivery blockers and separately scoped remediation. Negative or inconclusive findings cannot be self-waived.

**Source:** [V1: “Team charters → Security Engineering Team”; V10: “Role boundaries,” “Overlap and aggregation”](#governing-sections)

### Platform Engineering Team

- **Owns:** Developer tooling, repository/delivery systems, managed infrastructure, publication mechanics and authorized deployment execution.
- **Outputs:** Tooling/infrastructure changes and bounded publication/deployment execution outcomes.
- **Boundaries:** Tools and Infrastructure are descriptive fields, not hierarchy layers. AGO retains candidate governance authorship. Change Publisher and Deployment Operator are distinct execution-only Roles. Publisher uses trusted fixed Git machinery; semantic edits or invented conflict resolution return to an authorized implementer.
- **Escalation:** Authorized implementer for semantic corrections; Issue Delivery Manager for delivery blockers; Human Leadership for merge, production authorization and material policy/risk decisions. Publication does not activate governance.

**Source:** [V1: “Team charters → Platform Engineering Team,” “Accepted POC hierarchy,” “Coordination and separation rules”; RC-02: “Change Publisher.”](#governing-sections)

## Infrastructure and downstream work

**Control Plane is infrastructure outside the hierarchy.** It includes deterministic Wave Controller, workflow/gate evaluation, contract/lease/receipt/context routing, ledger/projections, provider synchronization and evidence registry. It neither becomes a Team member nor replaces independent specialist judgment or human authorization. No Wave Planner Role is added. [V1: “Accepted POC hierarchy,” “Coordination and separation rules”; DEC-098; RC-02.](#governing-sections)

**Known drafting work is not an unknown authority decision.** The [coverage record](role-coverage.md) distinguishes 22 canonical Roles from the smaller selected definition set. It records responsibilities and fixture participation without assigning new authority. The [worked AGO example](examples/roles/agent-governance-officer.md) is documentation, not a runtime Role.

The high-level hierarchy is not an exhaustive Profile inventory. Security Profile taxonomy remains deliberately deferred. If a required source or governing authority decision is missing/conflicting, block the affected path and route the decision to Human Leadership; do not fill it with a new unit, Role or permission. No such source conflict was found for this map at the registered input versions. [V11: “Decision — detailed design deferred to a Spike”; V1: “Authority and runtime boundary.”](#governing-sections)

## Governing sections

These are **shared-vault-relative locators**, not repository paths. Use the [baseline source register](baseline.md#governing-source-register) for retained bytes, stable IDs and retrieval. Company-specific authors use accessible framework contracts and their own governing context; this internal retrieval route is not their prerequisite.

| Key / stable ID | Vault-relative path | Sections used |
| --- | --- | --- |
| V0 / `CURRENT-INTERPRETATION` | `Decisions/Current Interpretation.md` | Current interpretation through DEC-105. |
| V1 / `SPEC-ORGANIZATION` | `Specifications/Organization/Engineering Organization Working Model.md` | Purpose; Accepted POC hierarchy; Hierarchy rule; Team charters (all six named subsections); Coordination and separation rules; Authority and runtime boundary; Round 7 workflow interfaces. |
| RC-02 / `RC-02` (baseline V5) | `Specifications/Runtime/Contracts/RC-02.md` | Component responsibilities and decision rights: producer/gate/output table; Change Publisher; Evidence/failure. |
| V10 / `DISC-L2905` | `Sources/Discovery/Round 3/DISC-L2905 - Question 3.22 - Security assurance role structure.md` | Role boundaries; Overlap and aggregation. |
| V11 / `DISC-L2981` | `Sources/Discovery/Round 3/DISC-L2981 - Question 3.24 - Security Role profiles.md` | Decision — detailed design deferred to a Spike. |
| DEC-098, DEC-099, DEC-101 | `Decisions/Records/DEC-098.md`, `Decisions/Records/DEC-099.md`, `Decisions/Records/DEC-101.md` | Decision as recorded; Rationale as recorded (each). |
