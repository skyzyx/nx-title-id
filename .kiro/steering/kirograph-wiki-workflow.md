---
inclusion: manual
---

# KiroGraph: Wiki Workflow

Use this workflow when you need to consult or update the project wiki.
Activate with `/kirograph-wiki-workflow` in Kiro IDE or read the file directly.

## When to use

* Before a complex task: look up relevant wiki pages
* After a session with durable knowledge: ingest it into the wiki
* After a source file is added: run ingest to capture its content
* Periodically: run lint to catch broken links or contradictions

## Steps

### 1. Look up existing knowledge before starting work

```text
kirograph_wiki_search(query: "<topic or keyword>")
```

Read any relevant pages:

```text
kirograph_wiki_page(slug: "<slug from search results>")
```

### 2. Ingest new knowledge (two-tool flow)

**a. Get the ingest prompt:**

```text
kirograph_wiki_ingest(source: "<text, notes, or paste from docs>", sourceName: "<descriptive name>")
```

The tool returns a structured prompt containing the wiki SCHEMA, the current MANIFEST, and your source text.

**b. Generate the WIKI_DIFF:**

Pass the returned prompt to yourself. Produce a `WIKI_DIFF_START ... WIKI_DIFF_END` block following the schema. Each entry should have a JSON header with `action`, `slug`, `title`, and `section` (optional), followed by Markdown content.

**c. Apply the diff:**

```text
kirograph_wiki_apply_diff(diff: "<the WIKI_DIFF block you generated>")
```

Review the response for any pending conflicts and resolve them.

### 3. List all pages

```text
kirograph_wiki_list()
```

### 4. Health check (periodic)

```text
kirograph_wiki_lint()
```

Issues to look for:

* `broken_link`: a `[[slug]]` reference that points to a non-existent page → fix the slug or create the page
* `orphan`: a page with no Related section and no incoming links → add Related or merge into another page
* `stale_source`: a source with no date metadata → add a date to the source header
* `contradiction`: two pages make semantically opposite claims → resolve via ingest or manual edit

## WIKI_DIFF format reference

```text
WIKI_DIFF_START
{"action": "create", "slug": "auth-flow", "title": "Authentication Flow"}
# Authentication Flow

The login flow validates credentials via JWT...

## Related
- [[user-model]]
WIKI_DIFF_END
```

Supported actions: `create`, `upsert` (merge into existing), `append` (add to specific section).

For append, include `"section": "Known Issues"` in the header.

## Conflict handling

If a diff contradicts an existing page, the tool reports it as a conflict:

* With `wikiAutoResolveConflicts: true`: the newer source wins automatically
* Without: the conflict is listed in the response — read both sides and ingest a resolution

## CLI commands

```bash
kirograph wiki search "<query>"
kirograph wiki page <slug>
kirograph wiki list
kirograph wiki lint
kirograph wiki status
kirograph wiki reindex
```
