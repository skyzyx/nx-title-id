# Implementation Plan

Fixes seven groups (A–G) in `src/nx_title_id/__init__.py`, following the Fix implementation and Testing strategy sections of `design.md`. The design names three deliberate behavior changes, so three existing tests change (the real-NSP multi-game test, `keys_without_header_key`, and the implicit exit-`0` on several games). Tests run with `uv run tryke test`. Tryke has no property-based generator and the project adds no `hypothesis` dependency, so the property tests loop over seeded `random.Random(0)` inputs.

Defect groups A–G are mostly independent, so each gets its failing exploration test(s) first, then its fix. The group G fix changes the shape of the resolution path (`title_of()` → `title_ids_of()`, a `set[int]`), which group E reads, so G is sequenced to land with E.

* [x] 1. Write bug condition exploration tests (BEFORE any fix)
  * **Property 1: Bug Condition** - Audit robustness defects A, B, D, E, G
  * **CRITICAL**: These tests MUST FAIL on the unfixed code - failure confirms the bugs exist
  * **DO NOT attempt to fix the test or the code when it fails**
  * **NOTE**: These tests encode the expected behavior and will validate the fix once they pass
  * **GOAL**: Surface counterexamples that demonstrate each defect, per design "Exploratory bug condition checking"
  * In `tests/test_cli.py`, add:
    * **A, unreadable package**: `A.nsp` with a `ticket_name(BASE)` ticket and `B.nsp` with mode `000`; expect exit `0`, `_ids(BASE)` on stdout, `skipping B.nsp: Permission denied` on stderr, no `Traceback`. Skip when `os.geteuid() == 0` (root ignores modes).
    * **A, unreadable keys**: a package with only a `META` NCA and `-k` a mode `000` keys file; expect exit `1`, stderr contains `cannot read keys file`, no `Traceback`. Same root skip.
    * **D, missing header_key keeps others printing**: a keys file with only `titlekek_00 = 00`, `A.nsp` with only a `META` NCA, `B.nsp` with a `ticket_name(OTHER_BASE)` ticket; expect exit `0`, `_ids(OTHER_BASE)` on stdout, exactly one `has no header_key` warning, a `skipping A.nsp` line mentioning `header_key`, no `Traceback`.
    * **E, several games (synthetic)**: two packages with `ticket_name(BASE)` and `ticket_name(OTHER_BASE)`; expect exit `3`, `_ids(BASE, OTHER_BASE)` on stdout, `more than one game` on stderr. Runs without the real NSPs.
    * **G, multi-title package (synthetic)**: one XCI from `xci([(ticket_name(BASE), b''), (ticket_name(OTHER_BASE), b'')])`; expect exit `3`, both IDs sorted on stdout, `more than one game` on stderr.
  * In `tests/test_containers.py`, add:
    * **B, oversized header**: a PFS0 header declaring `MAX_ENTRIES + 1` entries, and one declaring `MAX_STRINGS_SIZE + 1` string bytes; expect `MalformedPackageError` matching `limit` and `r.tell() == 16` after the raise (use the `reader()` builder).
  * Run all of the above on UNFIXED code
  * **EXPECTED OUTCOME**: Each test FAILS (traceback for A, `truncated`/moved position for B, exit `1`/no stdout for D, exit `0`/one ID for E and G) - this is correct, it proves the bugs exist
  * Document the counterexamples observed in each test
  * Mark complete when the tests are written, run, and their failures documented
  * _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 1.10, 2.1, 2.2, 2.3, 2.6, 2.7, 2.8, 2.13, 2.14_

