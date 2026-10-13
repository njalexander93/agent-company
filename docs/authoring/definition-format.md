# Shared Role and Profile format

The authoritative definition format is the packaged [definition format](../../src/nplet/resources/templates/definition-format.md), which the 2026-10 relocation moved into the `nplet` package beside the Role and Profile templates. This file keeps only the contributor material for this repository: the fresh-author usability check, coverage, fixture, change and evidence maintenance, and the worked example.

## Authoring route

For a fresh-author usability check, supply frozen product guidance and explicitly permitted governing records. Exclude research notes, upstream reasoning, conversations and a prewritten answer. Review the exact output separately.

## Review and maintenance

Items 1–3 (fields, sources and meaning) are in the packaged [review and maintenance](../../src/nplet/resources/templates/definition-format.md#review-and-maintenance) list.

4. **Coverage and fixtures:** Reconcile affected entries in the [single coverage record](../framework/role-coverage.md) and expectations in the [governance-change](../fixtures/governance-change.md) and [tooling-change](../fixtures/tooling-change.md) fixtures. Keep required, conditional and outside-scope participation consistent with the selected fixture dispositions; preserve expansion triggers and assigned downstream work. Linked artifacts do not establish trial completion, runtime success or final acceptance.
5. **Changes:** When a source rule or parent changes, identify all affected Roles/Profiles, policy references, coverage entries and fixture expectations. Retain the changed source identity; update affected artifacts in the same review or explicitly linked work with an owner and readiness effect. Recheck references and semantics; repeat affected authoring/review trials. A material candidate change requires applicable renewed review/decision, not transferred approval.
6. **Evidence:** Record the exact candidate, checks, failures/repairs, unresolved gaps and actual reviewer decisions at a durable evidence locator. Apply the baseline's human-reviewed GitHub PR requirement. Paper expectations remain separate from actual receipts, runtime tests, human acceptance and activation.

The [worked AGO example](../examples/agent-governance-officer.md) illustrates the format. It is a documentation artifact, not a packaged runtime Role. Its exact candidate needs independent review; acceptance does not transfer to changed bytes.
