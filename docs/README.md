---
doc_type: catalog
---
# Documentation task catalog

Public framework rules, authoring references, examples, fixtures and runtime guides live here. Select one task; follow its folder contract and load the named sections, not the whole tree. For edits, use the [maintenance contract](CONTEXT.md).

| Task | Start here | Scope to select |
| --- | --- | --- |
| Start or resume an issue, or execute an assigned step | [Contributor procedure](runtime/contributor-workflow.md) | Master planning, approval, step threads, handoffs and delivery; assigned children use the bounded step-thread route. |
| Create or update a pull request | [PR preparation and verification](runtime/contributor-workflow.md#pr-preparation-and-verification) | Title, exact repository template, evidence, current required checks and published read-back. |
| Set up a contributor checkout or local commit checks | [Runtime contract](runtime/CONTEXT.md#setup) | Environment setup, editor and check commands. |
| Diagnose pull-request checks or reproduce CI | [PR pre-merge checks](runtime/ci.md) | Five checks, native suites, coverage, evidence and retries; branch enforcement remains a separate human-owned step. |
| Configure or inspect a native host hook | [Runtime contract](runtime/CONTEXT.md#host-protocol) | The selected host protocol and its limitations. |
| Change or use task-workspace lifecycle behavior | [Runtime contract](runtime/CONTEXT.md#lifecycle) | The affected contract and usage sections. |
| Change a shared framework rule or Role placement | [Framework contract](framework/CONTEXT.md) | The named rule, team and affected Role entry. |
| Author a Role or Profile | [Authoring contract](authoring/CONTEXT.md) | Assignment, selected Role entry, applicable rules and one template. |
| Inspect or review a worked definition | [Example contract](examples/CONTEXT.md) | Exact candidate fields and corresponding criteria/rules. |
| Walk through a governance or tooling scenario | [Fixture contract](fixtures/CONTEXT.md) | One fixture and its selected actors/rules. |

The [worked AGO example](examples/agent-governance-officer.md) is documentation, not a runtime resource. Keep it out of a fresh-author test packet. The [baseline](framework/baseline.md#selected-paper-fixtures-and-exclusions) defines paper-scope limits; the [publication rules](framework/baseline.md#authoring-and-publication-rules) retain the existing human PR check.
