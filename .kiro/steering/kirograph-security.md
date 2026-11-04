---
inclusion: manual
---

# KiroGraph: Security Audit Workflow

Follow these steps for a structured security audit using the knowledge graph.
Activate this workflow before a release, after adding dependencies, or when asked to review security posture.

## Steps

### 1. Overview

```text
kirograph_security()
```

Note: total dependencies, vulnerability count, verdict breakdown, stale warning count.

### 2. Triage reachable vulnerabilities

```text
kirograph_vulns(verdict: "affected")
```

Focus only on confirmed reachable CVEs. Sort output by EPSS score (exploitation probability) first, then CVSS severity.

**Act immediately on:** EPSS >= 0.5 (actively exploited). Patch regardless of CVSS.
**Prioritize:** EPSS 0.1–0.5 over low-EPSS high-CVSS entries.
**Low urgency:** EPSS < 0.1 — use CVSS + reachability for triage.

### 3. Deep-dive reachability for critical CVEs

For each high-priority CVE from step 2:

```text
kirograph_reachability(target: "<CVE-ID or package name>")
```

This shows: exact call paths from entry points, affected architectural layers, distinct path count.

* `affected` verdict with known entry points → fix this dependency
* `not_affected` → no reachable path, document and move on
* `under_investigation` → unresolved symbols, treat conservatively

### 4. Check for under-investigation CVEs

```text
kirograph_vulns(verdict: "under_investigation")
```

For each: run `kirograph_reachability` to see what symbols are unresolved. If you can determine
the symbol is not called, you can downgrade to not_affected manually.

### 5. License compliance

```text
kirograph_licenses(policy: true)
```

Review any DENY violations — these must be resolved before shipping.
WARN violations should be documented and approved by the team.

### 5b. Structural vulnerability patterns (AST search)

```text
kirograph_live_search(pattern: "eval($X)", language: "javascript")
kirograph_live_search(pattern: "$OBJ.query($A + $B)", language: "typescript")
```

Use `kirograph pattern --list` to browse bundled SAST rules (SQL injection, path traversal, hardcoded secrets, etc.).
These patterns find code issues missed by dependency scanning.

### 6. Dependency staleness

```text
kirograph_staleness(threshold: 0.5)
```

Score guide: 0.3+ = worth reviewing, 0.7+ = significantly behind.
Cross-reference with step 2 results: stale + vulnerable = highest priority.

### 7. Refresh data if needed

If vulnerability data looks stale (flagged in step 1) or dependencies changed recently:

```text
kirograph_vulns(refresh: true)
```

### 8. Export compliance artifacts

```text
kirograph_sbom()   // Software Bill of Materials
kirograph_vex()    // Vulnerability Exploitability eXchange
```

## Interpretation reference

| Signal                                  | Meaning                                | Action                               |
|-----------------------------------------|----------------------------------------|--------------------------------------|
| `affected` + EPSS >= 0.5                | Actively exploited, reachable          | Patch immediately                    |
| `affected` + CVSS >= 9.0                | Critical, reachable                    | Patch this sprint                    |
| `affected` + CVSS 7.0–8.9               | High, reachable                        | Plan fix within 2 weeks              |
| `not_affected`                          | No reachable path found                | Document, no action needed           |
| `under_investigation`                   | Reachability unclear                   | Manual review required               |
| Stale >= 0.7                            | Very outdated                          | Review for accumulated CVEs          |
| License DENY                            | Policy violation                       | Must resolve before release          |
| Pattern match in security-critical code | Code-level vulnerability pattern found | Review context with `kirograph_node` |
