# Audit Robustness Fixes Bugfix Design

## Overview

`nx-title-id` has five robustness defects, a multi-title detection gap, and some code quality gaps, listed in `bugfix.md` from the audit in `docs/comprehensive.md`. All of them are in one file, `src/nx_title_id/__init__.py`, and most of them are in `main()`, `partition()`, and the title resolution path (`title_of()`, `meta_title_of()`).

The fix is a set of small, local changes:

* **A.** `main()` catches `OSError` per package and skips that package with `skipping <name>: <reason>`. The `header_key` closure turns an `OSError` from the keys file into a one-line fatal exit.

* **B.** `partition()` rejects a declared entry count above `MAX_ENTRIES = 0x10000` or a string table above `MAX_STRINGS_SIZE = 0x100000` (1 MiB) with `MalformedPackageError`, before reading either.

* **C.** Research shows real DLC never uses index `0x800` (the maximum DLC index is 2000, `0x7D0`). The recommended branch keeps the current `application_of()` mapping, documents the limitation in its docstring, and pins it with tests.

* **D.** The `header_key` closure catches the `ValueError` from `read_header_key()`, prints one warning, and returns `None` from then on. The title resolution treats a `None` key as "no meta NCA fallback", and `main()` skips such packages with a message naming the missing `header_key`.

* **E.** `main()` prints every ID first, then exits with code `3` when there is more than one base game. `README.md` and `CHANGELOG.md` document it.

* **F.** Specific annotations everywhere, no `Any`, and a comment explaining the deferred `Crypto` import. The optional single-open refactor (2.12) is not recommended (see "Fix implementation").

* **G.** Title resolution returns a set of title IDs per package instead of one. `title_of()` becomes `title_ids_of()`, collecting every ticket entry, and `meta_title_of()` returns every qualifying meta NCA title ID instead of only the lowest. `main()` folds each through `application_of()` with `apps.update(...)`. A single package that yields two or more distinct base games then flows into the group E exit-`3` path with no extra branch.

Group G is the one change that touches the resolution path rather than `main()` or `partition()` alone, so it is the largest of the seven. It keeps the resolution order (ticket, then tag, then meta NCA) and the laziness (`prod.keys` and `pycryptodome` are read only when a package has neither a ticket nor a tag), so preservation requirements 3.2, 3.3, 3.4, and 3.13 still hold.

Pyrefly already reports zero errors on the current module (checked with `pyrefly check src/nx_title_id/__init__.py`, preset `basic`), so group F is about specificity, not about fixing existing diagnostics.

## Glossary

* **Bug_Condition (C)**: the union of the input conditions in "Bug details" that trigger one of defects 1.1–1.6.
* **Property (P)**: the correct behavior for an input in C, defined in "Correctness properties".
* **Preservation**: every behavior for inputs outside C, listed in `bugfix.md` 3.1–3.11, which must not change.
* **F / F'**: the module before and after the fix.
* **`partition()`**: parses a PFS0 or HFS0 header in `src/nx_title_id/__init__.py` and returns `[(name, absolute data offset)]`.
* **`header_key` closure**: the function inside `main()` that reads `prod.keys` once, lazily, when a package needs the meta NCA fallback. It is passed to `title_of()`.
* **Meta NCA fallback**: step 3 of the resolution order (ticket, then filename tag, then meta NCA). It needs `header_key` and `pycryptodome`.
* **Application ID / base game ID**: a title ID with low 12 bits `0x000`. An update is `base + 0x800`. A DLC is `base + 0x1000 + index`.
* **AddOnContent (AOC) index**: the DLC number in the low 12 bits of a DLC title ID.
* **`MAX_ENTRIES`, `MAX_STRINGS_SIZE`**: the new sanity limits for a PFS0 or HFS0 header.
* **Multi-title package**: a single package (typically a collection XCI such as `tests/ff.xci`) holding the files of more than one base game, each with its own ticket and meta NCA.
* **`title_ids_of()`**: the renamed `title_of()`, returning every title ID a package declares (`set[int]`) instead of one. A package resolved by a single ticket or tag yields a one-element set; a multi-title cartridge yields several.

## Bug details

### Bug condition

The defects are mostly independent and share little code, so C is a disjunction. Each disjunct names the function where the defect shows up. G is the one exception: its fix changes the shape of the resolution path (a set instead of one ID), which E then reads.

* **A** (`main()`, `is_package()`, `title_of()`, `meta_title_of()`, `read_header_key()`): `open()` or `read()` raises `OSError` and nothing catches it.
* **B** (`partition()`): `f.read(count * entry_size)` and `f.read(strings_size)` run before any check on `count` or `strings_size`.
* **C** (`application_of()`): `low == 0x800` is tested before the DLC branch, so a DLC with index `0x800` is read as an update.
* **D** (`main()`): `except ValueError: sys.exit(str(e))` ends the run on the first package that needs the fallback.
* **E** (`main()`): the multi-game branch only prints a warning and falls through to the implicit exit `0`.
* **G** (`title_of()`, `meta_title_of()`): `title_of()` returns the first ticket match and `meta_title_of()` collapses every meta NCA to the lowest title ID, so a single package holding several base games contributes only one of them to `apps`.

