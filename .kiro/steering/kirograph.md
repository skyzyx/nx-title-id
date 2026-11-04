---
inclusion: always
---

# KiroGraph

KiroGraph builds a semantic knowledge graph of your codebase. Use its MCP tools instead of grep/glob/file reads whenever `.kirograph/` exists in the project.

## Quick decision guide

| Question                                     | Tool                                      |
|----------------------------------------------|-------------------------------------------|
| Where do I start on this task?               | `kirograph_context`                       |
| What is this symbol / show me its code       | `kirograph_node` with `includeCode: true` |
| Find a symbol by name                        | `kirograph_search`                        |
| Who calls function X?                        | `kirograph_callers`                       |
| What does function X call?                   | `kirograph_callees`                       |
| What breaks if I change X?                   | `kirograph_impact`                        |
| What files are indexed?                      | `kirograph_files`                         |
| Is the index healthy?                        | `kirograph_status`                        |
| How are X and Y connected?                   | `kirograph_path`                          |
| What extends / implements this type?         | `kirograph_type_hierarchy`                |
| Which code is never called?                  | `kirograph_dead_code`                     |
| Are there import cycles?                     | `kirograph_circular_deps`                 |
| What are the most critical symbols?          | `kirograph_hotspots`                      |
| Any unexpected cross-module coupling?        | `kirograph_surprising`                    |
| What changed since the last snapshot?        | `kirograph_diff`                          |
| What packages/layers exist?                  | `kirograph_architecture`                  |
| How coupled is package X?                    | `kirograph_coupling`                      |
| What does package X depend on?               | `kirograph_package`                       |
| Compress text or shell output before sending | `kirograph_compress`                      |
| Check token savings stats                    | `kirograph_gain`                          |
| Are there vulnerable dependencies?           | `kirograph_security`                      |
| Which CVEs affect my project?                | `kirograph_vulns`                         |
| Is this vulnerability reachable?             | `kirograph_reachability`                  |
| What licenses do my dependencies use?        | `kirograph_licenses`                      |
| Are dependencies outdated?                   | `kirograph_staleness`                     |
| Generate SBOM/VEX                            | `kirograph_sbom` / `kirograph_vex`        |
| Add a private CVE                            | `kirograph_vuln_add`                      |
| Find structural code patterns?               | `kirograph_live_search`                   |
| What symbols did I change?                   | `kirograph_diff_context`                  |
| Build commit message context                 | `kirograph_commit_context`                |
| Generate PR description                      | `kirograph_pr_context`                    |
| What tests cover symbol X?                   | `kirograph_test_map`                      |
| Show per-file coverage report                | `kirograph_test_coverage`                 |

---

## Tool reference

### `kirograph_context`: **start here for any code task**

Returns entry points, related symbols, and code snippets for a natural-language task description. Usually enough to orient without any additional tool calls.

```text
kirograph_context(task: "fix the auth token expiry bug")
kirograph_context(task: "add dark mode", maxNodes: 30)
kirograph_context(task: "refactor payment service", includeCode: false)
```

### `kirograph_search`: Find symbols by name

Exact match → FTS → LIKE fallback → vector (last resort). Use instead of grep.

```text
kirograph_search(query: "signIn")
kirograph_search(query: "UserService", kind: "class")
kirograph_search(query: "auth", limit: 20)
```

Supported kinds: `function`, `method`, `class`, `interface`, `type_alias`, `variable`, `route`, `component`

### `kirograph_node`: Inspect a symbol

Returns kind, file, signature, docstring. Add `includeCode: true` to get the full source.

```text
kirograph_node(symbol: "validateToken")
kirograph_node(symbol: "AuthService", includeCode: true)
```

### `kirograph_callers`: Who calls this?

BFS over incoming `calls` edges (depth 1).

```text
kirograph_callers(symbol: "processPayment", limit: 30)
```

### `kirograph_callees`: What does this call?

BFS over outgoing `calls` edges (depth 1).

```text
kirograph_callees(symbol: "handleRequest")
```

### `kirograph_impact`: Blast radius before a change

Traverses all incoming edges up to `depth` hops. Call this before editing a symbol.

```text
kirograph_impact(symbol: "UserRepository", depth: 3)
```

### `kirograph_path`: How are two symbols connected?

BFS shortest path across all edge types.

