---
inclusion: manual
---

# KiroGraph: Pattern Search Workflow

Use this workflow to find structural code patterns using AST matching.
Activate with `/kirograph-patterns` in Kiro IDE or CLI.

## Steps

### 1. Browse available rules

```text
kirograph_live_search(pattern: "--list")
```

Or use the CLI: `kirograph pattern --list`

### 2. Search for a specific structural pattern

```text
kirograph_live_search(pattern: "eval($X)", language: "typescript")
```

### 3. Run a bundled library rule

```text
kirograph pattern --library sql-injection-concat-js
```

### 4. Add a custom rule

Create a YAML file in your `patternLibraryPath` directory:

```yaml
id: my-custom-rule
language: [javascript, typescript]
severity: high
owaspCategory: A03
description: Custom pattern description
fixHint: How to fix this issue.
rule:
  pattern: dangerousFunction($ARG)
```

## Pattern syntax examples

| Pattern               | Matches                           |
|-----------------------|-----------------------------------|
| `eval($X)`            | Any eval() call                   |
| `$OBJ.query($A + $B)` | String concat in any query method |
| `fs.$F(req.$P, $$$)`  | Any fs method with request param  |
| `createHash('md5')`   | Hardcoded MD5 usage               |

## Interpretation

* Findings mean the pattern was found in the AST — not a false positive from symbol name matching
* Check the surrounding context: `kirograph_node(symbol: "...", includeCode: true)`
* Use `kirograph_callers` to understand how the affected function is reached