**Formal specification:**

```text
FUNCTION isBugCondition(X)
  INPUT: X = (folder contents, file permissions, keys file, CLI args)
  OUTPUT: boolean

  RETURN
    // A: an unreadable package, or an unreadable keys file that is needed
       EXISTS pkg IN packages(X) : NOT readable(pkg)
    OR (keysFile(X) exists AND NOT readable(keysFile(X))
        AND EXISTS pkg IN packages(X) : needsMetaFallback(pkg))
    // B: a header that declares more than any real package
    OR EXISTS hdr IN pfs0OrHfs0Headers(X) :
         hdr.count > MAX_ENTRIES OR hdr.stringsSize > MAX_STRINGS_SIZE
    // C: a title ID shaped like DLC index 0x800 (see research)
    OR EXISTS tid IN resolvedTitleIds(X) : isDlc(tid) AND (tid & 0xFFF) = 0x800
    // D: keys file without header_key, and a package needs it
    OR (keysFile(X) readable AND NOT hasHeaderKey(keysFile(X))
        AND EXISTS pkg IN packages(X) : needsMetaFallback(pkg))
    // E: more than one base game across the folder
    OR |{ application_of(tid) : tid IN resolvedTitleIds(X) }| > 1
    // G: a single package that holds more than one base game
    OR EXISTS pkg IN packages(X) :
         |{ application_of(tid) : tid IN titleIdsOf(pkg) }| > 1
END FUNCTION
```

`needsMetaFallback(pkg)` is true when the package has no ticket and no `[title ID]` tag. `isDlc(tid)` is true when bit 12 of `tid` is set (`base + 0x1000`); research below shows such an ID with index `0x800` does not exist in practice.

`titleIdsOf(pkg)` is every title ID the package declares: each ticket's rights ID, or (when there is no ticket and no tag and keys are available) each qualifying meta NCA's title ID. For the current code `titleIdsOf(pkg)` is at most a one-element set (first ticket, else tag, else the lowest meta NCA), so the G disjunct is always false today; defect 1.10 is exactly that collapse. With the fix, `titleIdsOf(pkg)` returns the full set, so a multi-title cartridge makes the G disjunct true and feeds the same counting the E disjunct does. Because G widens `resolvedTitleIds(X)` (it is the union of `titleIdsOf(pkg)` over the folder), a true G disjunct implies a true E disjunct; G is the single-package source of the multi-game condition E already handles.

### Examples

* **A, package:** `B.nsp` is mode `000` in a folder with a readable `A.nsp`. F prints a `PermissionError` traceback and nothing on stdout. F' prints `skipping B.nsp: Permission denied` to stderr, `A`'s ID to stdout, and exits `0`.

* **A, keys:** `-k my.keys` with mode `000`, and a package without a ticket or tag. F prints a traceback. F' exits `1` with `cannot read keys file my.keys: Permission denied`.

* **B:** a PFS0 header with `count = 0xFFFFFFFF` on a 10 GiB file. F asks for `0xFFFFFFFF * 0x18` bytes (about 96 GiB, so it reads the whole 10 GiB into memory) before failing as truncated. F' raises `MalformedPackageError` after reading 16 bytes, and `main()` prints `skipping <name>: PFS0 header at 0x0 declares 4294967295 entries (limit 65536)`.

* **C:** `application_of(0x0100000000011800)` returns `0x0100000000011000`, not `0x0100000000010000`. Research shows this ID is not a valid DLC ID, so with the recommended branch the output stays the same and is documented.

* **D:** a keys file with only `titlekek_00 = 00`, `A.nsp` with a ticket, and `B.nsp` with only a meta NCA (in that order). F prints the ticket-resolved `A` ID nowhere, because `B` calls `sys.exit()`. With a different order (`B` first) it never processes `A`. F' warns once, skips `B` with a `header_key` message, prints `A`'s ID, and exits `0`.

* **E:** two packages with tickets for `0x0100000000010000` and `0x0100000000020000`. F prints both IDs and exits `0`. F' prints both IDs, the warning, and exits `3`.

* **G, tickets:** one collection XCI whose secure partition holds tickets for `0x0100000000010000` and `0x0100000000020000`. F reads the first ticket only and prints one ID, exit `0`. F' collects both tickets, maps each through `application_of()`, prints both IDs, the warning, and exits `3`.

* **G, meta NCAs:** `tests/ff.xci`, a real cartridge with 6 titles and at least one update, run with `-k prod.keys` and no tickets. F reads the lowest meta NCA title ID and prints one base game, exit `0`. F' collects every meta NCA title ID, folds them through `application_of()` (so a base game and its update fold together), and prints every distinct base game. Without keys, F' sees no tickets and resolves it to one or zero base games (2.15), the known detection limit.

* **G, single base game (preserved):** a package with a base game, its update, and its DLC (whether by three tickets or three meta NCAs). Every ID folds to the same base game, so `titleIdsOf(pkg)` maps to one application ID, the G disjunct is false, and F' prints one ID and exits `0` exactly as F does (3.12).

