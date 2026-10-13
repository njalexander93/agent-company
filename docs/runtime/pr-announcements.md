# Pull-request announcements in Discord

The [PR Discord Announcement workflow](../../.github/workflows/pr-announce.yml)
posts a message to one Discord channel when a pull request is opened and when it
is merged. The workflow file is canonical; this guide explains how to maintain
and troubleshoot it.

## What posts and when

| Event | Discord message | Workflow run |
| --- | --- | --- |
| Pull request opened, including a draft or a fork PR | `Pull request #<number> opened by <author>`, with a green embed whose title links to the PR and fields for the number and author. | Job runs. |
| Pull request merged | `Pull request #<number> by <author> was merged by <merger>`, with a purple embed that adds a **Merged by** field. | Job runs. |
| Pull request closed without merging | Nothing. | The run appears with its job **skipped**. |
| Edits, pushes, reopening, draft-to-ready and other PR actions | Nothing. | No run; the workflow subscribes only to `opened` and `closed`. |

A draft is announced when it is opened, like any other PR. Marking it ready for
review posts nothing. The embed title is the PR title, shortened with an ellipsis
past Discord's 256-character limit; a blank title shows as `(untitled)`.

## Trigger and security boundary

The repository is public and accepts pull requests from forks. The ordinary
`pull_request` trigger withholds secrets from fork PRs, so the workflow uses
`pull_request_target` and fork PRs are announced too. That trigger gives the run
the webhook secret, so the workflow keeps a narrow boundary:

- GitHub runs the workflow file from the **default branch**, so a PR cannot change
  its own announcer.
- The job checks out nothing and runs no code from the PR.
- `permissions: {}` grants the job token no repository access.
- PR number, title, author, URL and merger reach the script only as environment
  data, never as text substituted into the script.
- The message sets `allowed_mentions` to none, so `@everyone` or a user mention in
  a PR title stays plain text.
- Diagnostics never print the webhook URL and redact it from Discord or network
  error text.

**Changes to the workflow take effect only after they reach `main`.** A PR that
edits `pr-announce.yml` is still announced by the version on `main`. Before merge,
the [workflow tests](../../tests/integration/tooling/test_pr_announce_workflow.py)
run the unmodified script against a local fake Discord server. They check the
trigger, permissions, message content, title handling, secret validation, retry
and redaction; they do not reach Discord.

## Webhook secret

The workflow reads the repository Actions secret `DISCORD_PR_WEBHOOK_URL`. Its
value is the incoming webhook URL of the Discord channel that receives
announcements. The URL contains the webhook token: anyone holding it can post to
the channel. **Never paste it into issues, PRs, code, commit messages or logs.**

To create or rotate the webhook:

1. In Discord, open the channel's **Edit Channel → Integrations → Webhooks**,
   create a webhook (or select the existing one) and copy its URL. The URL must
   start with `https://discord.com/api/webhooks/` or
   `https://discordapp.com/api/webhooks/`.
2. In GitHub, open the repository's **Settings → Secrets and variables → Actions**
   and create or update `DISCORD_PR_WEBHOOK_URL` with that URL.
3. When rotating, delete the old webhook in Discord after updating the secret, so
   the previous token stops working.
4. Re-run a failed announcement job, if needed, to confirm delivery.

## Failures

A failed announcement fails only its own workflow run and reports one `::error::`
annotation. Read it in the run's **Post announcement to Discord** step.

| Error says | Cause | Action |
| --- | --- | --- |
| Secret is missing or empty | `DISCORD_PR_WEBHOOK_URL` is not set. | Create the secret as described above, then re-run the job. |
| Secret is not a Discord webhook URL | The value does not start with a Discord webhook prefix. | Replace the secret with the channel's webhook URL, then re-run. |
| HTTP 401, 403 or 404 | The webhook was deleted or the secret is wrong. | Create a channel webhook in Discord, update the secret, then re-run. |
| HTTP 400 | Discord rejected the message payload. | Read Discord's sanitized `message` and `code` in the error and fix the workflow on `main`. |
| HTTP 429 or 5xx | Discord is rate limiting or unavailable. | Re-run the job later. |
| Could not reach Discord | A network failure prevented delivery. | Re-run the job later. |
| Event metadata is missing, or `ANNOUNCE_KIND` is invalid | The event lacked expected PR fields, or the workflow was edited incorrectly. | Check the workflow's `env` block on `main`. |
| Any other HTTP status | Unexpected Discord response. | Re-run the job and check Discord's status. |

For HTTP 429, HTTP 5xx and network failures, the script retries **once** before
failing. It waits for Discord's `retry_after` value or `Retry-After` header, capped
at 10 seconds, or 2 seconds when neither is given. Other HTTP errors fail without
a retry. The job times out after 5 minutes.

## Relation to pre-merge checks

The announcement run can appear on a PR alongside its checks. It is **not** one of
the five [pre-merge checks](pr-checks.md) and is not a merge requirement; a failed
or skipped announcement does not block review or merge. The merge announcement
runs after the merge has already happened.

## Limits

The workflow tests use a fake Discord server, so they cannot prove delivery to the
real channel. Live behaviour can only be confirmed by real pull requests after a
workflow change reaches `main`. Announcements for fork PRs follow from GitHub's
documented `pull_request_target` behaviour but had not been observed on a real fork
PR when this guide was written.

The workflow announces only the two events above. It does not announce
draft-to-ready transitions, reopened PRs, reviews or new commits.
