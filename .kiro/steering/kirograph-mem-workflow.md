---
inclusion: manual
---

# KiroGraph: Memory Workflow

Use this workflow to recall past knowledge, store new observations, and keep the memory base
consistent by detecting and resolving conflicts.

## 1. Recall before acting

Before making an architecture decision or fixing a bug, search what's already known:

```text
kirograph_mem_search(query: "<topic or keywords>", kind: "decision")
kirograph_mem_search(query: "<error symptom>", kind: "error")
```

Results include inline conflict annotations (⚡) — review them before proceeding.

## 2. Store a new observation

After a decision, bug fix, or discovery:

```text
kirograph_mem_store(
  content: "<one concise fact>",
  kind: "decision" | "error" | "pattern" | "architecture" | "note",
  topicKey: "<category/slug>",      // optional: stable semantic key for revisitable decisions
  reviewAfter: <epoch-ms>           // optional: schedule re-evaluation after a migration/upgrade
)
```

**topicKey examples:** `"architecture/auth-model"`, `"infra/db-choice"`, `"pattern/error-handling"`

## 3. Capture observations from structured text

If you have a Markdown block with bullet points under headings like `## Key Learnings` or
`## Decisions`, extract them all at once:

```text
kirograph_mem_capture(content: "<markdown text>", kind: "decision")
```

## 4. Detect conflicts

After storing related observations, scan for potential contradictions:

```text
kirograph_mem_conflicts_scan(limit: 20)
```

Returns candidate pairs ranked by similarity. Review each one.

## 5. Compare two observations

To understand if two observations conflict, are compatible, or one supersedes the other:

```text
kirograph_mem_compare(observationA: "<id or topicKey>", observationB: "<id or topicKey>")
```

Returns both observations side by side. Read them, then judge.

## 6. Judge a relation

```text
kirograph_mem_judge(
  relationId: "<id>",
  relation: "supersedes" | "conflicts_with" | "compatible" | "scoped" | "related" | "not_conflict",
  confidence: 0.0–1.0,
  reason: "<why>"
)
```

Use `supersedes` when a newer decision replaces an older one. Use `not_conflict` to dismiss
false positives so they don't reappear in scans.

## 7. Review stale observations

Find observations scheduled for re-evaluation:

```text
kirograph_mem_review(limit: 20)
```

For each: verify it's still accurate. If valid, mark it reviewed:

```text
kirograph_mem_mark_reviewed(id: "<observation-id>")
```

If outdated, store a new observation with the correct information and judge the old one as
superseded via `kirograph_mem_judge`.

## Quick reference

| Situation                     | Action                                             |
|-------------------------------|----------------------------------------------------|
| About to make a decision      | `mem_search` first                                 |
| Made a decision               | `mem_store` with `kind: "decision"` and `topicKey` |
| Fixed a non-obvious bug       | `mem_store` with `kind: "error"`                   |
| Two things seem to contradict | `mem_compare` → `mem_judge`                        |
| Knowledge base getting stale  | `mem_review` → `mem_mark_reviewed`                 |
| Structured notes to extract   | `mem_capture`                                      |
| Regular conflict hygiene      | `mem_conflicts_scan`                               |
