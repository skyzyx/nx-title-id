---
# @config-manager:start tryke-testing-frontmatter
inclusion: auto
name: tryke-testing
description: How to write, run, and verify Python tests with the Tryke test runner (uv run tryke test). Use when adding or changing tests, fixtures, or test configuration.
# @config-manager:end tryke-testing-frontmatter
---

<!-- @config-manager:start tryke-testing -->
# Testing with Tryke

[Tryke](https://tryke.dev) is a Rust-based Python test runner with a Jest-style API (`@test`, `expect()`, `describe()`). These notes cover the official docs plus behavior observed with Tryke 0.0.30. Notes marked _observed_ were confirmed by running Tryke, not taken from the docs.

## Running tests

* One-shot run: `uv run tryke test`. Use this for verification.
* Never run bare `tryke` or `uv run tryke`. Without `test` it starts **watch mode** and blocks ([installation](https://tryke.dev/guides/installation.html)).
* Exit code is `0` when everything passes. The summary ends with lines like `Tests  56 passed (56)` and `PASS`.
* Add it as a pinned dev dependency: `uv add --dev "tryke==<version>"`. Tryke requires Python 3.12 or newer.
* _Observed:_ the summary line's spacing isn't plain ASCII spaces, so `grep "Tests "` (with a trailing space) matches nothing. Use `grep -E "Tests|✗"` instead. When filtering output, capture it first (`uv run tryke test > out.txt 2>&1; echo $?`) so the exit code isn't lost in a pipe.
* Tryke writes a discovery cache to `<project root>/.tryke/cache` ([configuration](https://tryke.dev/guides/configuration.html)). Make sure `.tryke/` is gitignored.

## Configuration

Configure Tryke in `pyproject.toml` under `[tool.tryke]` ([configuration](https://tryke.dev/guides/configuration.html)).

For a `src/` layout, add `src` to the source roots so absolute imports in tests resolve for Tryke's static import graph:

```toml
[tool.tryke]
  src = [".", "src"]
```

At runtime, tests import the package from the project's `.venv`, which `uv sync` installs as editable. Tryke picks up `.venv` automatically when `python` is unset.

## Writing tests

Put tests in `tests/test_*.py` with an empty `tests/__init__.py`. Shared helpers can live in a non-test module such as `tests/builders.py` and be imported as `from tests.builders import ...` (_observed_ to work).

```python
from tryke import expect, test


@test(name='adds two numbers')
def adds() -> None:
    expect(1 + 1).to_equal(2)
```

### Assertions

Matchers ([writing tests](https://tryke.dev/guides/writing-tests.html)): `to_equal`, `to_be`, `to_be_truthy`, `to_be_falsy`, `to_be_none`, `to_be_instance_of`, `to_be_greater_than` (and `_or_equal`), `to_be_less_than` (and `_or_equal`), `to_contain`, `to_have_length`, `to_match`, `to_raise`. Negate with `.not_`, for example `expect(x).not_.to_contain('Traceback')`.

* Exceptions: pass a callable, `expect(lambda: f()).to_raise(MyError, match='regex')`.
* Assertions are **soft** by default: every `expect` in a test runs and reports even after one fails. Chain `.fatal()` when later assertions depend on an earlier one, for example indexing into a list whose length you just checked.

### Naming tests

How a test gets its display name matters, because the wrong combination gives a blank or unhelpful label:

* `@test(name='...')` sets the label explicitly. Prefer this.
* Without `name=`, Tryke uses the **literal first line** of the docstring (_observed_). If the project puts `"""` on its own line, that line is empty and the test shows up with **no name**. Don't rely on docstrings for names in that style.
* `@test.skip_if(condition, reason=...)` registers the test by itself and **takes no `name=`** ([API reference](https://tryke.dev/reference/api.html)). Stacking it above `@test(name=...)` still runs the test, but the `name=` is dropped and the function name is shown instead (_observed_). For a conditionally skipped single test, give the function a descriptive name, for example `def more_than_one_game_prints_each_with_a_warning(...)`.
* With `@test.cases`, each case label becomes the name (`fn[label]`).

### Grouping

```python
from tryke import describe

with describe('partition'):

    @test(name='returns [] for the wrong magic')
    def wrong_magic() -> None: ...
```

Discovery only recurses into `with describe(...)` and `if __TRYKE_TESTING__:` blocks, not other `if`/`for` bodies ([discovery](https://tryke.dev/concepts/discovery.html)). Never generate tests in a loop.

### Parametrized tests

```python
@test.cases(
    test.case('base game', tid=0x0100000000010000, expected=0x0100000000010000),
    test.case('update', tid=0x0100000000010800, expected=0x0100000000010000),
)
def normalizes(tid: int, expected: int) -> None:
    expect(application_of(tid)).to_equal(expected)
```

Rules ([cases](https://tryke.dev/concepts/cases.html)):

* Discovery is static (AST, no import), so labels **must be string literals**. `@test.cases(*specs)`, `@test.cases(build_cases())`, and variable labels are rejected. You can't build cases from a glob, so list each file or value explicitly.
* Keyword values can be expressions (`n=10**6`).
* Don't combine `@test.cases` with `@test`. You can stack function-level `@test.skip_if` above `@test.cases`, and every case is skipped together.
* Per-case `skip=`, `xfail=`, or `todo=` must be string literals.
* A case keyword must not share a name with a fixture-injected parameter.

### Fixtures

```python
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated
from tryke import Depends, fixture, test

@fixture
def workdir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)  # teardown runs after each test, even on failure

@test(name='writes a file')
def writes(d: Annotated[Path, Depends(workdir)]) -> None: ...
```

* `@fixture` runs per test by default. `@fixture(per='scope')` runs once per module or `describe` block and is **shared by reference**, so treat its value as read-only ([writing tests](https://tryke.dev/guides/writing-tests.html)).
* A fixture applies to tests in its lexical scope (the module, or the `describe` block it's defined in). Define it in each test file that needs it.
* Give the parameter a different name from the fixture function (`d` for `workdir`).
* `per='scope'` fixtures force the tests in that scope onto one worker, which reduces parallelism ([concurrency](https://tryke.dev/concepts/concurrency.html)).

### Skipping and expected failures

* `@test.skip_if(cond, reason='...')` is evaluated at import time, which suits tests that need local assets or credentials.
* `@test.skip('reason')`, `@test.todo('reason')`, and `@test.xfail('reason')` are also available.

## Concurrency and isolation

Tests run in parallel across a pool of Python **worker processes**, and each test is its own work unit by default ([concurrency](https://tryke.dev/concepts/concurrency.html)). As a result:

* **Don't redirect `sys.stdout` or `sys.stderr` in-process.** Workers talk to the Rust runtime over stdin/stdout using JSON-RPC. To test a CLI's output, run it in a subprocess: `subprocess.run([sys.executable, '-m', 'pkg', ...], capture_output=True, text=True, check=False)`.
* **Don't mutate module globals** (monkeypatching) to steer behavior. Other tests on the same worker see the change.
* **Isolate the user's environment.** If code reads `Path.home()` or `~/...` (config files, keys), pass the subprocess `env={'HOME': str(tmp_home), 'PATH': '/usr/bin:/bin'}` so the user's real files never affect the result.
* Use a per-test temporary-directory fixture for files. Never write into the repository.

## Tests that use local-only assets

Large, private, or licensed fixtures (real data files, credentials) are often gitignored, so they're missing in CI and fresh clones:

* Compute availability once in a helper module, for example `HAVE_REAL_FILES: bool = all(p.is_file() for p in ...)`, and gate each test with `@test.skip_if(not HAVE_REAL_FILES, reason='...')`.
* Check that the gate works by running the suite with the assets moved away and `HOME` pointed at an empty temp dir. Every gated test should show as skipped (`»`) with its reason, and the rest should still pass.
* Symlink large assets into a test's temp dir instead of copying them.
* Never print or assert on secret values. Assert on derived results, such as a decrypted ID.

## Verifying test changes

1. Run `uv run tryke test` and confirm the exit code is `0` and the summary count is what you expect.
2. For a regression test, **prove it can fail**: temporarily break the fix (back up the file, edit, run, then restore), confirm the test fails, restore, and re-run. Check that the restore actually happened.
3. Run the project's linters and type checker over `tests/` as well as `src/`. Tryke's `Depends()` is typed, so `Annotated[T, Depends(fixture)]` type-checks.
4. If the package is built for distribution, check that `tests/` and any assets aren't in the sdist or wheel.
<!-- @config-manager:end tryke-testing -->
