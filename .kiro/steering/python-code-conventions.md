---
# @config-manager:start zdp-frontmatter
inclusion: fileMatch
fileMatchPattern: "**/*.py"
# @config-manager:end zdp-frontmatter
---

<!-- @config-manager:start zdp -->
# Python Zero Diagnostics

This project uses [Pyrefly](https://pyrefly.org) as its Python type checker. Pyrefly is a fast type checker and LSP server written in Rust, developed by Meta. Always use Pyrefly — not mypy or pyright — unless Pyrefly is unavailable.

This project targets zero Pyrefly diagnostics across all Python source files. Run the check with:

```bash
pyrefly check {file1} {file2} {file3}
```

## Philosophy

* **Small, surgical edits** are strongly preferred over wide-scale refactoring. Without a larger spec to guide the work, keep changes minimal and focused on the specific diagnostic being resolved.
* **Fix the problem, not the symptom.** Strongly favor resolving the root cause over suppressing the error. Suppression is a last resort reserved for cases where a fix is genuinely impossible or would introduce worse problems.
* The goal is **zero remaining diagnostic issues** when running `pyrefly check {file1} {file2} {file3}` from the project root.

## Verification workflow

1. After editing any `.py` file, run `getDiagnostics` on that file.
2. If diagnostics are reported, fix every one before moving on.
3. After all fixes, run `pyrefly check {file1} {file2} {file3}` from the project root to surface anything remaining.
4. Run `uv run pytest` for the affected package to confirm no regressions.

Do not present work as finished while diagnostics remain.

## Commands

This project uses [Ruff](https://docs.astral.sh/ruff/) as its Python formatter and linter. Ruff replaces YAPF, Black, isort, and flake8 in a single tool. Always use Ruff — do not run YAPF, Black, autopep8, or isort alongside it.

```bash
# Format all Python files in-place
uv run ruff format

# Lint and auto-fix what is fixable
uv run ruff check --fix

# Check formatting without modifying (useful in CI)
uv run ruff format --check

# Check linting without modifying (useful in CI)
uv run ruff check
```

## Rules learned from resolving diagnostics

### Reassigning a parameter does not narrow its union type

Pyrefly (and mypy) do not narrow a parameter's declared type when you reassign it inside the function body. This pattern produces errors on every attribute access after the reassignment:

```python
# Bad — type checker still sees issue as MyModel | dict after the reassignment
def f(issue: MyModel | dict) -> dict:
    if isinstance(issue, dict):
        issue = dacite.from_dict(MyModel, issue)
    return {"id": issue.key}  # error: Item "dict" has no attribute "key"
```

Use a new typed local variable instead:

```python
# Good — typed_value is unambiguously MyModel
def f(issue: MyModel | dict) -> dict:
    typed_value: MyModel = (
        dacite.from_dict(MyModel, issue) if isinstance(issue, dict) else issue
    )

    return {"id": typed_value.key}
```

### `list` is invariant — Use `Sequence` for covariant read-only parameters

`list[SonarIssue]` is not assignable to `list[SonarIssue | dict]` because `list` is invariant. When a function only reads from the sequence, declare the parameter as `Sequence[T]` (from `collections.abc`), which is covariant:

```python
from collections.abc import Sequence

def process(items: Sequence[MyModel | dict]) -> dict: ...
```

### `dataclasses.asdict` preserves `None` fields — Strip them explicitly

`dataclasses.asdict()` includes fields whose value is `None`. When serialising to JSON for an API that omits optional keys, strip `None` values with a recursive helper. Only strip `None`-valued dict keys — do not collapse empty lists or empty dicts, as those may be required fields (e.g. `items: []`):

```python
def _to_dict(obj: object) -> dict:
    def _drop_none(d: object) -> object:
        if isinstance(d, dict):
            return {k: _drop_none(v) for k, v in d.items() if v is not None}

        if isinstance(d, list):
            return [_drop_none(i) for i in d]

        return d

    raw = dataclasses.asdict(obj)  # type: ignore[call-overload]
    result = _drop_none(raw)

    return result if isinstance(result, dict) else {}
```

### Scripts with intra-package imports need a `ModuleNotFoundError` fallback

When a script is run directly (e.g. `uv run {file}`), the package root may not be on `sys.path`, so `from {package} import ...` raises `ModuleNotFoundError`. Add a fallback to the bare module name.

Pyrefly can flag this pattern: it resolves the `try` branch successfully, then type-checks the `except` branch anyway and reports a redefinition on every re-imported name. Also inject the script's own directory into `sys.path` in the `except` block so the bare module name resolves at runtime:

```python
try:
    from {package}.models import MyClass, OtherClass
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).parent))
    from models import (  # pyrefly: ignore[missing-import]
        MyClass,      # pyrefly: ignore[redefinition]
        OtherClass,   # pyrefly: ignore[redefinition]
    )
```

The `sys.path.insert` ensures the bare `from models import ...` resolves at runtime when the package root is not on `sys.path`. Pyrefly uses `# pyrefly: ignore[<error-kind>]` comments to suppress diagnostics by kind. The `# pyrefly: ignore[missing-import]` on the `from models import` line suppresses the unresolvable-module error. Each imported name also needs `# pyrefly: ignore[redefinition]` because Pyrefly sees them as redefinitions of the names already bound in the `try` branch.

## Tooling

Use `ruff` for formatting and linting Python code, and `uv` as the package manager. Run formatting with:

```bash
uv run ruff format
uv run ruff check --fix
```

## Suppression

When a diagnostic cannot be resolved cleanly, suppress it with a Pyrefly ignore comment. Prefer targeting the specific error kind — `# pyrefly: ignore[<error-kind>]` — over a bare `# pyrefly: ignore`, and always include a justification. Suppression is a last resort — STRONGLY prefer fixing the root cause.

**Critical rules:**

* Do NOT fall back to suppression comments except as a last resort. The goal is to resolve the issues, not hide them. Even if there are a large number of call sites, fixing them is the goal.
* For anything deferred (not fixed), present and explain it to the user at the end of the job so the user can follow-up.

## Style conventions

### Docstrings — Opening and closing triple quotes on their own lines

Both the opening `"""` and the closing `"""` must appear on their own lines:

```python
# Bad
def f() -> None:
    """Does something."""
    pass

# Good
def f() -> None:
    """
    Does something.
    """
    pass
```

### Blank line before `return` and `sys.exit` in blocks of three lines or more

If a function or block body is three lines or longer, add a blank line immediately before a `return` statement or a `sys.exit` call:

```python
# Bad
def f(x: int) -> int:
    a = x + 1
    b = a * 2
    return b

# Good
def f(x: int) -> int:
    a = x + 1
    b = a * 2

    return b

# Bad
if error:
    print("something went wrong")
    log(error)
    sys.exit(1)

# Good
if error:
    print("something went wrong")
    log(error)

    sys.exit(1)
```

### Blank line before `if`, `for`, `try`, and `except` statements

Always add a blank line before an `if` condition, `for` statement, `try` block, and `except` block. If a comment immediately precedes the statement, place the blank line before the comment instead:

```python
# Bad
def f(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item:
            result.append(item)
    return result

# Good
def f(items: list[str]) -> list[str]:
    result = []

    for item in items:

        if item:
            result.append(item)

    return result

# Good — blank line before the comment, not between comment and statement
def f(items: list[str]) -> list[str]:
    result = []

    # filter empty strings
    for item in items:
        result.append(item)

    return result
```

### Type annotations — Always specific, avoid `Any`

Add PEP-484 type annotations to every function parameter, return value, and variable where the type can be determined. Prefer specific types over broad ones. Avoid `Any` wherever possible; when it is genuinely necessary, import it explicitly:

```python
from typing import Any

# Bad — missing annotations, uses Any unnecessarily
def process(data):
    result: Any = {}
    return result

# Good
def process(data: dict[str, str]) -> dict[str, int]:
    result: dict[str, int] = {}

    return result
```

Always include key and value types when annotating a `dict`. Never use a bare `dict`:

```python
# Bad
def f(data: dict) -> dict:
    ...

# Good
def f(data: dict[str, Any]) -> dict[str, int]:
    ...
```

Prefer explicit type annotations over relying on type inference.

### Model JSON, YAML, and TOML with dataclasses and dacite

Whenever code accepts input from, produces output to, or converts between JSON, YAML, or TOML documents, model the structure with `@dataclass` classes and use `dacite.from_dict` to deserialise into them. Do not pass raw `dict` objects through the call stack when a typed dataclass can represent the shape instead:

```python
from dataclasses import dataclass
import dacite

@dataclass
class Config:
    host: str
    port: int

# Bad
def load(raw: dict) -> dict:
    return raw

# Good
def load(raw: dict[str, object]) -> Config:
    return dacite.from_dict(Config, raw, config=dacite.Config(strict=False))
```

### Comment line length

Comments, including any whitespace (where tabs count as 4 spaces), must not have individual lines longer than 80 characters. Wrap to the next line instead of continuing on the same line.

Wrong:

```python
"""
validateManagedMarkers checks all profiles at once, used by the validate command to
give a comprehensive report rather than stopping at the first error.
"""
```

Right:

```python
"""
validateManagedMarkers checks all profiles at once, used by the validate command
to give a comprehensive report rather than stopping at the first error.
"""
```

### Exit early on errors

When handling errors or invalid conditions, exit or raise as soon as the problem is detected rather than deferring to the end of the function. This keeps the happy path unindented and easier to follow:

```python
# Bad — late exit
def process(value: str | None) -> str:
    result = ""
    if value is not None:
        cleaned = value.strip()
        result = cleaned.upper()
    else:
        print("Error: value is required", file=sys.stderr)
        sys.exit(1)
    return result

# Good — early exit
def process(value: str | None) -> str:
    if value is None:
        print("Error: value is required", file=sys.stderr)

        sys.exit(1)

    cleaned = value.strip()

    return cleaned.upper()
```

### File section order

Organise the top-level contents of every Python file in this order:

1. Imports
2. Constants
3. Module-level variables
4. Classes (dataclasses first, then regular classes)
5. Private functions (names prefixed with `_`)
6. Public functions

Private helper functions must be defined before the public functions that call them, because Python resolves names at call time top-to-bottom. Placing private functions before public functions satisfies both the ordering convention and Python's execution model.

#### Section separators

When a file has multiple logical sections (constants, classes, functions, etc.), separate them with a section separator comment:

Bad:

```python
#--------------------------------------------------------------------------
# Section name
#--------------------------------------------------------------------------
```

Bad:

```python

    # ------------------------------------------------------------------------------
    # SECTION NAME

```

Good:

```python

# ------------------------------------------------------------------------------
# SECTION NAME

```

Good:

```python

    # --------------------------------------------------------------------------
    # SECTION NAME

```

The line, including the hyphens, should have zero or one spaces after the comment character. And the length of the entire line, including any preceding white space (where a tab is equivalent to 4 spaces), should be a maximum of 80 characters. If the section separator is indented, the length of the hyphens is shorter. The `SECTION NAME` should be all-caps. There should be one empty line before the separator and one empty line after the section name.

Use this pattern consistently for every top-level section in the file.

### Dataclasses in their own file

If the total line count of all dataclass definitions in a module exceeds 50 lines, move them into a dedicated file (e.g. `models.py`). Dataclasses that fit within 50 lines may remain in the same file as the code that uses them.

### Imports are always global

All imports must appear at the top of the file, never inside a function or class body:

```python
# Bad
def main() -> None:
    import argparse
    import os
    ...

# Good — imports at the top of the file
import argparse
import os

def main() -> None:
    ...
```

### Shebang line for executable scripts

* If a file is meant to be **imported**, do not add a shebang.
* If a file is meant to be **executed directly**, add `#!/usr/bin/env -S uv run --script` as the very first line and make the file executable (`chmod +x`).

```python
#!/usr/bin/env -S uv run --script
# scripts/my_script.py — entry point script
```

## Learning

Whenever you learn something new, make sure to update this document with the latest information that you have discovered. This document will be shared and reused across many different repositories, so make sure that you generalize the documentation (that should still be optimized for AI understanding) rather than project-specific details.
<!-- @config-manager:end zdp -->
