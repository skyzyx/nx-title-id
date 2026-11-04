---
inclusion: manual
---

# KiroGraph: Code Review Workflow

Follow these steps for a structured, risk-aware code review using the knowledge graph.

## Steps

1. **Understand the change scope**

   ```text
   kirograph_context(task: "<describe what changed>")
   ```

2. **See exactly what symbols changed**

   ```text
   kirograph_diff_context(staged: true)
   ```

   Lists changed symbols, their callers (who may break), and callees (what they depend on).

3. **Analyze blast radius**
   For each key symbol that was modified:

   ```text
   kirograph_impact(symbol: "<changed symbol>", depth: 2)
   ```

4. **Verify test coverage for changed symbols**

   ```text
   kirograph_test_map(symbol: "<changed symbol>")
   ```

   Flag any changed symbols with no test files in their caller graph.

5. **Look for surprising coupling**

   ```text
   kirograph_surprising(limit: 10)
   ```

6. **Produce findings** grouped by risk level (high/medium/low) with:
   * What changed and why it matters
   * Test coverage status
   * Suggested improvements
   * Overall merge recommendation
