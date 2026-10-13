---
doc_type: maintenance-contract
---
# Author a Role or Profile

## Inputs

- Working: authorized objective, exact candidate/parent, assigned output path and permitted Company inputs.
- Reference: [authoring route](../../src/nplet/resources/templates/definition-format.md#authoring-route), [field guidance](../../src/nplet/resources/templates/definition-format.md#role-field-guidance), [completeness](../../src/nplet/resources/templates/definition-format.md#completeness-and-gaps), [source contract](../../src/nplet/resources/templates/definition-format.md#source-reference-contract) and [review criteria](../../src/nplet/resources/templates/definition-format.md#review-and-maintenance).
- Select the canonical Role entry in [coverage](../framework/role-coverage.md#canonical-role-inventory) and its owning unit in [Teams](../../src/nplet/resources/organization/team-map.md#teams) or [Company-level staff placement](../../src/nplet/resources/organization/team-map.md#placement-and-authority); use only the selected entries.
- Read the applicable [authority](../../src/nplet/resources/organization/baseline.md#authority-and-role-boundaries), [sources](../../src/nplet/resources/organization/baseline.md#source-authority-and-retrieval), [context](../../src/nplet/resources/organization/baseline.md#context-and-independent-assurance) and [outcome](../../src/nplet/resources/organization/baseline.md#handoffs-and-outcomes) sections. Add [governance ownership](../../src/nplet/resources/organization/baseline.md#governance-and-technical-ownership) for AGO or [assurance ownership](../../src/nplet/resources/organization/baseline.md#assurance-ownership) for assurance scopes.
- Copy the blank [Role template](../../src/nplet/resources/templates/role.md) or [Profile template](../../src/nplet/resources/templates/profile.md), following the [template contract](../../src/nplet/resources/templates/README.md). For a Profile, also read the exact permitted parent and [Profile guidance](../../src/nplet/resources/templates/definition-format.md#profile-template).

## Process

1. Resolve the assignment and required facts, then fill the selected template at the assigned destination.
2. Link canonical rules; preserve every applicable field and explicit gap. Read further sections only when the selected responsibility triggers them.
3. Check exact revisions and assigned semantic scope against the review criteria. A fresh-author trial excludes the worked example and upstream reasoning.

## Outputs

One scoped Role/Profile draft plus field/source checks and unresolved gaps in its assigned evidence record. The packaged templates folder holds the reference format/templates, not every draft.

## Human check

Use the existing PR check in [the maintenance contract](../CONTEXT.md#human-check); separate independent review still follows the public rules.