* [x] 2. Write preservation property tests (BEFORE implementing the fix)
  * **Property 2: Preservation** - Non-buggy inputs behave as before
  * **IMPORTANT**: Follow the observation-first methodology - record behavior on UNFIXED code, then assert it
  * Observe on unfixed code: `one_game` exits `0` with `_ids(BASE)`; `ticket_first`/`tag`/`meta_fallback` resolve as today and never read keys when a ticket or tag is present; `partition()` returns the builder's entries for valid headers; `application_of()` returns `BASE` for base, update, DLC index 1, DLC index `0x7FF`.
  * In `tests/test_title.py`, add a seeded-loop property test for `application_of()` (Property 4, 8): for 1,000 `random.Random(0)` application IDs `b` in `[0x0100000000010000, 0x01FFFFFFFFFFE000]` with low 13 bits cleared and random `i` in `1..2000`, assert `application_of(b) == application_of(b + 0x800) == application_of(b + 0x1000 + i) == b`.
  * In `tests/test_containers.py`, add a seeded-loop property test for `partition()` within limits (Property 3, 8): for seeded `(count, strings_size)` within the limits, build a `pfs0()`/`hfs0()` body and assert the entries equal the builder input.
  * In `tests/test_cli.py`, add a seeded-loop property test for stdout-only-IDs (Property 7): for ~20 seeded folders mixing ticket, tag, meta-NCA, malformed, and non-package files, assert every stdout line matches `^[0-9A-F]{16}$`, lines are sorted and unique, and neither stream contains `HEADER_KEY.hex()`.
  * Confirm the existing suite (`one_game`, `ticket_first`, `tag`, `meta_fallback`, `real_ticket`, `empty_folder`, `not_a_folder`, `missing_keys_file`, `no_keys`, `not_a_package`, `malformed`, `partition`/`package_files`/`is_package` tests) passes on UNFIXED code as the preservation baseline.
  * Run the new property tests on UNFIXED code
  * **EXPECTED OUTCOME**: These tests PASS on the unfixed code (they capture the baseline to preserve)
  * Mark complete when the tests are written, run, and passing on unfixed code
  * _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.12, 3.13, 3.14_

