# Authoring baseline

This guide defines the scope and source conventions for writing compatible Role/Profile definitions. Start with the [authoring route](README.md). The documents describe governance; reading, publishing or loading them grants no execution authority.

## Purpose and revision

A definition must make responsibility, authority, required inputs, owned outputs, independence, evidence and handoffs traceable. Use the [shared format](definition-format.md), [team map](team-map.md) and [coverage record](role-coverage.md).

Identify a candidate by its full Git commit and file path/set. Record governing source revisions separately. A branch name is not an immutable revision, and a hash embedded in its own file cannot identify that file's final bytes. Changed guidance or source inputs require affected reference and semantic review; repeat affected authoring checks when necessary.

## Selected paper fixtures and exclusions


| Fixture | Bounded purpose | Expected trace, not performed actions |
| --- | --- | --- |
| [Governance change G-1](fixtures/governance-change.md) | G-BASE-1 → G-CAND-1 adds one completion-evidence table to a hypothetical internal Company Issue Delivery Manager contract: each required source’s ID, pinned revision/content identity and verified-current/missing/stale/conflicting status, with affected stopped transition and escalation destination. References/status only; no raw secrets or private reasoning. | Planning and delivery coordination; Agent Governance Officer candidate authorship; independent artifact/security review; exact human decision; publication and distinct activation handoff; recorded outcome and retrospective input. |
| [Tooling change T-1](fixtures/tooling-change.md) | T-BASE-1 → T-CAND-1 specifies a read-only report for the selected Role, selected Profile and canonical parent against the pinned T-DATA-1 inventory. Expected findings cover missing targets, stale parent revision and wrong identity. No repair, acquisition or execution; T-TEST-SPEC-1 remains future independent output. | Tooling Engineer ownership; independent tests and scoped review; applicable security; exact-candidate publication/release handoff; recorded outcome and retrospective input. |

Both fixtures are internal, local paper cases. Correction, authority conflict and missing/stale inputs are variations within them. Expected outcomes are not observations, receipts, approvals or runtime proof.

Neither fixture supplies an approved Initiative roadmap/Epic plan, exact human authorization or Wave Admission Record. Product Planning and Human Leadership must supply those before an actual authorization/admission transition. Stop that transition; bounded authoring can continue. See V4, Planning and roadmap handoff, and V6, Wave integration.

The pack does not supply a full Role catalog, implemented Skills, the reference checker, final machine schemas, runtime loaders, distribution packaging or general runtime authority enforcement. The separate [task-workspace utility](task-workspace.md) has its own implementation and host limitations. Expanding either fixture into shipped behavior, exposed surfaces, setup or infrastructure requires revised scope and applicable specialist coverage.

## Working locations and ownership

- **Guidance and examples:** `docs/` contains documentation. The [worked AGO example](examples/roles/agent-governance-officer.md) illustrates authoring; it is not a packaged runtime Role.
- **Future runtime assets:** Actual runtime Role resources belong under `src/agent_company/resources/roles/` in the formal packaged build. That location is a packaging boundary, not a claim that runtime Role assets or a loader are supplied. Do not duplicate the example there.
- **Governing knowledge:** Framework contributors resolve the register's paths relative to their accessible shared-vault root. Company authors use accessible framework contracts and company-owned sources. Do not put personal checkout paths in definitions.
- **Working context:** The registered main worktree owns `.task/<issue-id>/`; participating worktrees expose issue-specific views. `roadmap.md`, scoped `context/` notes and bounded `events.jsonl` diagnostics follow the [workspace contract](task-workspace.md). Links confer no filesystem permission.
- **Delivery records:** Keep project tracking, research, private snapshots, conversations and exact-candidate review history in the work tracker or shared knowledge vault. They are not runtime resources or required authoring instructions.

## Confirmed authoring and publication decisions

