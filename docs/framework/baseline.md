# Authoring baseline

The authoritative organizational rule set is the packaged [organizational baseline](../../src/agent_company/resources/organization/baseline.md), which the 2026-10 relocation of the organizational rules into the package moved into the `agent_company` package together with the team map, definition format and templates. This file keeps only the contributor sections for this repository: paper fixtures, working locations, authoring and publication rules, setup and interpretation limits. Start with the [authoring route](../README.md). The documents describe governance; reading, publishing or loading them grants no execution authority.

## Selected paper fixtures and exclusions

| Fixture | Bounded purpose | Expected trace, not performed actions |
| --- | --- | --- |
| [Governance change G-1](../fixtures/governance-change.md) | G-BASE-1 → G-CAND-1 adds one completion-evidence table to a hypothetical internal Company Issue Delivery Manager contract: each required source’s ID, pinned revision/content identity and verified-current/missing/stale/conflicting status, with affected stopped transition and escalation destination. References/status only; no raw secrets or private reasoning. | Planning and delivery coordination; Agent Governance Officer candidate authorship; independent artifact/security review; exact human decision; publication and distinct activation handoff; recorded outcome and retrospective input. |
| [Tooling change T-1](../fixtures/tooling-change.md) | T-BASE-1 → T-CAND-1 specifies a read-only report for the selected Role, selected Profile and canonical parent against the pinned T-DATA-1 inventory. Expected findings cover missing targets, stale parent revision and wrong identity. No repair, acquisition or execution; T-TEST-SPEC-1 remains future independent output. | Tooling Engineer ownership; independent tests and scoped review; applicable security; exact-candidate publication/release handoff; recorded outcome and retrospective input. |

**Fixture Profiles and exceptions.** The [coverage record](role-coverage.md#four-profile-definitions--separate-from-role-count) identifies the four Profiles selected for these fixtures. The fixture-specific exceptions to the packaged [assurance ownership](../../src/agent_company/resources/organization/baseline.md#assurance-ownership) rule apply only to their stated paper scopes.

Both fixtures model internal, local work as paper cases. Correction, authority conflict and missing/stale inputs are variations within them. Expected outcomes are not observations, receipts, approvals or runtime proof.

Neither fixture supplies an approved Initiative roadmap/Epic plan, exact human authorization or Wave Admission Record. Product Planning and Human Leadership must supply those before an actual authorization/admission transition. Stop that transition; bounded authoring can continue. See [Planning and Wave boundaries](../../src/agent_company/resources/organization/baseline.md#planning-and-wave-boundaries).

The pack does not supply a full Role catalog, implemented Skills, the reference checker, final machine schemas, runtime loaders, distribution packaging or general runtime authority enforcement. The separate [task-workspace utility](../runtime/task-workspace.md) has its own implementation and host limitations. Expanding either fixture into shipped behavior, exposed surfaces, setup or infrastructure requires revised scope and applicable specialist coverage.

## Working locations and ownership

- **Public rules:** This baseline, the [team map](../../src/agent_company/resources/organization/team-map.md), [coverage record](role-coverage.md) and [shared format](../../src/agent_company/resources/templates/definition-format.md) provide the rules needed to author definitions. Company-specific assignments add their own accessible governing material.
- **Guidance and examples:** `docs/` contains documentation. The [worked AGO example](../examples/agent-governance-officer.md) illustrates authoring; it is not a packaged runtime Role.
- **Future runtime assets:** Actual runtime Role resources belong under `src/agent_company/resources/roles/` in the packaged build. This location does not supply a loader or activate a Role. Do not duplicate the example there.
- **Working context:** The registered main worktree owns `.task/<issue-id>/`; participating worktrees expose issue-specific views under the [workspace contract](../runtime/task-workspace.md). Links confer no filesystem permission.
- **Delivery evidence:** Keep project tracking, research, conversations, private reasoning and exact-candidate review history in access-controlled delivery records. These are not public product rules or default author/reviewer inputs.

## Authoring and publication rules

1. **Accessible guidance.** Framework and Company authors use these public rules plus governing material accessible and permitted for their assignment. Complete Company-only onboarding and distribution are not supplied by this pack.
2. **Independent authoring and review.** To test guidance, freeze a scoped product/source packet and ask a fresh author to create a definition without a prewritten answer or upstream reasoning. A separate reviewer checks the exact output. Repair missing guidance and repeat affected checks. This tests documentation usability, not runtime isolation.
3. **Human-reviewed publication.** Repository changes require a human-reviewed GitHub PR into `main` and human-controlled merge. Candidate authorship, independent assurance, human decision, source publication and governance activation remain distinct. Opening a proposal PR supplies no approval or activation authority.

**Contributor exception to [Planning and Wave boundaries](../../src/agent_company/resources/organization/baseline.md#planning-and-wave-boundaries).** The narrow exception is an explicitly initiated repository contributor issue: follow the [contributor procedure](../runtime/contributor-workflow.md#1-establish-the-master-and-issue-state) to assign that issue to its resolved initiating human. This exception does not change Wave selection, reservation or assignment policy.

## Setup boundary

Reuse existing service identities and access for integrations; no separate Agent Company account, enrollment ceremony or human-authentication system is supplied. Setup requires usable access to Linear, GitHub and the Obsidian knowledge integration; connection/setup investigation covers all three and remains separately scoped. Protected permission grants, exact human decisions and exceptional recovery remain explicit. The [development guide](../runtime/development.md) covers the implemented contributor environment, not complete Company onboarding or an end-user installer.

## Interpretation and limitations

- These are normative documentation rules, not proof of implemented enforcement, runtime isolation, accepted definitions or certification. A document's publication does not authorize an operational lane.
- The [team map](../../src/agent_company/resources/organization/team-map.md) defines placement; [coverage](role-coverage.md) records the 22 Roles, four selected Profiles and known definition gaps. Detailed Security taxonomy and complete runtime definitions remain unfinished.
- The [worked example](../examples/agent-governance-officer.md) is an authoring aid, not an installed Role. Review always applies to exact bytes; record dispositions outside product examples.
- Workspace protocol tests do not establish installed-host callback delivery, trust, provider round trips, other operating-system support or final acceptance. See [task-workspace limitations](../runtime/task-workspace.md).
- The repository's [LICENSE](../../LICENSE) governs this checkout. A local build does not establish installer, release-pipeline or distribution readiness.
