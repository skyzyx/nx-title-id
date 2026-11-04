---
inclusion: manual
---

# KiroGraph: Git Context Workflow

Use this workflow for pre-commit reviews, PR descriptions, and understanding what changed.

## 1. Before committing — Understand what you changed

```text
kirograph_diff_context()              // unstaged — see what's touched
kirograph_diff_context(staged: true)  // staged — final check before commit
```

This shows changed symbols, their callers (who might break), and their callees (what they call).

## 2. Build commit message context

```text
kirograph_commit_context()
```

Returns staged files, diff stat, and affected symbols. Feed into commit message generation.

## 3. Check test coverage for changed symbols

```text
kirograph_test_map()                          // all symbols with no test coverage
kirograph_test_map(symbol: "<changed fn>")    // tests for a specific symbol
```

## 4. PR description

```text
kirograph_pr_context(base: "main", head: "HEAD")
```

Returns symbols added/removed/changed between refs. Use as structured context for PR summary.

## 5. Coverage report (if lcov/Istanbul files exist)

```text
kirograph_test_coverage()                    // worst-covered files first
kirograph_test_coverage(sortBy: "desc")      // best-covered files first
```

## Quick reference

| Intent                 | Tool                       |
|------------------------|----------------------------|
| What did I change?     | `kirograph_diff_context`   |
| Commit message         | `kirograph_commit_context` |
| PR description         | `kirograph_pr_context`     |
| Are my changes tested? | `kirograph_test_map`       |
| Per-file coverage %    | `kirograph_test_coverage`  |
| Semantic changelog     | `kirograph_changelog`      |