```text
kirograph_path(from: "LoginController", to: "DatabasePool")
```

### `kirograph_type_hierarchy`: Class/interface inheritance

```text
kirograph_type_hierarchy(symbol: "BaseRepository", direction: "down")  // derived types
kirograph_type_hierarchy(symbol: "PaymentService", direction: "up")    // base types
kirograph_type_hierarchy(symbol: "IUserStore", direction: "both")      // all
```

### `kirograph_dead_code`: Unreferenced symbols

Returns unexported symbols with zero incoming edges. Good first step when cleaning up.

```text
kirograph_dead_code(limit: 50)
```

### `kirograph_circular_deps`: Import cycles

Runs Tarjan's SCC over import edges. No parameters needed.

```text
kirograph_circular_deps()
```


### `kirograph_files`: Indexed file structure

```text
kirograph_files(format: "tree")                          // default
kirograph_files(format: "flat")                          // one path per line
kirograph_files(format: "grouped")                       // by directory
kirograph_files(filterPath: "src/auth", maxDepth: 2)
kirograph_files(pattern: "**/*.test.ts")
```

### `kirograph_status`: Index health

Returns file count, symbol count, edge count, embedding coverage, DB size. Call when something feels off.

### `kirograph_hotspots`: Most-connected symbols

Returns the top-N symbols by total edge degree (in + out, excluding structural `contains` edges). Use to find core abstractions, identify high blast-radius symbols before a refactor, or understand what the codebase revolves around.

```text
kirograph_hotspots(limit: 20)
```

### `kirograph_surprising`: Unexpected cross-module coupling

Finds direct edges between symbols in structurally distant files, scored by path distance × edge-kind weight. Use before a refactor to discover hidden dependencies that will break. High score = more unexpected.

```text
kirograph_surprising(limit: 20)
```

### `kirograph_diff`: What changed since a snapshot?

Compares the current graph against a saved snapshot. Shows added/removed symbols and edges. A snapshot must exist: the user saves one with `kirograph snapshot save <label>` before making changes.

```text
kirograph_diff()                              // vs latest snapshot
kirograph_diff(snapshot: "pre-refactor")     // vs named snapshot
```

---

## Architecture tools _(require `enableArchitecture: true` in config)_

### `kirograph_architecture`: **start here for architectural questions**

Returns the full package graph, detected layers (api/service/data/ui/shared), and their dependency edges.

```text
kirograph_architecture()                    // packages + layers
kirograph_architecture(level: "packages")
kirograph_architecture(level: "layers")
kirograph_architecture(includeFiles: true)  // add file→package assignments
```

### `kirograph_coupling`: Stability metrics per package

Returns Ca (afferent: depended on by), Ce (efferent: depends on), and instability (Ce/(Ca+Ce)).

* High Ca + low instability = load-bearing, safe to depend on, risky to change interface.
* High Ce + high instability = depends on many things, safe to refactor internals.

```text
kirograph_coupling()                        // all packages, sorted by instability
kirograph_coupling(sortBy: "afferent")     // most depended-on first
kirograph_coupling(sortBy: "efferent")     // most outgoing deps first
```

### `kirograph_package`: Drill into one package

Returns metadata, coupling metrics, outgoing deps, incoming dependents, and file list.

```text
kirograph_package(package: "auth")
kirograph_package(package: "src/services", includeFiles: false)
```

---

## Git context tools _(require `enableGitContext: true` in config)_

### `kirograph_diff_context`: Changed symbols in working tree

Returns symbols whose definitions overlap with `git diff` line ranges, plus their callers and callees. Use before a commit to understand what you actually changed.

```text
kirograph_diff_context()              // unstaged changes
kirograph_diff_context(staged: true)  // staged changes only
```

### `kirograph_commit_context`: Staged changes summary

Returns staged files, diff stat, and affected symbols. Ready to paste into a commit message prompt.

```text
kirograph_commit_context()
```

### `kirograph_pr_context`: Semantic diff between two refs

Returns symbols added/removed/changed between two git refs. Use for PR descriptions.

```text
kirograph_pr_context(base: "main", head: "HEAD")
kirograph_pr_context(base: "v1.2.0")
```

### `kirograph_test_map`: Symbol → test file mapping

Shows which test files cover a symbol (by caller graph), and which symbols have no test coverage at all.