## Expected behavior

### Preservation requirements

**Unchanged behaviors:**

* stdout holds only title IDs: 16 uppercase hex digits, one per line, sorted. Every diagnostic goes to stderr (3.1).
* A package resolved by ticket or tag never reads `prod.keys` and never imports `Crypto` (3.2, 3.13). With the group G fix this still holds: `title_ids_of()` reads the meta NCAs only when the ticket set is empty and there is no tag.
* `prod.keys` is read at most once per run (3.3). With the fix, a failed read (missing `header_key`) is also cached, so it is not retried per package.
* Resolution order stays ticket, tag, meta NCA (3.4, 3.13). The group G fix collects all tickets at the ticket step, but still short-circuits before the tag and meta NCA steps when any ticket is present.
* A package with exactly one base game across its titles resolves to exactly one base game ID and, when alone in the folder, exits `0` (3.12).
* `application_of()` returns the same value as today for every input (3.5). With the recommended branch of C this holds for all inputs, including `0x800` DLC-shaped IDs.
* Valid PFS0 and HFS0 headers within the limits parse to the same entries (3.6). Truncated, unterminated, wrong-magic, and no-ID packages keep their stderr messages (3.7).
* Exit `1` for not a folder, a missing `-k` file, and no readable title ID, exit `2` for bad arguments, exit `0` for one game (3.8, 3.9).
* No key value is ever printed (3.10). The new messages print only the keys path, `str(ValueError)` (which names only the path), and `OSError.strerror`.

**Scope:**

All inputs where `isBugCondition` is false must produce the same stdout, stderr, and exit code as today. This includes:

* Folders with one game, whatever mix of base, update, and DLC packages.
* A single package carrying one base game together with its update and its DLC: it still resolves to one base game ID (3.12).
* Packages resolved by ticket, tag, or meta NCA with a valid `header_key`.
* AppleDouble, empty, wrong-extension, and non-package files.
* Malformed packages within the limits (truncated, unterminated name, wrong magic).

## Hypothesized root cause

1. **No `OSError` handling (A).** `main()` catches `MalformedPackageError` and `ValueError` around `title_of()`, but `is_package()` runs outside the `try`, and nothing catches `OSError`. `read_header_key()` runs inside `title_of()` through the closure, so a keys `OSError` would surface as a package failure if `main()` caught `OSError` generically. The fix must turn the keys `OSError` into a fatal exit inside the closure, before it reaches the per-package handler.

2. **Reads before validation (B).** `partition()` checks lengths only after `f.read()`. Python's `read(n)` on a regular file allocates up to `min(n, remaining)` bytes, so the memory use is bounded only by the file size.

3. **Update test before DLC test (C).** `application_of()` dispatches on the low 12 bits alone and ignores bit 12, which distinguishes DLC (`base + 0x1000`) from base and update IDs. This is only a defect if index `0x800` exists (see research).

4. **Keys error treated as fatal (D).** The `except ValueError: sys.exit(...)` in `main()` was a design choice, now reversed by decision D.

5. **Missing exit code (E).** The multi-game branch has no `sys.exit()`.

6. **Code quality (F).** Annotations were left out or set to `Any` where the type was not obvious at the time.

7. **One ID per package (G).** `title_of()` was written for the common case of one game per package: it `return`s on the first ticket match, and `meta_title_of()` keeps `found = min(found, tid)` so a package with several meta NCAs yields only the lowest. Both throw away the other base games in a multi-title cartridge. The fix returns the full set at each step and lets `main()` fold every ID into `apps`, so the existing E counting sees them. The resolution order and laziness are untouched, because the ticket step still short-circuits the meta NCA step.

## Research on DLC index `0x800`

**Question:** can a real DLC have title ID `base + 0x1000 + 0x800`?

**Findings:**

* nxdumptool's author publishes a helper that defines `ADDONCONTENT_MIN_INDEX = 1` and `ADDONCONTENT_MAX_INDEX = 2000`, and computes the valid DLC range as `[aoc_base + 1, aoc_base + 2000]` ([DarkMatterCore, DLC ID to application ID gist](https://gist.github.com/DarkMatterCore/0a7d29e5fc9f1b8a8fa16a3a5f7ca827)). 2000 is `0x7D0`, below `0x800`.

* The same helper maps DLC to its application with `(aoc_id - 0x1000) & 0xFFFFFFFFFFFFF000`, which agrees with `application_of()` for every index from 1 to 2000.

* Switchbrew's title list says an update has the same ID as the application with bitmask `0x800` set ([Switchbrew, Title list/Games](https://switchbrew.org/wiki/Title_list/Games)). For Switch 2 Upgrade Packs, the DLC ID is the AddOnContent base ID plus the index, like other DLC ([Switchbrew, Switch 2: Title list/Games](https://switchbrew.org/wiki/Switch_2:_Title_list/Games)).