* [x] 3. Fix the audit robustness defects (groups A–G)

  * [x] 3.1 Add sanity limits and the partition() guard (group B)
    * In `src/nx_title_id/__init__.py`, after `META`, add constants `MAX_ENTRIES = 0x10000` and `MAX_STRINGS_SIZE = 0x100000` with the design's "why these values" comment.
    * In `partition()`, after unpacking `count, strings_size` and before the `f.read(...)` calls, raise `MalformedPackageError(f'{magic.decode()} header at {at:#x} declares {count} entries (limit {MAX_ENTRIES})')` when `count > MAX_ENTRIES`, and the matching `strings_size` message when `strings_size > MAX_STRINGS_SIZE`.
    * Annotate the return and `out` as `list[tuple[str, int]]`.
    * Keep `0x10000` just above `0xFFFF` so the existing `truncated_entries`/`malformed` tests (which declare `0xFFFF` entries) still hit the `truncated` path unchanged.
    * _Bug_Condition: isBugCondition(X) B disjunct - hdr.count > MAX_ENTRIES OR hdr.stringsSize > MAX_STRINGS_SIZE_
    * _Expected_Behavior: Property 3 - raise MalformedPackageError with tell() at most 16 past `at`, main() skips the package_
    * _Preservation: 3.6, 3.7 - valid headers parse to the same entries; truncated/unterminated/wrong-magic keep their messages_
    * _Requirements: 2.3, 2.4, 2.10_

  * [x] 3.2 Pin application_of() mapping, branch 1 (group C)
    * In `application_of()`, keep the current mapping (research shows real DLC indexes run `1..2000` = `0x7D0`, so low 12 bits `0x800` is always an update).
    * Add one docstring line stating the 1–2000 DLC index range and that `0x800` is therefore always an update.
    * Annotate `tid: int` and `-> int`.
    * Ensure the `test_title.py` cases pin base, update, DLC index 1, DLC index `0x7D0` (2000), and the `0x800` DLC-shaped ID mapping to `base + 0x1000` (documented, not a valid DLC).
    * _Bug_Condition: isBugCondition(X) C disjunct - isDlc(tid) AND (tid & 0xFFF) = 0x800_
    * _Expected_Behavior: Property 4 - branch 1 returns b for base/update/DLC 1..2000, and b + 0x1000 for the 0x800 DLC-shaped ID_
    * _Preservation: 3.5 - application_of() returns the same value as today for every input_
    * _Requirements: 2.5, 2.10_

  * [x] 3.3 Rework the resolution path to return sets (groups D and G)
    * Rename `title_of()` to `title_ids_of()` returning `set[int]`: collect every `*.tik` rights ID; if any ticket, return that set (short-circuit before tag and meta NCA so a ticket-resolved package never loads `pycryptodome`); else a `[title ID]` tag → one-element set; else, with a `header_key` callable, read the key and return `meta_title_of(path, key) if key else set()`.
    * Change the `header_key` parameter to `Callable[[], bytes | None] | None`.
    * Change `meta_title_of()` to return `set[int]`: replace `found = min(found, tid)` with `found.add(tid)`; empty set when no meta NCA. Annotate `path: Path`, `header_key: bytes`, `-> set[int]`.
    * Update the imports: remove `from typing import Any`, add `from collections.abc import Callable`.
    * _Bug_Condition: isBugCondition(X) G disjunct - a single pkg whose titleIdsOf folds to >= 2 distinct base games; D disjunct - keys without header_key and a package needs it_
    * _Expected_Behavior: Property 9 - every distinct base game ID from tickets (or meta NCAs when no ticket/tag and keys available) reaches the folder set; 2.15 detection-without-keys limit_
    * _Preservation: 3.2, 3.4, 3.13 - ticket-first short-circuit keeps lazy prod.keys/pycryptodome; 3.12 single base game folds to one ID_
    * _Requirements: 2.13, 2.15, 2.10_

  * [x] 3.4 Rework main(): warn-once closure, per-package OSError skip, fold, exit 3 (groups A, D, E, G)
    * Replace the `cache`/`header_key` pair with `cache: list[bytes | None]` and a `header_key() -> bytes | None` closure: `try` `read_header_key(keys)`; `except ValueError as e` print one `warning: {e}; skipping packages that need it` to stderr and cache `None`; `except OSError as e` `sys.exit(f'cannot read keys file {keys}: {e.strerror or e}')`. Bind `keys_path: Path = keys` before the closure if Pyrefly does not narrow the captured `keys`.
    * Wrap `pkg.stat()`, `is_package(pkg)`, and `title_ids_of(...)` in one `try`; add `except OSError as e: print(f'skipping {pkg.name}: {e.strerror or e}', file=sys.stderr); continue`. Remove the old `except ValueError` branch.
    * Replace `tid = title_of(...)` / `apps.add(application_of(tid))` with `tids = title_ids_of(pkg, header_key if keys else None)`; on empty `tids` print the skip message and `continue`; else `apps.update(application_of(t) for t in tids)`.
    * Build the empty-`tids` skip message from three cases: no keys (existing hint), keys without `header_key` (`cache == [None]`: message naming `{keys}` and `header_key`), or keys with a key (existing message).
    * After printing the sorted IDs, add `if len(apps) > 1: sys.exit(3)` - one check covers several single-game packages (E) and one multi-title package (G); keep the `more than one game` warning on stderr before the IDs.
    * Replace `apps: set[Any]` with `apps: set[int]`.
    * _Bug_Condition: isBugCondition(X) A, D, E, G disjuncts_
    * _Expected_Behavior: Properties 1, 2, 5, 6, 9 - skip unreadable packages, fatal one-line unreadable keys, warn-once missing header_key, exit 3 after printing every ID_
    * _Preservation: 3.1, 3.3, 3.8, 3.9, 3.10, 3.12 - stdout only IDs, keys read once (failed read cached), exit codes 0/1/2 unchanged, key values never printed_
    * _Requirements: 2.1, 2.2, 2.6, 2.7, 2.8, 2.14, 2.10_

  * [x] 3.5 Code quality: annotations and the Crypto import comment (group F)
    * Replace every remaining `Any` with a specific type and annotate the remaining parameters/returns: `is_package(path: Path)`, `read_header_key(keys_path: Path)`, `package_files(f: BufferedReader) -> list[tuple[str, int]]`, `root: int`, `names: list[str]`.
    * In `xts_decrypt_sector()`, replace the inline comment with a comment above the import stating the deferred `Crypto` import is an intentional exception to the global-imports convention so ticket/tag-resolved packages never load `pycryptodome`; annotate `header_key: bytes`.
    * Do NOT do the optional single-open refactor (2.12) here - see task 6.
    * _Bug_Condition: isBugCondition(X) F concerns (typing/doc gaps 1.7, 1.8)_
    * _Expected_Behavior: specific annotations everywhere, no Any, documented Crypto exception; pyrefly reports zero errors_
    * _Preservation: no runtime behavior change (3.2, 3.13 - laziness of the Crypto import unchanged)_
    * _Requirements: 2.10, 2.11_

  * [x] 3.6 Update the three deliberately-changed existing tests
    * `tests/test_cli.py` `more_than_one_game_prints_each_sorted_with_a_warning`: expect exit `3` (was `0`), keep the sorted IDs and `more than one game` stderr.
    * `tests/test_cli.py` `keys_without_header_key`: keep exit `1` (its only package needs the key), and also assert exactly one `has no header_key`/warning line and no `Traceback`.
    * `tests/test_title.py`: update `meta_title_of` and `title_of` tests to the set-returning API (`title_ids_of`): `none_found`/`meta_fallback` expect an empty set rather than `None`, `ticket_first`/`tag`/`lowest_meta` wrap expected values in a set, and the `_no_key`/`_key` callables keep the same ticket-first short-circuit guarantee.
    * _Requirements: 3.11_

  * [x] 3.7 Verify the bug condition exploration tests now pass
    * **Property 1: Expected Behavior** - Audit robustness defects A, B, D, E, G
    * **IMPORTANT**: Re-run the SAME tests from task 1 - do NOT write new tests
    * Run `uv run tryke test` for the task-1 tests
    * **EXPECTED OUTCOME**: Tests PASS (confirms each defect is fixed) - no tracebacks, correct exit codes `0`/`1`/`3`, every stdout line `^[0-9A-F]{16}$`
    * _Requirements: 2.1, 2.2, 2.3, 2.6, 2.7, 2.8, 2.13, 2.14_

  * [x] 3.8 Verify the preservation tests still pass
    * **Property 2: Preservation** - Non-buggy inputs behave as before
    * **IMPORTANT**: Re-run the SAME tests from task 2 and the existing suite - do NOT write new tests
    * Run `uv run tryke test`
    * **EXPECTED OUTCOME**: All preservation and existing tests pass (no regressions), except the three tests updated in task 3.6 to the decided behavior
    * _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14_