```text
kirograph_test_map()                         // all uncovered symbols
kirograph_test_map(symbol: "processPayment") // tests for a specific symbol
```

### `kirograph_test_coverage`: Per-file coverage report

Parses lcov/Istanbul/Cobertura files. Sorted worst-first by default.

```text
kirograph_test_coverage()
kirograph_test_coverage(sortBy: "desc", limit: 20)  // best coverage first
```


---

## Workflows

**Bug fix or feature:**

1. `kirograph_context`: orient, find entry points.
2. `kirograph_node` with `includeCode: true`: read the relevant symbol.
3. `kirograph_callers` / `kirograph_callees`: trace the call flow.
4. `kirograph_impact`: check blast radius before editing.

**Refactor planning:**

1. `kirograph_hotspots`: identify the most-connected symbols; changing these is risky.
2. `kirograph_surprising`: surface hidden coupling that will break.
3. `kirograph_impact` on specific targets: confirm blast radius.
4. `kirograph_diff` after the refactor: verify the structural change matches intent.

**Architectural review:**

1. `kirograph_architecture`: get the package and layer map.
2. `kirograph_coupling`: find the most stable (high Ca) and most volatile (high instability) packages.
3. `kirograph_package`: drill into any package of interest.
4. `kirograph_circular_deps`: check for import cycles.

**Code cleanup:**

1. `kirograph_dead_code`: find unreferenced unexported symbols.
2. `kirograph_circular_deps`: find import cycles to untangle.
3. `kirograph_surprising`: find unexpected coupling to decouple.

**Pre-commit / pre-push review:**

1. `kirograph_diff_context`: understand what symbols changed and who calls them.
2. `kirograph_test_map`: verify changed symbols have test coverage.
3. `kirograph_commit_context`: build a structured commit message.

**PR description:**

1. `kirograph_pr_context(base: "main")`: get semantic diff between branches.
2. Use the output as structured context for the PR summary.


---

## Workflow steering files

KiroGraph installs task-specific steering files in `.kiro/steering/`. They are not always active — load them on demand.

**In Kiro IDE:** type `/kirograph-review`, `/kirograph-security`, etc. to activate a workflow for the current session.

**In Kiro CLI / other agents:** when the user asks for a specific workflow or you recognize the intent, read the file directly:

```text
Read file: .kiro/steering/kirograph-security.md
Read file: .kiro/steering/kirograph-review.md
```

| User intent                                       | File to load                                |
|---------------------------------------------------|---------------------------------------------|
| security audit, check vulnerabilities, CVE review | `.kiro/steering/kirograph-security.md`      |
| code review, review this PR                       | `.kiro/steering/kirograph-review.md`        |
| debug, trace this bug, root cause                 | `.kiro/steering/kirograph-debug.md`         |
| architecture, understand structure, package map   | `.kiro/steering/kirograph-architecture.md`  |
| onboard, understand this codebase                 | `.kiro/steering/kirograph-onboard.md`       |
| refactor, rename, safe refactoring                | `.kiro/steering/kirograph-refactor.md`      |
| git diff review, PR context, commit message       | `.kiro/steering/kirograph-git-context.md`   |
| memory, recall decisions, conflict detection      | `.kiro/steering/kirograph-mem-workflow.md`  |
| wiki, update knowledge base, ingest docs          | `.kiro/steering/kirograph-wiki-workflow.md` |

Each file contains numbered steps, exact tool calls, and an interpretation reference. Follow the steps in order.

---

## If `.kirograph/` does NOT exist

Ask the user: "This project doesn't have KiroGraph initialized. Run `kirograph init -i` to build a code knowledge graph for faster exploration?"

## Communication style: Lite

Respond concisely. Omit filler words (just, really, basically, simply, actually).
Keep full sentences and articles. Remove pleasantries and hedging.
Preserve all code blocks, technical terms, file paths, and URLs unchanged.
Pattern: state the fact, then the next step.
Auto-clarity exceptions: temporarily revert to normal prose for (1) security warnings, (2) confirmations of irreversible actions (delete, overwrite, force-push), and (3) multi-step sequences where fragment order could cause misunderstanding. Resume compressed style immediately after.

## Memory

KiroGraph has persistent memory. Use it to recall past decisions and store new ones.

