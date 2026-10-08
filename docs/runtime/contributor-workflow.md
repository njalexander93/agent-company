# Contributor issue workflow

Use this procedure when asked to start or resume a repository issue, including a short “start AGENT-X” request. The initiating thread is the **master**. An explicitly assigned child follows the [step-thread route](#step-thread-route), not a second master workflow.

This is the canonical contributor procedure required by [root instructions](../../AGENTS.md). It defines agent behavior; prose alone does not enforce it. Preserve the [lifecycle contract](task-workspace.md), scoped ownership, recovery rules, supported platforms and [human-controlled publication](../framework/baseline.md#authoring-and-publication-rules).

## Before ordinary tools: bootstrap readiness

1. Use identity already supplied by the host and authorized setup: the issue identifier, actual host/session identity, chosen checkout and explicit main worktree. A new issue also needs its known immutable issue UUID, explicit coordinator participant and reader-specific startup packet. Existing issues need that session’s coordinator-assigned packet. Obtain missing inputs through the host’s user-input/setup route; never invent identities or fetch them through an unready provider call.
2. Follow the [supported bootstrap route](task-workspace-usage.md#explicit-setup-and-bootstrap) and [host tool shape](host-hooks.md#setup-and-bootstrap). The checkout runtime and canonical command must already be available from setup. Submit exactly one `Task: <issue-id>` line. Use only exact allowlisted lifecycle commands: `diagnose`/`register` as needed, with explicit `startup` for a new issue, or the applicable `bind`/`resume`/recovery route for existing state. Startup supplies creation and scope; `create` and `scope` are not unready-tool exceptions.
3. Use scoped `read`, then `acknowledge` with the returned exact packet digest. The next covered tool checks readiness; after acknowledgment, verify `ready` through the supported lifecycle route before stage 1. Ordinary issue reads/writes, Git inspection, file reads and command-generation scripts have no bootstrap exemption. Preserve the [existing exceptions and trust boundary](task-workspace.md#bootstrap-exceptions).

**Exit:** readiness is established before ordinary issue operations. If identity, startup assignment, runtime, canonical invocation or recovery is missing, report the exact prerequisite and recovery action. Continue only independent work already authorized on a ready path. Bootstrap does not count as a verified issue transition: stage 1 still sets and reads back In Progress at the first supported assignment start, including planning.

## 1. Establish the master and issue state

1. After bootstrap readiness, fetch the live issue by identifier. Read its full criteria and applicable governing inputs through the [task catalog](../README.md). Inspect existing authorized work, branches, PRs and recorded progress before starting duplicate work.
2. Resolve the **initiating human** from explicit session evidence and a verified provider identity. Connector “me”, issue creator and service-account identity are not sufficient. If identity is ambiguous, ask for the human and resolve the provider user before assigning.
3. If unassigned or assigned to someone else, assign the issue to that initiating human. Read the issue back and verify the exact assignee ID. This is the contributor exception in [planning and Wave boundaries](../framework/baseline.md#planning-and-wave-boundaries).
4. At assignment start, **including planning**, set the issue to **In Progress** and read back its state. If already In Progress, verify it. On resume, preserve a verified later delivery state and continue from the recorded gate; do not reset completed work or silently reopen a terminal issue.
5. Name the master `AGENT-X · Master — <short issue title>` using the actual identifier. Record its actual thread identity, issue identity, observed assignment and status in the workspace once available.

**Exit:** the issue, initiating human, master identity and applicable status are verified. Record unavailable integrations or failed writes/read-backs as explicit blockers; never claim the transition succeeded without evidence.

## 2. Establish or resume shared task state

1. Verify the shared state established by the [bootstrap prerequisite](#before-ordinary-tools-bootstrap-readiness). Preserve existing state on resume. Manual data requires supported adoption; cleaned state requires verified recovery before ordinary work.
2. Use the canonical `.task/<ISSUE-ID>/roadmap.md` and `context/`. Linked local worktrees share the same issue workspace through [storage and identity](task-workspace.md#storage-and-identity); different issues remain separate. Preserve existing progress, ownership, approval and unresolved work.
3. Preserve the explicitly assigned master coordinator and bounded source packets. For a changed packet, read it, acknowledge its exact digest and verify readiness. Use payload locators such as `roadmap.md` or `context/<name>.md` for managed notes; absolute paths through worktree issue symlinks are rejected by the external source reader. Refresh stale required sources through the coordinator. Use revision-checked lifecycle updates; reread and retry conflicts.
4. Inspect the selected host’s [setup](host-hooks.md#setup-and-bootstrap), [coverage](host-hooks.md#implemented-coverage) and [validation limits](host-hooks.md#validation-and-remaining-evidence). Determine whether installed hooks actually run on the chosen path. Record observed callbacks or the precise unverified/unsupported boundary.

**Exit:** a usable shared roadmap/context workspace and current readiness are verified. A setup failure blocks dependent work: report the exact error and recovery action. Manual core calls establish lifecycle evidence only; separate missing setup, documentation gaps and runtime defects. Preserve platform and child-identity restrictions.

## 3. Present a concrete proposal and wait

1. Populate the roadmap with the issue objective, criteria, governing references, exact candidate context, existing progress and proposed numbered steps. Each step names **scope, dependencies, outputs, verification and exit criteria**. Identify permitted inputs and ownership boundaries.
2. Present the populated proposal and its revision to the human. Mark it proposed and awaiting approval. A blank template, prior approval of the general workflow or a request to start the issue is not approval of this implementation roadmap.
3. Stop before implementation until the human explicitly approves that proposal. Record the attributable approval and exact approved revision. Silence is not approval.

**Exit:** explicit approval is recorded. On resume, verify existing approval against the current scope; preserve it when still applicable.

## 4. Execute within the approval boundary

Approval authorizes the listed steps **through PR submission**, including routine handoffs and checks. Do not request repeated approval at each handoff. Human PR review and merge remain separate gates.

A material change to scope, acceptance criteria, authority or delivery obligations requires a revised proposal and explicit approval before affected implementation. Record the change and its impact. Continue unaffected approved work where possible.

**Exit:** each dispatched assignment maps to a current approved numbered step.

## 5. Dispatch fresh step threads

1. Start **one fresh thread per approved numbered step**. Give it the bounded objective, governing criteria, allowed source references, owned outputs, dependencies, verification, exit criteria and return destination. Keep private reasoning out of public artifacts.
2. Verify the actual child host/session identity. Record its thread identity and ownership in the master roadmap, then install its own lifecycle scope. A created-chat ID or inherited parent context alone does not prove a runtime child identity or readiness.
3. Run independent steps in parallel when their ownership and inputs permit it. Start dependent steps only after the master verifies prerequisite outputs. Resolve overlapping writes explicitly.

**Exit:** every active step has a verified identity, bounded packet, recorded owner and satisfied dependencies. An unsupported child path blocks dispatch until a supported path is established; do not infer native-hook coverage from a manual attachment.

## 6. Verify handoffs and preserve evidence

1. Each child returns exact changes and revisions, commands/checks and results, unresolved findings and the next action. Include precise artifact and evidence references.
2. The master inspects the exact output against the step’s criteria. Record accepted results, decisions, evidence and unresolved work in the main roadmap and durable permitted context through lifecycle updates. Child completion alone does not satisfy the exit criteria.
3. If temporary external scratch `step-x.md` handoff files were used, verify that all useful information is durably retained and readable, then delete only those scratch files. Keep owned `context/` notes managed through the lifecycle. The current lifecycle has no individual managed-note delete operation, and `reconcile-files` cannot remove files. Retain managed notes until supported issue cleanup; never bypass ownership, manifest or recovery checks with raw deletion.

**Exit:** the master has verified the handoff and retained its evidence. Temporary cleanup is either verified or explicitly unresolved.

## 7. Continue the approved roadmap

After each verified handoff, update progress and dispatch the next eligible step. **Child completion does not end the task.** Continue until all approved steps meet their exit criteria or a blocker requires human input. Record each blocker, its affected work and the next action. Keep other authorized independent work moving.

**Exit:** every approved step is verified complete, or remaining work has an explicit blocker and owner.

## 8. Submit and verify delivery

1. Prepare, submit and read back a **PR for human review into `main`** using [PR preparation and verification](#pr-preparation-and-verification) and the [publication rules](../framework/baseline.md#authoring-and-publication-rules). Retain the exact candidate revision, diff, relevant check results and acceptance evidence in the delivery record. Validate affected links and templates. Report unproven runtime or platform claims.
2. At PR submission, move Linear to **In Review** and verify by read-back. Record the PR and lifecycle `in_review` checkpoint; retain context while review and corrections continue.
3. Read the live rules applicable to the target branch to identify required checks. Preserve current-candidate evidence for failures of currently required checks and the applicable **CI failed** label. Reconcile that label against current requirements and results; record a removed requirement as removed, not passed. An absent check that is no longer required is not a merge blocker. Keep repository-local validation and issue-specific acceptance obligations separate from GitHub merge requirements. Follow those obligations without inventing infrastructure work.
4. Keep merge human-controlled. Verify the actual PR merge into `main`, then move to **Merged** and read back the state. After acceptance and every issue obligation are verified, move to **Done** and verify the completed state. An open PR, passing checks or child completion is insufficient.
5. Record supported [completion evidence](task-workspace-usage.md#recovery-and-completion-semantics) before terminal lifecycle disposition. Preserve unresolved obligations and recovery state. Report unavailable transitions/integrations as blockers rather than fabricating closure.

**Exit:** actual merge, human acceptance, required checks, obligations and provider completion are evidenced. Workspace archival/cleanup retains its separate lifecycle prerequisites.

### PR preparation and verification

Apply these steps when creating a PR and when updating it after review or scope changes.

**Title:** use `<ISSUE-ID>: <issue title>`, with the actual issue identifier, a colon and one space, followed by the issue title. For example: `AGENT-29: Require roadmap approval and separate step threads when starting issues`. Verify the title against the linked issue before publication and during PR read-back.

1. Load [`.github/pull_request_template.md`](../../.github/pull_request_template.md) from the candidate checkout. It is the canonical body structure. Copy it before filling content, including when using an API or CLI that does not insert it automatically.
2. Preserve every heading and change-type option in its exact wording and order. Mark applicable options with `x` in **Type of Change** and leave other options unchecked. Fill every section. Put the issue link, concrete problem, resulting behavior and material limitations in **Description**. Put reproducible commands, tested revision, environment, results and pending validation in **Testing**. Use **Screenshots or Command Outputs (if applicable)** for useful output, or `N/A` with a reason. Keep relevant evidence inside these sections.
3. Keep the **Type of Change** checkboxes; omit the code review checklist. Use concise prose and concrete evidence for validation. Report checks actually run, their results and any pending validation in **Testing**. Use `N/A` with a reason for sections that do not apply. Keep automation-owned additions separate from the authored template and preserve them when editing.
4. Before submission, compare the prepared body against the loaded template: exact heading sequence and change-type options, completed section content, and accurate results, pending validation and N/A explanations. Confirm the title, issue link, branch, target `main` and candidate revision. Use structured body arguments or a UTF-8 body file to preserve Markdown.
5. After creating or editing the PR, fetch it again. Verify the published title and authored body against the prepared values, template structure, base/head branches and exact head commit. Repair mismatches before reporting successful submission. Attach the PR to the working chat and retain the read-back in the delivery record. Repeat affected checks when the candidate or template changes.

**Submission exit:** the published PR matches the required title and exact template, its evidence applies to the named revision, and current required-check status is recorded. Submission does not establish human acceptance or authorize merge.

## Step-thread route

1. Use the supplied bounded assignment and actual host/session identity. Follow the [bootstrap prerequisite](#before-ordinary-tools-bootstrap-readiness) with the master’s assigned packet; report missing identity or setup before ordinary tools.
2. After scoped read, exact-digest acknowledgment and readiness, read the permitted governing references and verify the selected checkout before implementation. Stop affected work and request refresh when required sources become stale.
3. Implement only your approved step within its owned paths. Preserve other work and use the assigned checks. Leave master roadmap ownership and issue delivery transitions with the master unless explicitly assigned.
4. Return the [handoff](#6-verify-handoffs-and-preserve-evidence) and await the master’s verification or correction request. Do not restart issue planning or infer a new approval gate for this already approved assignment.
