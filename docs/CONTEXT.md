---
doc_type: maintenance-contract
---
# Maintain the documentation bundle

## Inputs

- Working: the scoped change request, exact candidate diff and affected files selected through [README.md](README.md).
- Reference: the selected folder's `CONTEXT.md` and [publication rules](framework/baseline.md#authoring-and-publication-rules).

## Process

1. Select the task and read only its named inputs. Trace affected links and section anchors before edits or moves.
2. Edit the canonical rule or guide; update its affected callers, template, example or fixture without duplicating the rule.
3. Check link targets, exact artifact identity and the assigned semantic scope. Record unresolved inputs and implementation limits accurately.

## Outputs

Updated assigned documentation and a reviewable diff; verification evidence in the assigned delivery record. Drafts and evidence do not need a new folder here.

## Human check

At the existing human PR review, verify the intended meaning, affected references and reported limits before human-controlled merge. Folder layout grants no runtime authority or extra approval stage.
