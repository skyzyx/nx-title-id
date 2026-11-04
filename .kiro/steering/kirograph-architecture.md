---
inclusion: manual
---

# KiroGraph: Architecture Exploration Workflow

Follow these steps to understand the high-level structure of the codebase.

## Steps

1. **Get project overview**

   ```text
   kirograph_status()
   ```

2. **View architecture**

   ```text
   kirograph_architecture()
   ```

3. **Check coupling health**

   ```text
   kirograph_coupling(sortBy: "instability")
   ```

4. **Find core abstractions**

   ```text
   kirograph_hotspots(limit: 20)
   ```

5. **Detect hidden dependencies**

   ```text
   kirograph_surprising(limit: 15)
   ```

6. **Check for cycles**

   ```text
   kirograph_circular_deps()
   ```

## Interpretation

* High Ca (afferent) = load-bearing, risky to change interface
* High Ce (efferent) = depends on many things, safe to refactor internals
* Surprising edges = hidden coupling that may break during refactoring
