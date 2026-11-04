---
inclusion: manual
---

# KiroGraph: Onboarding Workflow

Follow these steps to quickly understand a new codebase.

## Steps

1. **Project overview**

   ```text
   kirograph_status()
   ```

2. **File structure**

   ```text
   kirograph_files(format: "tree", maxDepth: 2)
   ```

3. **Key entry points**

   ```text
   kirograph_hotspots(limit: 15)
   ```

4. **Architecture layers**

   ```text
   kirograph_architecture()
   ```

5. **Explore a specific area**

   ```text
   kirograph_context(task: "<area you want to understand>")
   ```

6. **Understand a key symbol**

   ```text
   kirograph_node(symbol: "<symbol name>", includeCode: true)
   ```

## Tips

* Start broad (status, files, hotspots) then narrow down
* Use `kirograph_type_hierarchy` to understand inheritance patterns
* Use `kirograph_callees` on entry points to trace execution flow
