---
inclusion: manual
---

# KiroGraph: Debug Workflow

Follow these steps to systematically trace and debug issues using the knowledge graph.

## Steps

1. **Find related code**

   ```text
   kirograph_search(query: "<error message or symptom keywords>")
   ```

2. **Get full context**

   ```text
   kirograph_context(task: "<describe the bug>")
   ```

3. **Check what recently changed in related symbols**

   ```text
   kirograph_diff_context()
   ```

   Most bugs trace back to recent changes — this surfaces them immediately.

4. **Trace the call chain**

   ```text
   kirograph_callers(symbol: "<suspected function>")
   kirograph_callees(symbol: "<suspected function>")
   ```

5. **Understand blast radius**

   ```text
   kirograph_impact(symbol: "<root cause symbol>", depth: 3)
   ```

## Tips

* Check both callers and callees to understand the full context
* `kirograph_diff_context` is the fastest way to spot a regression — check it first
* Use `kirograph_path` to trace how two symbols are connected