1. **Separate audiences.** Framework authors currently use shared-vault design sources. Company authors use accessible framework guidance and their own governing sources. The private team vault is not a universal prerequisite. Complete company-only onboarding and distribution are not supplied by this pack.
2. **Independent authoring and review.** To test guidance, freeze a scoped product/source packet and ask a fresh author to create a definition without a prewritten answer or upstream reasoning. A separate reviewer checks the exact output. Repair missing guidance and repeat affected checks. This tests documentation usability, not runtime isolation.
3. **Human-reviewed publication.** Repository changes require a human-reviewed GitHub PR into `main` and human-controlled merge. Candidate authorship, independent assurance, human decision, source publication and governance activation remain distinct. See V1, Coordination and separation rules; V5, Change Publisher; V7, Human Leadership requirement and correction.

## Source authority and retrieval

Interpret historical records through V0 and applicable later decisions. A newer timestamp, readable file or matching digest alone does not establish authority. Use only the source sections permitted for the assignment; a register is not permission to read the whole vault. Research notes and conversations are not implicit authoring inputs.

1. Resolve the listed path from the accessible shared-vault root and confirm the stable ID/title.
2. Read the relevant section and check its applicability through current interpretation.
3. Pin the exact current or retained version in the assignment's source packet. The hashes below identify the captured Markdown bytes, not historical front-matter `source_sha256` values. If current content differs, obtain the retained version from the source custodian or retain/reconcile the new version before relying on it.
4. Missing, stale, inaccessible or conflicting required sources stop the affected conclusion. Preserve the gap and escalation destination; continue unrelated bounded work.

Company-specific authors use an accessible source register with the same identity, section, locator, revision, retrieval and authority fields. They do not need this team's private source history.

## Governing source register