* [x] 4. Add the remaining unit, property, and integration tests
  * **Property 9: Bug Condition** - A package with several base games contributes all of them
  * In `tests/test_title.py`, add unit tests for the set API: `title_ids_of()` with a `None`/no-key callable gives an empty set for a meta-only package and is not called for a ticket/tag package; a `pfs0()` with two tickets gives a two-element set; a single-ticket package gives a one-element set. `meta_title_of()` with two `META` NCAs (`BASE`, `OTHER_BASE`) returns both; a non-`META` NCA is excluded; no meta NCA returns an empty set.
  * In `tests/test_cli.py`, add a test for one base game in one package (3.12): a single `pfs0()` with a `BASE` ticket, an `UPDATE` tag file, and a `DLC_1` ticket; expect exit `0` with just `_ids(BASE)`.
  * In `tests/test_title.py`, add the multi-title fold property test (Property 9, seeded loop): for seeded sets of distinct base games `B`, build one `pfs0()` with one ticket per base (plus some `UPDATE`/`DLC_1` tickets off a base); assert `{application_of(t) for t in title_ids_of(pkg, None)} == B`, and the CLI on a folder with just that package exits `3` when `|B| > 1` and `0` when `|B| == 1`.
  * In `tests/builders.py`, add `HAVE_FF_XCI = (TESTS_DIR / 'ff.xci').is_file()`. In `tests/test_cli.py`, add the real-fixture integration test gated with `@test.skip_if(not (HAVE_FF_XCI and HAVE_REAL_KEYS), reason=...)`: run `ff.xci` with real keys, assert exit `3`, more than one `^[0-9A-F]{16}$` line, the warning on stderr, no key value on either stream.
  * In `tests/test_cli.py`, add the no-keys detection-limit test (2.15): run `ff.xci` with no `-k` and the empty-HOME fixture; assert exit `1` with the `give -k prod.keys` hint or exit `0` with one ID, and never a `Traceback`. Gate on `HAVE_FF_XCI`.
  * In `tests/test_containers.py`, extend the `partition()` limit cases: count at `MAX_ENTRIES` (accepted/`truncated` if absent), at `MAX_ENTRIES + 1` (limit error), strings at `MAX_STRINGS_SIZE` and `+ 1`, for both PFS0 and HFS0 entry sizes.
  * Run `uv run tryke test`
  * **EXPECTED OUTCOME**: All new tests pass; fixture-gated tests skip cleanly when `ff.xci` or real keys are absent (3.15)
  * _Requirements: 2.13, 2.14, 2.15, 2.3, 2.4, 3.12, 3.15_

