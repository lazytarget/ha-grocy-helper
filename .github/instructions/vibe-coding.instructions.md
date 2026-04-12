---
applyTo: "**"
---

# Vibe-Coding Workflow

When the user says "vibe-coding", "vibe coding", or references this workflow, follow this pattern:

## Workflow Steps

1. **Develop using TDD pattern** — Write tests first, then implement to make them pass. Use pytest.
2. **Commit on success** — After each successful increment (tests pass), create a git commit. Include a commit description formatted as Markdown. Keep commits small and low-complexity when possible.
3. **Summarize** — After committing, briefly summarize the changes to the user.
4. **Await review** — The user will review the commit and may give feedback for modifications.
5. **Iterate or proceed** — If modifications are requested, apply them. On thumbs up, proceed to next step/phase.

## Commit Conventions

- Commit message: short subject line, blank line, then Markdown-formatted description of changes
- Description should list: what was added/changed, key design decisions, test coverage
- Use conventional-ish prefixes when appropriate: `feat:`, `test:`, `refactor:`, `fix:`

## TDD Specifics

- Write failing tests first, then implement the minimum code to pass
- Run tests after implementation to verify
- Tests use pytest + pytest-asyncio
- Test files live in `tests/` directory
- Shared fixtures and fakes in `tests/conftest.py`

## PR Creation Verification

When creating a PR via `gh pr create`, treat command output as the source of truth.

- Consider PR creation **successful** if stdout contains:
  - `Creating pull request for ...`
  - and a GitHub PR URL (for example `https://github.com/<owner>/<repo>/pull/<id>`)
- If both markers are present, **do not retry** PR creation, even if the terminal reports a non-zero exit code.
- After success, report the PR URL/ID back to the user and stop creating PRs.
- If no URL is printed, then treat it as a real failure and investigate/retry.

## PR Review Process

When the user asks to review and address PR comments:

1. **Fetch comments once** — Use `github-pull-request_activePullRequest` to understand context, then fetch PR review comments with a single `gh api` call (`--paginate`) and derive pending top-level comments locally. Ignore any threads that have been resolved.
2. **Analyze all comments** — Categorize each comment (bug fix, test request, copy fix, question, etc.) and plan the minimal correct fix. Be critical of the comments and only apply those that are valid and actionable.
3. **Implement fixes** — Apply all fixes, add tests where requested, run the full test suite.
4. **Commit & push** — Single commit with a descriptive message listing all addressed issues. Push to the PR branch.
5. **Reply to each pending top-level comment exactly once** — Before posting, filter out comments that already have replies and de-duplicate the reply list by `id`.

### Efficient CLI Pattern (minimize `gh api` calls)

- Use one read call for all review comments on the PR, including replies.
- Compute pending top-level comment IDs from that dataset.
- Post only those IDs.

```powershell
$owner = "<owner>"
$repo = "<repo>"
$pr = <pr_number>

# 1) Single fetch: includes top-level comments and replies.
$all = gh api "repos/$owner/$repo/pulls/$pr/comments" --paginate | ConvertFrom-Json

$topLevel = @($all | Where-Object { -not $_.in_reply_to_id })
$repliedToIds = @(
  $all |
    Where-Object { $_.in_reply_to_id } |
    ForEach-Object { [int64]$_.in_reply_to_id } |
    Select-Object -Unique
)

# Pending = top-level comments that do not yet have a reply.
$pending = @(
  $topLevel |
    Where-Object { $repliedToIds -notcontains [int64]$_.id }
)

Write-Output "Top-level: $($topLevel.Count), pending: $($pending.Count)"
```

### Duplicate-Reply Guardrails

- Never reply to IDs outside `$pending`.
- Build `$replies` from `$pending` and then de-duplicate by `id`.
- Skip any reply with empty body.

```powershell
# Build replies from pending comments only.
$replies = @(
  # @{ id = <pending_comment_id>; body = "Fixed in <sha>. <explanation>" }
)

# Enforce uniqueness by id (prevents accidental duplicate posts).
$replies = $replies | Group-Object id | ForEach-Object { $_.Group[0] }

$posted = @{}
foreach ($r in $replies) {
  if ($posted.ContainsKey([string]$r.id)) { continue }
  if ([string]::IsNullOrWhiteSpace($r.body)) { continue }

  $result = gh api "repos/$owner/$repo/pulls/$pr/comments/$($r.id)/replies" -f "body=$($r.body)" 2>&1 | ConvertFrom-Json
  $posted[[string]$r.id] = $true
  Write-Output "Replied to $($r.id) -> $($result.id)"
}
```

6. **Leave threads unresolved** — The user will review and resolve them manually unless told otherwise.