| ID / stable record ID | Title and relevant sections | Current locator | Retained content SHA-256 |
| --- | --- | --- | --- |
| V0 / `CURRENT-INTERPRETATION` | Current Interpretation — Current interpretation. | `Decisions/Current Interpretation.md` | `fcd701d2871e3c0d0cc0e363f0f780a10d0f29cf55270d39f281cd26123427b4` |
| V1 / `SPEC-ORGANIZATION` | Engineering Organization Working Model — Accepted hierarchy, hierarchy rule, team charters, coordination, authority. | `Specifications/Organization/Engineering Organization Working Model.md` | `dc9cef6af4cb3317432a60bc6c2ec3a07569cdc52b879a37f6ad6b9d965b01aa` |
| V2 / `SPEC-REPOSITORY` | Repository Blueprint — Status, repository responsibilities, proposed tree, structural rules. | `Specifications/Runtime/Repository Blueprint.md` | `8e85f21765024b805b3230af4da6ab940cec8a5290e073f06eefb92ed1c0191f` |
| V3 / `SPEC-PRINCIPLES` | Design Principles — Independent assurance, ownership/handoffs, least privilege, policy before personality. | `Specifications/Governance/Design Principles.md` | `a0170d9622e6468c724ae7269477dbf4d88f1137091a0d734df195cda25a5811` |
| V4 / `SPEC-INITIALIZATION` | Initialization and Adoption — Source/revision identity, portable references, governance/tooling/human boundaries. | `Specifications/Initialization/Initialization and Adoption.md` | `eeca6d2b3c4b01973af90212a2a12538a82175b821727adea4da5346ae1932a6` |
| V5 / `RC-02` | RC-02 — Component responsibilities and decision rights — Component responsibilities, Change Publisher, missing-evidence failure. | `Specifications/Runtime/Contracts/RC-02.md` | `8a0eaed9836d3725b9c9579ea1d6c508bf1557158ebaac5587acaae4b6557c84` |
| V6 / `RC-07` | RC-07 — Context, receipts and communication — Context rules, receipt progression, stale/missing input, archive and Wave linkage. | `Specifications/Runtime/Contracts/RC-07.md` | `1e97f36d6915d292e6b951de6993112fd40f4a4ddc37634c93d2be6029e79192` |
| V7 / `DISC-L3904` | Question 5.1: Agent Governance Officer placement and V1 activation — Accepted placement, candidate authorship, Tooling Engineer boundary. | `Sources/Discovery/Round 5/DISC-L3904 - Question 5.1 - Agent Governance Officer placement and V1 activation.md` | `4a40e86f3e9cdc0fd3fd02eb8cf980dcc26ad4a1cfa9fa410d9b73ac4ed236bf` |
| V8 / `DISC-L2719` | Question 3.20: Test Developer role structure — Runtime-behavior Profile and independent test/write boundaries. | `Sources/Discovery/Round 3/DISC-L2719 - Question 3.20 - Test Developer role structure.md` | `7037670855d8b03b177ccd7d41b5860f820d6b7852ce7898718957949c518267` |
| V9 / `DISC-L2753` | Question 3.21: Code Reviewer specialization and multi-domain aggregation — Platform/Agent Capability artifact Profiles, scoped review, aggregation. | `Sources/Discovery/Round 3/DISC-L2753 - Question 3.21 - Code Reviewer specialization and multi-domain aggregation.md` | `04799354fcca88ea74f5921dc7ef26f8b91fbb13aea283161b6b4c4761319063` |
| V10 / `DISC-L2905` | Question 3.22: Security assurance role structure — Three protected subjects and assurance-only boundaries. | `Sources/Discovery/Round 3/DISC-L2905 - Question 3.22 - Security assurance role structure.md` | `e963c9093e6fb4176bdf57f8ee4bddb06979861d2500e61ff91fa22cb469a764` |
| V11 / `DISC-L2981` | Question 3.24: Security Role profiles — Deferred taxonomy, candidate areas not accepted Profiles. | `Sources/Discovery/Round 3/DISC-L2981 - Question 3.24 - Security Role profiles.md` | `cc5926a9bb6976edfa48cd608ae1fdd5976d5c3b24aacde7b7edb5fb426d8fb1` |
| RC-09 / `RC-09` | RC-09 — Observability, provenance and audit — Logging and evidence contract | `Specifications/Runtime/Contracts/RC-09.md` | `c6aa6cb524065070d386cabe2b46e3fee7e7b2d498dcdc58906d85b3a459145e` |
| DEC-105 / `DEC-105` | Accept the reconciled Round 7 closeout and close Round 7 — Initialization and Project inheritance — Acceptance | `Decisions/Records/DEC-105.md` | `aac3b228c2a9ab5de708e5ff5a11b2c2d44af5b5988414a2d24e4913deff9c8f` |
| RUNTIME / `REG-CONTRACTS` | Runtime Logical Contracts — Status and common requirements | `Specifications/Runtime/Runtime Logical Contracts.md` | `1914d7b47cd04965b2a4fa950fbc165f53bcaeb56f7d1c87d4cb9035ac0488cb` |
| R7-AUDIT / `AUDIT-R7` | Round 7 Closeout Audit — Closeout disposition; final acceptance | `Sources/Audits/Round 7 Closeout Audit.md` | `7da9bcc7f08f8d02905117290d914712d8233e8a47c386fe2b3602055247e9f3` |
| DEC-052 / `DEC-052` | Pin approved Role-specific model/capability and reasoning ranges, defaults, qualification basis and fallback policy at Wave admission. Permit the deterministic selector to replace an unavailable preferred model before that Role first starts, only within the admitted eligible set and same selected runtime, after hard-constraint and remaining-Wave-budget checks. Bind the exact model/reasoning at first Role start — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-052.md` | `9cb628cee421d5735ca0272593259eb6ac35ae5f3e3c7214a1b38a1320b8ab58` |
| DEC-055 / `DEC-055` | Distinguish Company, Project and Repository policy/configuration scopes beneath Framework rules, with flexible colocation and explicitly registered authoritative sources, stable identities, approved versions and verifiable references — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-055.md` | `e55b013c108d4449a421d2bb14be8c18082cda8a5b9a41b401a4ad6f7381ee71` |
| DEC-063 / `DEC-063` | Each Initiative belongs to one product Project; Waves belong to that Initiative and may include authorized supporting shared-repository work — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-063.md` | `830ca847d5a632178d93ff6a98eb3df36c5964c073750f565b18d2f22e3164fc` |
| DEC-070 / `DEC-070` | Configure runtime model ranges and personal model/reasoning preferences in user-local plugin configuration rather than Company, Project or Repository policy — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-070.md` | `aae510e51a9ef27011e2052f5b816b8ee83e523f21dbab438bb3568d207e2439` |
| DEC-071 / `DEC-071` | Resolve issue Agent Classes through user-local model/reasoning ranges; impose no universal program, Company, Project or Repository model-quality eligibility gate for the POC — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-071.md` | `cf7042dd35a990ef54dfc1ef599875abba32e5c2ba53e599a58612b40d2324f7` |
| DEC-077 / `DEC-077` | Use local setup with existing service identities for the POC, without separate Agent Company accounts; investigate Obsidian, Linear and GitHub connections in one three-part Spike — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-077.md` | `cd736d2365f15db2c85ea7f798ffa0d29d2d4ebd11d09c416ed4b7f0a6ced62d` |
| DEC-078 / `DEC-078` | Defer separate Agent Company human enrollment and authentication for the POC; rely on existing Linear identity/assigned work, GitHub repository permissions and Obsidian vault access — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-078.md` | `2340f3ea20aa6e0132a78c708351dbce744a45eb68854d159e66b24fc8c19a23` |
| DEC-098 / `DEC-098` | Wave Controller deterministically selects and generates Wave proposals from the approved Initiative roadmap; do not add a Wave Planner Role — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-098.md` | `cf55ec917421fd8828c24a725dec2aa23c60181103ea6a4a734b6ca4583bcfe1` |
| DEC-099 / `DEC-099` | Rename Project Planner to Epic Planner as the current working Role title — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-099.md` | `8177aa23c0330f8316d8d4b9d6d957b412427b132bbc4fda43825e705852edba` |
| DEC-100 / `DEC-100` | Automatically link genuine planning/review evidence and human approval of an exact roadmap revision to the proposed Wave, start authorization and Wave Admission Record — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-100.md` | `99221a76f004e40e0e9c76d03c8fd04c5712e276cc8366d62903bcb5786b3848` |
| DEC-101 / `DEC-101` | Remove automatic Wave advancement from the current POC baseline; require an explicit human start for each successor Wave after the retrospective interaction — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-101.md` | `9dc8512338b503bba994eef82f5b80434ec24c934851f31af7f441720e34f435` |
| DEC-102 / `DEC-102` | Exclude any issue blocked by any unfinished prerequisite task; isolate eligible issue changes on their own branches/PRs and resolve code collisions before merge — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-102.md` | `20c29d5f9ea1d9f5a95fcc8f339decb528bb090edab579d1ea9ef97890fc58dc` |
| DEC-103 / `DEC-103` | Use Linear Queued for Wave-proposal reservation, with an issue activity stamp identifying the Controller/proposal and timestamp; first to queue receives the reservation. Move to In Progress when the Wave starts — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-103.md` | `9bc124fdee9e942d66e9bd434ed2f0cd9b68ea1c797f07f706c5ae606622eef8` |
| DEC-104 / `DEC-104` | Exclude cross-Controller resource/budget reservation coordination from the POC; users are responsible for running independent Controllers against the same account allowance — Decision as recorded; Recorded status; Rationale as recorded | `Decisions/Records/DEC-104.md` | `1ef730865bf3ad1156a5ec263d573c2b8fbc098c228a9017c6abb66cda2ea545` |


## Interpretation and limitations

- V1 supplies accepted placement and charters. V8/V9 supply detailed Test Developer and Code Reviewer Profiles; the high-level diagram alone is not a complete Profile inventory. V11 deliberately defers Security Profile taxonomy.
- V0 and DEC-098–104 control current planner naming, deterministic Wave proposals, explicit human successor start, prerequisite checks, separate issue changes and bounded reservation/accounting. V6's logical linkage does not prove a runtime schema or isolation.
- Known definition gaps are listed in [coverage](role-coverage.md). Missing authority remains a blocker rather than a drafting assumption.
- The [worked example](examples/roles/agent-governance-officer.md) has been relocated and edited for product documentation. Earlier review applies only to its historical revision. The edited candidate needs its own review; it is not an installed or activated Role.
- Workspace protocol tests do not establish installed-host callback delivery, trust, provider round trips, other operating-system support or final acceptance. See [task-workspace limitations](task-workspace.md).
- The repository's [LICENSE](../LICENSE) governs this checkout. A local build does not establish installer, release-pipeline or distribution readiness.