* [x] 5. Document the new exit code and changelog (groups E, F)
  * In `README.md` "Exit status", replace the paragraph with the exit-code table (`0` one game, `1` not-a-folder/no-keys/unreadable-keys/no-readable-ID, `2` bad arguments, `3` more than one game) and add the two sentences: a keys file without `header_key` is a warning not an error (packages needing it are skipped), and code `3` also covers a single multi-title cartridge holding more than one base game.
  * In `CHANGELOG.md` under `## Unreleased`, add `### Changed` (exit `3` for several games incl. one multi-title package; missing `header_key` is a warning), `### Added` (a package holding several base games contributes every one), and `### Fixed` (unreadable packages skipped; unreadable keys file a one-line error; oversized PFS0/HFS0 headers rejected).
  * Follow the project Markdown style (`*` markers, aligned tables, fenced code with a language, sentence-case headings, no wrapping).
  * _Requirements: 2.9_

* [~] 6. (Optional, not recommended) Single open per package (2.12)
  * The design recommends deferring this: opening each file three times costs three small cached reads, and a single-open refactor changes the signatures of `is_package()`, `title_ids_of()`, and `meta_title_of()` that tests call with a `Path`, adding risk and code for no visible change.
  * If pursued: thread one open `BufferedReader` through the resolution path with no change to stdout/stderr/exit codes, and keep the existing tests passing.
  * _Requirements: 2.12_

* [x] 7. Checkpoint - final verification
  * Run `uv run tryke test` (all pass; fixture/keys-gated tests skip when absent).
  * Run `pyrefly check src/nx_title_id/__init__.py tests/*.py` (zero errors).
  * Run `uv run ruff format --check` and `uv run ruff check` (clean).
  * Confirm no key value appears on stdout or stderr in any test output, and that `stdout` holds only sorted, unique `^[0-9A-F]{16}$` lines.
  * Ask the user if any question arises.
  * _Requirements: 2.10, 3.1, 3.10, 3.11, 3.14_

## Task dependency graph

```text
1 (exploration tests, fail on unfixed)
2 (preservation tests, pass on unfixed)
      |
      v
3.1 (B: limits + partition guard)
3.2 (C: application_of branch 1)
3.3 (D+G: title_ids_of / meta_title_of -> set)
      |
      v
3.4 (A+D+E+G: main closure, OSError skip, fold, exit 3)  [needs 3.3]
      |
      v
3.5 (F: annotations + Crypto comment)
3.6 (update 3 changed existing tests)  [needs 3.3, 3.4]
      |
      v
3.7 (verify exploration tests pass)  [needs 3.1-3.6]
3.8 (verify preservation tests pass)  [needs 3.1-3.6]
      |
      v
4 (unit + property + integration tests)  [needs 3.3, 3.4]
5 (README + CHANGELOG)  [needs 3.4]
      |
      v
6 (optional single-open refactor)  [independent, not recommended]
      |
      v
7 (checkpoint: tryke, pyrefly, ruff)  [needs all above]
```