| Question                                  | Tool                                            |
|-------------------------------------------|-------------------------------------------------|
| What did we decide about X?               | `kirograph_mem_search`                          |
| Store a decision / bug fix / pattern      | `kirograph_mem_store`                           |
| Does this contradict something stored?    | `kirograph_mem_conflicts_scan`                  |
| Two observations conflict — which wins?   | `kirograph_mem_compare` → `kirograph_mem_judge` |
| Extract observations from structured text | `kirograph_mem_capture`                         |
| Which observations need re-evaluation?    | `kirograph_mem_review`                          |
| Mark an observation as still valid        | `kirograph_mem_mark_reviewed`                   |

Memory is searchable via hybrid FTS + vector search. Observations surface automatically in
`kirograph_context` and `kirograph_impact` results when linked to relevant code symbols.

**When to store:** After fixing a bug, making an architecture decision, discovering a pattern,
or learning something future sessions should know. One fact per store call. A hook reminds you
at session end.

**topicKey:** Use a stable semantic key (e.g. `"architecture/auth-model"`) when storing a
decision that may be superseded or revisited. Lets you address the same concept across sessions.

**reviewAfter:** Pass an epoch-ms timestamp when an observation should expire or be re-evaluated
(e.g. after a planned migration, a library upgrade, or a time-boxed experiment).

For the full conflict-detection workflow, load: `.kiro/steering/kirograph-mem-workflow.md`

## Documentation

KiroGraph indexes project documentation by heading structure. Use `kirograph_docs_search`
to find relevant doc sections instead of reading entire files. Use `kirograph_docs_section`
to retrieve the exact section you need by ID.

**Available tools:**

* `kirograph_docs_toc` — table of contents for a file or the whole project
* `kirograph_docs_search` — search sections by query (independent from code search)
* `kirograph_docs_section` — retrieve full content of a section by ID
* `kirograph_docs_outline` — heading hierarchy for a single document
* `kirograph_docs_refs` — find code symbols referenced by a doc section (or vice versa)

**When to use:** Before reading a documentation file directly, check if `kirograph_docs_search`
or `kirograph_docs_outline` can give you the specific section you need. This saves tokens
and gives you structured navigation instead of raw file content.

## Pattern matching

KiroGraph can search for structural code patterns using @ast-grep/napi.

**Available tools (only when enablePatterns: true and @ast-grep/napi installed):**

* `kirograph_live_search` — search for any AST pattern across the codebase at query time

**CLI commands:**

* `kirograph pattern "<pattern>"` — live structural search
* `kirograph pattern --list` — browse bundled SAST rules
* `kirograph pattern --library <id>` — run a specific library rule

**When to use:** When you need to find code patterns that can't be expressed as symbol names or semantic queries — "all eval() calls", "all SQL string concatenation", "all readFile with request parameters".

## General-purpose compression

`kirograph_compress` is an on-demand tool for reducing token usage before content reaches the model.
Call it whenever you receive large input that you need to reason over but not reproduce verbatim.

**Two engines — auto-routed by the `command` parameter:**

| Scenario                                                           | Call                                                  |
|--------------------------------------------------------------------|-------------------------------------------------------|
| Paste of shell output (git log, npm install, test run, docker ps…) | `kirograph_compress(text: "...", command: "git log")` |
| Prose text, RAG chunk, observation, or mixed content               | `kirograph_compress(text: "...")`                     |

* **With `command`:** rtk-style structural filters — pattern-matched to the command family (git, test, lint, docker, etc.), removes noise, deduplicates repeated lines, keeps structure.
* **Without `command`:** caveman grammar — removes filler words, articles, hedging phrases, and (at ultra level) applies standard abbreviations. Preserves code blocks, paths, URLs, and identifiers unchanged.

**Compression levels** (same enum for both engines):

* `lite` / `normal` — light touch: remove noise and filler only
* `full` / `aggressive` — default: also remove articles, hedging, group repeated output
* `ultra` — maximum: abbreviations, causality arrows (→), conjunction compression (+)

**When to use:**

* You received a large file diff, log dump, or search result and only need the structure
* You want to store an observation in memory and the text is verbose
* A tool output is close to or over budget and you need to trim before reasoning

**When NOT to use:**

* Content that must be reproduced exactly (code to be written to disk, user quotes)
* Short content (< 200 tokens) — overhead not worth it
* Already-compressed output (KiroGraph_exec already applies rtk filters automatically)

