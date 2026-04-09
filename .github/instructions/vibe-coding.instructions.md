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

## PR Review Process

When the user asks to review and address PR comments:

1. **Fetch comments** — Use `github-pull-request_activePullRequest` to read all unresolved review comments.
2. **Analyze all comments** — Categorize each comment (bug fix, test request, copy fix, question, etc.) and plan the minimal correct fix.
3. **Implement fixes** — Apply all fixes, add tests where requested, run the full test suite.
4. **Commit & push** — Single commit with a descriptive message listing all addressed issues. Push to the PR branch.
5. **Reply to each comment** — Use `gh api` to post replies on each review thread explaining what was done. Use this batch pattern:

```powershell
$replies = @(
  @{ id = <comment_id>; body = "Fixed in <sha>. <explanation>" },
  @{ id = <comment_id>; body = "Fixed in <sha>. <explanation>" }
)
foreach ($r in $replies) {
  $result = gh api "repos/<owner>/<repo>/pulls/<pr>/comments/$($r.id)/replies" -f "body=$($r.body)" 2>&1 | ConvertFrom-Json
  Write-Output "Replied to $($r.id) -> $($result.id)"
}
```

6. **Leave threads unresolved** — The user will review and resolve them manually unless told otherwise.