* A community issue claims the last three nibbles of a DLC can be `000`–`FFF` ([rommapp/romm issue 2526](https://github.com/rommapp/romm/issues/2526)). This is an informal grouping rule, not a statement of what Nintendo issues, and it conflicts with the 2000 maximum above.

Content was rephrased for compliance with licensing restrictions.

**What is not verified:** the 2000 maximum comes from a tool author's code, not from Nintendo documentation, and no exhaustive list of real DLC IDs was checked. This is the strongest available source.

**Branch 1 (recommended): real DLC never uses `0x800`.** Keep `application_of()` as is. Add one docstring line: DLC indexes run from 1 to 2000 (`0x7D0`), so a low-12-bit value of `0x800` is always an update. Add tests that pin base, update, DLC index 1, DLC index 2000 (`0x7D0`), and the `0x800` DLC-shaped ID, which maps to `base + 0x1000` (documented, not a valid DLC). Zero runtime change, so 3.5 holds for all inputs.

**Branch 2 (not recommended): real DLC can use `0x800`.** Dispatch on bit 12 first:

```python
def application_of(tid: int) -> int:
    if tid & 0x1000:
        return (tid - 0x1000) & ~0xFFF  # DLC: base + 0x1000 + index

    return tid & ~0xFFF  # base game or update (base + 0x800)
```

This relies on application IDs having bit 12 clear. That holds for all three real IDs in `tests/` (`…A000`, `…E000`, `…6000`) and follows from the DLC base being `application + 0x1000` sitting between applications, but it is an extra assumption. It changes the output only for IDs with bit 12 set and low bits `0x800`, plus any malformed ID with bit 12 set and low bits `0x000` (today returned unchanged, then treated as a base game).

**Tradeoff:** branch 2 is shorter, but it changes behavior for inputs that the research says do not exist, and adds an assumption. Branch 1 is no code change and follows "minimum code". The tests in both branches pin the same cases, so switching later is a two-line change.

## Correctness properties

Property 1: Bug Condition - Unreadable packages are skipped

_For any_ folder where a subset of package files cannot be opened or read, the fixed `main()` SHALL print `skipping <name>: <reason>` to stderr for each unreadable package, print the title IDs of the readable packages to stdout exactly as it would if the unreadable files were absent, and never print a traceback.

**Validates: Requirements 2.1.**

Property 2: Bug Condition - Unreadable keys file is a one-line fatal error

_For any_ run where the keys file exists but cannot be read and some package needs the meta NCA fallback, the fixed `main()` SHALL exit non-zero with a single stderr line naming the keys path and the `OSError` reason, with no traceback and no key material.

**Validates: Requirements 2.2.**

Property 3: Bug Condition - Oversized partition headers are rejected before reading

_For any_ PFS0 or HFS0 header whose declared `count > MAX_ENTRIES` or `strings_size > MAX_STRINGS_SIZE`, the fixed `partition()` SHALL raise `MalformedPackageError` with the file position at most 16 bytes past `at` (the header only), and `main()` SHALL skip that package with a stderr message.

**Validates: Requirements 2.3, 2.4.**

Property 4: Bug Condition - `application_of()` mapping is pinned

_For any_ application ID `b` (low 13 bits zero), the fixed `application_of()` SHALL return `b` for `b`, `b + 0x800`, and `b + 0x1000 + i` for every `i` in `1..2000`. For `b + 0x1000 + 0x800` it SHALL return the documented value of the chosen branch (`b + 0x1000` for branch 1, `b` for branch 2).

**Validates: Requirements 2.5.**

Property 5: Bug Condition - Missing `header_key` warns once and continues

_For any_ folder with a readable keys file that has no `header_key`, the fixed `main()` SHALL print exactly one warning naming the keys path, skip every package that needs the meta NCA fallback with a message that mentions `header_key`, and print the IDs resolved by ticket or tag. The exit code SHALL be `0` (one game), `3` (several), or `1` (none resolved).

**Validates: Requirements 2.6, 2.7.**

Property 6: Bug Condition - Several games exit 3 after printing every ID

_For any_ folder whose resolved IDs map to two or more distinct base games, whether they come from several single-game packages or from one multi-title package, the fixed `main()` SHALL print every base game ID to stdout, sorted, print the multi-game warning to stderr, and exit with code `3`.

**Validates: Requirements 2.8, 2.9, 2.14.**

Property 7: Preservation - stdout holds only title IDs

_For any_ input, including a multi-title package, the fixed `main()` SHALL write to stdout only lines matching `^[0-9A-F]{16}$`, sorted and unique, and SHALL never write a key value to stdout or stderr.

**Validates: Requirements 3.1, 3.10, 3.14.**

Property 8: Preservation - Non-buggy inputs behave as before

_For any_ input where `isBugCondition` is false, the fixed code SHALL produce the same stdout, stderr, and exit code as the original code. This includes: `partition()` returns the same entries for every header within the limits, `application_of()` returns the same value for base, update, and DLC indexes 1–2000, `prod.keys` is read at most once and only when a package needs it, a package whose titles all fold to one base game resolves to exactly one base game ID (so one such package alone in the folder exits `0`), and the existing exit codes `0`, `1`, `2` are unchanged.

**Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.11, 3.12, 3.13.**

Property 9: Bug Condition - A package with several base games contributes all of them

_For any_ single package whose title IDs (from every ticket entry and, when there is no ticket and no tag and keys are available, every qualifying meta NCA) fold through `application_of()` to two or more distinct base game IDs, the fixed resolution SHALL contribute every distinct base game ID to the folder result set, so `main()` prints every one to stdout and triggers the group E multi-game path (warning on stderr, exit `3`). When `prod.keys` is unavailable or has no `header_key`, detection SHALL rely on tickets alone, so a multi-title cartridge with no tickets MAY resolve to one or zero base game IDs without this being a defect.

**Validates: Requirements 2.13, 2.14, 2.15, 3.14.**

## Fix implementation

### Changes required

**File:** `src/nx_title_id/__init__.py`. No other source file changes.

**Constants** (after `META`):

```python
# Sanity limits for a PFS0/HFS0 header, far above any real package (an NSP
# holds about a dozen files): they keep a corrupt header from making us read
# up to the whole file into memory.
MAX_ENTRIES = 0x10000
MAX_STRINGS_SIZE = 0x100000  # 1 MiB
```

**Why these values (2.4):**

* A real NSP holds a handful of files: one `.tik`, one `.cert`, one meta NCA, and a few content NCAs. The three real NSPs in `tests/` each have one ticket and one `.cnmt.nca` (pinned by `real_layout`). An XCI secure partition holds the NCAs of the games on the card plus their updates and DLC, which is tens to low hundreds.

* `0x10000` is more than 100 times any real count, and keeps the worst-case entry read at `0x10000 * 0x40` = 4 MiB (HFS0 entries are `0x40` bytes).

* Names are 32 hex digits plus an extension (about 40 bytes), so 1 MiB of names covers more than 25,000 files.

* `0x10000` is just above `0xFFFF`, so the existing tests that build a PFS0 with `0xFFFF` declared entries and expect a `truncated` error (`truncated_entries` in `tests/test_containers.py`, `malformed` in `tests/test_cli.py`) still pass unchanged.

* Tradeoff: tighter limits (for example `0x1000`) would cap memory lower but would change those tests' message from `truncated` to the new limit message. The difference (4 MiB versus 256 KiB worst case) does not matter for a CLI, so the higher value wins on "touch only what you must".

**Function `partition()`** (after unpacking `count, strings_size`, before the reads):

1. If `count > MAX_ENTRIES`, raise `MalformedPackageError(f'{magic.decode()} header at {at:#x} declares {count} entries (limit {MAX_ENTRIES})')`.

2. If `strings_size > MAX_STRINGS_SIZE`, raise `MalformedPackageError(f'{magic.decode()} header at {at:#x} declares a {strings_size}-byte string table (limit {MAX_STRINGS_SIZE})')`.

3. Annotate the return as `list[tuple[str, int]]` and `out` the same way.

**Function `application_of()`:** branch 1 adds one docstring line about the 1–2000 DLC index range and the `0x800` consequence. Annotate `tid: int` and `-> int`. Branch 2 replaces the body with the code shown in "Research".

**Function `title_of()` becomes `title_ids_of()` (group D and group G):** it returns a `set[int]` of every title ID the package declares, instead of one `int | None`. The `header_key` parameter becomes `Callable[[], bytes | None] | None`. The body collects all tickets, then falls through to the tag, then the meta NCAs:

```python
def title_ids_of(path: Path, header_key: Callable[[], bytes | None] | None = None) -> set[int]:
    """
    Every title ID a package declares: its tickets' rights IDs, else a [title
    ID] tag in its name, else (with header_key, a function returning the key
    or None once the fallback is disabled) its meta NCAs. An empty set when
    none of them says. A multi-title cartridge yields more than one.
    """
    with open(path, 'rb') as f:
        names: list[str] = [name for name, _ in package_files(f)]

    # the tickets first: a file name's tag can be wrong. All of them, so a
    # multi-title cartridge contributes every base game (2.13).
    tickets: set[int] = {
        int(name[:16], 16) for name in names if re.fullmatch(r'[0-9A-Fa-f]{32}\.tik', name)
    }

    if tickets:
        return tickets

    m: re.Match[str] | None = re.search(r'\[([0-9A-Fa-f]{16})\]', path.name)

    if m:
        return {int(m.group(1), 16)}

    if not header_key:
        return set()

    key: bytes | None = header_key()

    return meta_title_of(path, key) if key else set()
```

This keeps the lazy read (the meta NCAs are opened only when the ticket set is empty and there is no tag, so 3.2, 3.4, and 3.13 hold) and lets the closure signal "fallback disabled" with `None`. When tickets exist, the meta NCAs are not decrypted, which is the explicit resolution of the 2.13 (`every ticket entry and every qualifying meta NCA`) versus 3.4/3.13 (ticket-first) tension: `every … meta NCA` is read as "of the step that actually runs", and the ticket-first short-circuit wins so that a ticket-resolved package never loads `pycryptodome`. For `tests/ff.xci`, a cartridge that carries no tickets, the meta NCA step runs (with keys) and supplies the multiple IDs; without keys it returns the empty set or (via a tag) one ID (2.15).

**Function `main()`:**

1. **Closure.** Replace the `cache`/`header_key` pair with a cache of `bytes | None`:

    ```python
    cache: list[bytes | None] = []  # [] until read, then [key] or [None]

    def header_key() -> bytes | None:
        """
        prod.keys header_key, read once and only when a package needs the
        meta NCA. None, after one warning, when the file has no header_key.
        """

        if not cache:

            try:
                cache.append(read_header_key(keys))

            except ValueError as e:  # no header_key: warn once, go on
                print(f'warning: {e}; skipping packages that need it', file=sys.stderr)
                cache.append(None)

            except OSError as e:  # fatal, and not mistaken for a bad package
                sys.exit(f'cannot read keys file {keys}: {e.strerror or e}')

        return cache[0]
    ```

   `keys` is narrowed to `Path` inside the closure by the `if keys` guard at the call site; if Pyrefly does not narrow a captured variable, bind `keys_path: Path = keys` before defining the closure. `sys.exit()` raises `SystemExit`, which the per-package `except OSError` does not catch.

2. **Per-package `OSError`.** Move `pkg.stat()`, `is_package(pkg)`, and `title_ids_of(...)` into one `try`, and add `except OSError as e: print(f'skipping {pkg.name}: {e.strerror or e}', file=sys.stderr); continue`. Remove the `except ValueError` branch, since the closure no longer raises it. `e.strerror` gives `Permission denied` without the errno prefix and the full path; `or e` covers an `OSError` with no `strerror`.

3. **Collect every ID (group G).** Replace `tid = title_of(...)` / `apps.add(application_of(tid))` with a set per package and a fold of each ID:

    ```python
    tids: set[int] = title_ids_of(pkg, header_key if keys else None)

    if not tids:
        why = ...  # the three cases below
        print(f'skipping {pkg.name}: {why}', file=sys.stderr)

        continue

    apps.update(application_of(t) for t in tids)
    ```

   The emptiness check replaces `tid is None`. A single package that yields two or more distinct base games now adds all of them to `apps`, so `len(apps) > 1` and the group E exit-`3` path fires with no new branch (2.14). A package whose titles all fold to one base game adds exactly one ID (3.12).

4. **Skip message for a disabled fallback (2.7).** When `tids` is empty, pick the reason from three cases: no keys (current hint), keys without `header_key` (`cache == [None]`: `no ticket, no [title ID] in its name, and {keys} has no header_key`), or keys with a key (current message).

5. **Exit 3 (2.8, 2.14).** After printing the sorted IDs, add `if len(apps) > 1: sys.exit(3)`. The warning stays on stderr before the IDs, as today. stdout is flushed on interpreter exit, so all IDs reach stdout before the process exits. This one check covers both several single-game packages (E) and one multi-title package (G).

**Function `xts_decrypt_sector()` (2.11):** replace the inline comment with a comment above the import:

```python
# An intentional exception to the "imports are always global" convention:
# packages resolved by their ticket or tag never load pycryptodome.
from Crypto.Cipher import AES
```

Annotate `header_key: bytes`.

**Function `meta_title_of()` (group G):** return every qualifying meta NCA's title ID as a `set[int]` instead of the lowest. The `found = min(found, tid)` collapse is exactly defect 1.10, so it is replaced with set accumulation:

```python
def meta_title_of(path: Path, header_key: bytes) -> set[int]:
    """
    The title IDs of every meta NCA in the package, one per base game, update
    and DLC it carries. An empty set when it has none. A single-game package
    yields the base game, its update, and its DLCs, which application_of()
    later folds back to one base game.
    """
    found: set[int] = set()

    with open(path, 'rb') as f:

        for name, at in package_files(f):
            ...  # unchanged: skip non-NCA, decrypt sector 1, check magic and META

            tid: int = struct.unpack_from('<Q', h, 0x10)[0]
            found.add(tid)

    return found
```

The empty-set return for a package with no meta NCA matches the old `None`, so `title_ids_of()`'s `meta_title_of(path, key) if key else set()` keeps the same "nothing found" signal.

**Other annotations (2.10):** `is_package(path: Path)`, `read_header_key(keys_path: Path)`, `package_files(f: BufferedReader) -> list[tuple[str, int]]`, `root: int`. `names` in `title_ids_of()` is `list[str]`. Remove `from typing import Any`, add `from collections.abc import Callable, Sequence`.

**Optional single open (2.12): not recommended.** Opening a file three times costs three small reads (`0x104` bytes, the header, and the NCA sectors), and the OS caches them. A single-open refactor would change the signatures of `is_package()`, `title_ids_of()`, and `meta_title_of()`, which tests call with a `Path`. It adds risk and code for no visible change, against "minimum code". Recommend deferring it.

**File:** `README.md`, section "Exit status" (2.9). Replace the paragraph with a table:

| Code | Meaning                                                                                          |
|------|--------------------------------------------------------------------------------------------------|
| `0`  | One game: its title ID was printed                                                               |
| `1`  | Not a folder, no such keys file, an unreadable keys file, or no package with a readable title ID |
| `2`  | Bad command-line arguments                                                                       |
| `3`  | More than one game: every title ID was printed, with a warning on stderr                         |

Add two sentences: a keys file without `header_key` is a warning, not an error, and packages that need it are skipped; code `3` also covers a single package (such as a collection cartridge) that holds more than one base game.

**File:** `CHANGELOG.md`, under `## Unreleased`, add `### Changed` (exit code `3` for several games, including a single multi-title package; a missing `header_key` is a warning), `### Added` (a package holding several base games now contributes every one of them), and `### Fixed` (unreadable packages are skipped; unreadable keys file is a one-line error; oversized PFS0/HFS0 headers are rejected).

**Out of scope, noted:** an unreadable folder (`args.folder.iterdir()` raising `PermissionError`) still prints a traceback. `bugfix.md` covers package files and the keys file only. Fixing it is one more `try`, and can be added if wanted.

## Testing strategy

### Validation approach

Two phases: first, write tests that surface each defect on the unfixed code (they fail), then apply the fix and check that they pass along with the preservation tests. Tests run with `uv run tryke test`. Tryke has no property-based generator and the project has no `hypothesis` dependency, so the property tests below loop over seeded `random.Random(0)` inputs and `test.cases`. Adding `hypothesis` would add a dependency for a few properties, against "minimum code".

### Exploratory bug condition checking

**Goal:** surface counterexamples on the unfixed code to confirm the root causes above.

**Test plan:** add the tests below to `tests/test_cli.py` and `tests/test_containers.py` and run them before changing `src/`.

**Test cases:**

1. **Unreadable package:** `A.nsp` with a ticket and `B.nsp` with mode `000`. Expect exit `0`, `A`'s ID on stdout, and `skipping B.nsp: Permission denied` on stderr (fails on unfixed code with a traceback). Skip when `os.geteuid() == 0`, since root ignores file modes.

2. **Unreadable keys:** a package with only a meta NCA and `-k` pointing to a mode `000` keys file. Expect exit `1` and `cannot read keys file` without `Traceback` (fails on unfixed code). Same root skip.

3. **Oversized header:** a PFS0 header declaring `MAX_ENTRIES + 1` entries, and one declaring `MAX_STRINGS_SIZE + 1` string bytes. Expect `MalformedPackageError` matching `limit`, and `reader.tell() == 16` after the raise (fails on unfixed code: the message says `truncated` and the position has moved).

4. **Missing `header_key`, other packages still print:** a keys file without `header_key`, `A.nsp` with only a meta NCA and `B.nsp` with a ticket. Expect exit `0`, `B`'s ID, exactly one `has no header_key` warning, and a `skipping A.nsp` line mentioning `header_key` (fails on unfixed code with exit `1`).

5. **Several games, synthetic:** two packages with tickets for `BASE` and `OTHER_BASE`. Expect exit `3`, both IDs, and `more than one game` (fails on unfixed code with exit `0`). This runs without the real NSPs, unlike the existing test.

6. **DLC index `0x800`:** `application_of(BASE + 0x1000 + 0x800)`. With branch 1 this passes on unfixed code by design; it pins the documented behavior.

7. **Multi-title package, synthetic tickets:** one XCI whose secure partition holds two tickets, for `BASE` and `OTHER_BASE`, built with `xci([(ticket_name(BASE), b''), (ticket_name(OTHER_BASE), b'')])`. Expect exit `3`, both IDs sorted on stdout, and `more than one game` on stderr (fails on unfixed code: `title_of()` returns only the first ticket, so exit `0` with one ID). This needs no real files or keys.

**Expected counterexamples:**

* Tracebacks with `PermissionError` from `is_package()` and `read_header_key()`.
* `truncated PFS0 header` instead of the limit message for oversized headers.
* Exit `1` with no stdout for case 4, and exit `0` for case 5.
* One ID and exit `0` for case 7, where the fixed code gives two IDs and exit `3`.

### Fix checking

**Goal:** for every input in C, the fixed code gives the expected behavior.

**Pseudocode:**

```text
FOR ALL X WHERE isBugCondition(X) DO
  result := run_cli_fixed(X)
  ASSERT no "Traceback" IN result.stderr
  ASSERT every stdout line matches ^[0-9A-F]{16}$
  ASSERT result.code = expected_code(X)   // 0, 1 or 3 per Properties 1-6, 9
END FOR
```

### Preservation checking

**Goal:** for every input outside C, the fixed code gives the same result as the original.

**Pseudocode:**

```text
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT run_cli_original(X) = run_cli_fixed(X)
END FOR
```

**Testing approach:** property-style tests with seeded random inputs are recommended because they cover many headers and title IDs for little code, catch boundary values that hand-picked cases miss, and give a stronger guarantee that valid inputs are untouched.

**Test plan:** the existing suite pins the current behavior and must keep passing. Two existing tests change because their behavior is deliberately changed: `more_than_one_game_prints_each_sorted_with_a_warning` expects code `3`, and `keys_without_header_key` keeps exit `1` (its only package needs the key) but now also checks for exactly one warning and no traceback.

**Test cases:**

1. **Valid headers:** existing `partition` and `package_files` tests, plus the property test below.

2. **Resolution order and lazy keys:** existing `ticket_first`, `tag`, `meta_fallback`, and `real_ticket` tests (the `_no_key` callable fails if the keys are read). The ticket-first short-circuit of `title_ids_of()` keeps these passing, since the meta NCAs are never decrypted when a ticket is present.

3. **Exit codes `0`, `1`, `2`:** existing `one_game`, `empty_folder`, `not_a_folder`, `missing_keys_file`, `no_keys` tests. `one_game` (a base, its update, and its DLC across three packages) also pins 3.12 at the folder level.

4. **Skip messages:** existing `not_a_package` and `malformed` tests.

5. **One base game in one package (3.12):** a new test builds a single `pfs0()` with a `BASE` ticket, an `UPDATE` tag file, and a `DLC_1` ticket, and expects exit `0` with just `BASE`, so the per-package set folds to one base game.

### Unit tests

* `partition()`: count at `MAX_ENTRIES` (accepted, then `truncated` if absent), at `MAX_ENTRIES + 1` (limit error), strings at `MAX_STRINGS_SIZE` and `MAX_STRINGS_SIZE + 1`, for both PFS0 and HFS0 entry sizes.

* `application_of()`: base, update, DLC index 1, DLC index 2000 (`0x7D0`), and DLC-shaped `0x800`, as `test.cases`.

* `title_ids_of()`: a callable returning `None` gives an empty set for a package with only a meta NCA, and is not called for a ticket or tag package; a `pfs0()` with two tickets gives a two-element set; a single-ticket package gives a one-element set.

* `meta_title_of()`: a `pfs0()` with two meta NCAs (`BASE` and `OTHER_BASE`, both content type `META`) returns both IDs; a non-`META` NCA is excluded; a package with no meta NCA returns an empty set.

### Property-based tests

* **`application_of()` (Property 4, 8):** for 1,000 seeded random application IDs `b` in `[0x0100000000010000, 0x01FFFFFFFFFFE000]` with the low 13 bits cleared, and random `i` in `1..2000`, assert `application_of(b) == application_of(b + 0x800) == application_of(b + 0x1000 + i) == b`.

* **`partition()` limits (Property 3, 8):** for seeded random `(count, strings_size)` pairs, build a PFS0 or HFS0 header. Above a limit, assert `MalformedPackageError` and `tell() == at + 16`. Within the limits with a real body built by `pfs0()` or `hfs0()`, assert the same entries as the builder's input.

* **stdout holds only IDs (Property 7):** for seeded random folders mixing ticket, tag, meta-NCA, malformed, and non-package files, run the CLI and assert every stdout line matches `^[0-9A-F]{16}$`, lines are sorted and unique, and neither stream contains `HEADER_KEY.hex()` in any case. Keep the count small (about 20 folders), since each run starts a subprocess.

* **Multi-title fold (Property 9):** for seeded random sets of distinct base games `B`, build one `pfs0()` carrying one ticket per base (plus, for some, an `UPDATE` and a `DLC_1` ticket off that base). Call `title_ids_of()` and assert `{application_of(t) for t in result} == B`, and that the CLI on a folder with just that package exits `3` when `|B| > 1` and `0` when `|B| == 1`. This pins both 2.13/2.14 (every base contributes) and 3.12 (updates and DLC fold into their base).

### Integration tests

* End-to-end CLI runs through `run_cli()` for each exploratory case above: unreadable package, unreadable keys, oversized header, missing `header_key` with mixed packages, and several games.

* With the real NSPs (skipped when absent): the existing multi-game test, now expecting exit `3`.

* **Multi-title cartridge, synthetic (no real files):** the exploratory case 7 above, kept as a regression test: one XCI with two tickets, exit `3`, both IDs, one warning, no `Traceback`.

* **Multi-title cartridge, real fixture (`tests/ff.xci`):** add `HAVE_FF_XCI = (TESTS_DIR / 'ff.xci').is_file()` and `HAVE_REAL_KEYS` guards in `tests/builders.py`, and gate the test with `@test.skip_if(not (HAVE_FF_XCI and HAVE_REAL_KEYS), reason=...)`, so it runs only when both the fixture and `~/.switch/prod.keys` are present. `ff.xci` carries 6 titles including at least one update, so the fold yields fewer than 6 base games; assert exit `3`, more than one `^[0-9A-F]{16}$` line, the warning on stderr, and no key value on either stream. `ff.xci` stays gitignored like the other real packages (3.15), so a fresh clone or CI skips this test.

* **Multi-title cartridge without keys (2.15):** run `ff.xci` with no `-k` and no default keys (the subprocess HOME is the test's empty home). Since the cartridge carries no tickets, assert the run resolves it to zero or one base game (exit `1` with the `give -k prod.keys` hint, or exit `0` with one ID) and never a traceback. This documents the known detection limit as a passing test, not a defect.

* Verification commands after the fix: `uv run tryke test`, `pyrefly check src/nx_title_id/__init__.py tests/*.py` (zero errors), `uv run ruff format --check`, and `uv run ruff check`.