**Savings are reported inline:** `[42% saved | 1800→1044 | rtk:git:aggressive]`

## Wiki

KiroGraph maintains a structured LLM wiki — a set of Markdown pages that compound knowledge
across sessions. Use it to look up project decisions, architecture facts, and domain knowledge
before starting work. Use it to save knowledge that should survive context resets.

**Available tools:**

* `kirograph_wiki_ingest` — build an ingest prompt for a source text; pass the result to yourself to generate a WIKI_DIFF
* `kirograph_wiki_apply_diff` — apply a WIKI_DIFF to create or update wiki pages
* `kirograph_wiki_search` — full-text search over wiki pages
* `kirograph_wiki_page` — retrieve the full content of a page by slug
* `kirograph_wiki_list` — list all pages with metadata
* `kirograph_wiki_lint` — health check: broken links, orphan pages, contradictions

**When to consult the wiki:**

* Before starting a complex feature or bug fix: `kirograph_wiki_search(query: "<topic>")`
* When the user references a concept you don't recognize from the code graph alone
* After `kirograph_context` returns wiki enrichments (pages above threshold score)

**When to update the wiki:**

* End of a session that produced durable knowledge (architecture decision, API contract, process)
* The ingest hook will remind you at agentStop if `enableWiki: true` is set

**Quick workflow:**

1. `kirograph_wiki_ingest` — get the prompt with SCHEMA + MANIFEST + your source text
2. Generate a `WIKI_DIFF` block (create/upsert/append per page)
3. `kirograph_wiki_apply_diff` — apply it; review any pending conflicts in the response

## Security

KiroGraph scans dependency manifests across 14 ecosystems for known vulnerabilities, performs
call-graph reachability analysis, tracks exploitation probability (EPSS), checks license
compliance, and monitors dependency staleness.

**Available tools:**

* `kirograph_security` — overview: dep count, CVE count, verdict breakdown, stale warnings
* `kirograph_vulns` — list CVEs with severity, EPSS score, reachability verdict, fix suggestion
* `kirograph_reachability` — deep-dive: call paths, entry points, affected layers for one CVE or package
* `kirograph_licenses` — list dependency licenses; flag policy violations (deny/warn by SPDX pattern)
* `kirograph_staleness` — identify outdated dependencies (staleness score 0.0–1.0)
* `kirograph_sbom` — export CycloneDX 1.5 SBOM for compliance/auditing
* `kirograph_vex` — export CycloneDX 1.5 VEX with reachability-derived analysis states
* `kirograph_vuln_add` — manually register a private/internal CVE not in public databases

**Proactive triggers — run `kirograph_security` when:**

* You or the user add/update/remove a dependency
* Before a production deploy or release branch cut
* The user asks about security, compliance, or "is it safe to upgrade X"
* `kirograph_context` surfaces a ⚠ Security warning in its output

**Interpreting verdicts:**

* `affected` — a call path exists from an entry point to the vulnerable code. Act on this.
* `not_affected` — no reachable path found, no unresolved imports. Strong signal: likely safe.
* `under_investigation` — traversal hit unresolved symbols (dynamic dispatch, reflection). Treat with caution.

**Interpreting EPSS scores** (shown by `kirograph_vulns`):

* `>= 0.5` — actively exploited or very likely to be. Patch immediately regardless of CVSS.
* `0.1 – 0.5` — elevated risk. Prioritize over low-EPSS vulns with higher CVSS.
* `< 0.1` — low exploitation probability. Use CVSS + reachability for triage.

**Recommended workflow:**

1. `kirograph_security` — get the big picture before diving in
2. `kirograph_vulns --verdict affected` — focus only on confirmed reachable CVEs
3. For each high-EPSS or high-CVSS result: `kirograph_reachability <cve>` to see exact call paths
4. `kirograph_licenses --policy` — check for license violations before shipping
5. `kirograph_staleness --threshold 0.5` — flag severely outdated dependencies
6. Fix, then `kirograph_vulns --refresh` to re-query OSV and confirm resolution
7. `kirograph_vex` / `kirograph_sbom` for compliance artifacts

**Staleness score guide:** 0.0 = current; 0.3+ = worth reviewing; 0.7+ = significantly behind.
A high staleness score alone is not a security issue, but old dependencies accumulate CVEs over time.
