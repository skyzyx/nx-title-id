# Implementation Plan: Build-ID Flag

## Overview

This plan implements the opt-in `-b`/`--build-id` flag in the single module `src/nx_title_id/__init__.py`, with tests in `tests/`. The work is test-driven and incremental: the deterministic synthetic path (exefs `EncryptionType` 1 = None) and its tests come first so the feature is provably testable in CI with no retail assets, then the real-crypto path (key-area-key + AES-CTR), then the gated real-fixture integration tests. Every step ends wired into `main()` with tests passing.

All code follows the house conventions: zero Pyrefly diagnostics (`uvx pyrefly check <files>`), `uv run ruff check` and `uv run ruff format --check` clean, tests run with `uv run tryke test` (never bare `tryke`), specific type annotations on every parameter/return/local, docstrings with both `"""` on their own lines, blank lines before `return`/`sys.exit` and before `if`/`for`/`try`/`except` in blocks of three or more lines, `# ---` all-caps section separators, and all imports global except the one documented lazy `from Crypto.Cipher import AES` inside the crypto section. Property tests use seeded `random.Random(0)` loops (never generated in a discovery-time `for` loop), are realized via `@test.cases` with string-literal labels, run at least 100 iterations, and carry a tag comment of the form `# Feature: build-id, Property N: {text}`.

## Tasks

* [x] 1. Add Build-ID constants and fix the fixture builder
  * [x] 1.1 Add new constants to `src/nx_title_id/__init__.py`
    * In the existing constants section, add `NSO_MAGIC = b'NSO0'`, `MODULE_ID_AT = 0x40`, `MODULE_ID_SIZE = 0x20`, `PROGRAM = 0`, `HEADER_REGION = 0xC00`, `FS_ENTRY_AT = 0x240`, `FS_HEADER_AT = 0x400`, `KEY_AREA_AT = 0x300`, `UPDATE_FLAG = 0x800`, each with a comment explaining why (not what), beside the existing `META = 1`
    * Keep line lengths within the comment (80) and code (120) limits
    * _Design: Architecture → New constants. Requirements: 2.1, 2.2, 3.1_

  * [x] 1.2 Fix `HAVE_REAL_NSPS` and expose each fixture's Build ID in `tests/builders.py`
    * Change the `HAVE_REAL_NSPS` computation and `real_nsp()` from the `'{tid:016X}.nsp'` form to the hyphenated `'<TitleID>-<BuildID>.nsp'` form now on disk (for example `01004B800AF5A000-B98E9D18B061B2F0.nsp`); discover the fixtures by globbing `tests/<TitleID>-*.nsp` so each real Title ID maps to its on-disk filename and encoded Build ID
    * Add a helper (for example `real_nsp_build_id(title_id: int) -> str | None`) returning the uppercase hex after the hyphen for a present fixture, and update `real_nsp(title_id)` to return the hyphenated path; keep `REAL_TITLE_IDS`, `NO_REAL_NSPS`, `HAVE_REAL_KEYS`, and `REAL_KEYS` working for the fixture-gated tests
    * This is the one builder change the design calls out as mandatory: without it the renamed fixtures are invisible and Requirement 6.5/6.6 tests cannot find them
    * _Design: Testing Strategy → Builder additions. Requirements: 6.5, 6.6_

* [x] 2. Add synthetic program-NCA builders to `tests/builders.py`
  * [x] 2.1 Add the NSO blob factory and exefs PFS0 builder
    * Add a function that builds a main NSO blob: a buffer with `NSO0` at `0x0` and the caller-supplied `ModuleId` bytes placed at `0x40` (length 0..32, padded to `MODULE_ID_SIZE` in the buffer but remembering the caller's exact byte length for assertions)
    * Add a helper that wraps that NSO as a file named `main` inside an exefs PFS0 built with the existing `_partition`
    * Reuse the existing `PROGRAM`/`META`/`HEADER_KEY`/`nintendo_xts_encrypt` and `_partition`; do not introduce a new crypto dependency
    * _Design: Testing Strategy → Builder additions (NSO blob factory, exefs PFS0). Requirements: 6.1_

  * [x] 2.2 Add the None-encryption program-NCA builder and package assembler
    * Add a builder that produces a program NCA: a `0xC00` decrypted header declaring `ContentType = PROGRAM` at `0x205`, one PartitionFS `FsEntry` at `FS_ENTRY_AT` and matching `FsHeader` at `FS_HEADER_AT` with `FsType = 1` (PartitionFS) and `EncryptionType = 1` (None) pointing at the exefs placed after the `0xC00` header region, with the header encrypted via `nintendo_xts_encrypt(HEADER_KEY, ..., sector)` for sectors 0..5
    * Add a convenience helper that assembles base/update/DLC packages: a `pfs0` carrying such a program NCA plus the `nca_header` meta/title entries needed for `application_of` folding, and (for updates) an embedded update version readable by `update_version_of`
    * _Design: Testing Strategy → Builder additions (program NCA, convenience helper); Data Models → NCA header region. Requirements: 6.1, 3.1, 3.2_

* [x] 3. Implement NCA header decryption and the synthetic exefs section reader
  * [x] 3.1 Implement `decrypt_nca_header` in `src/nx_title_id/__init__.py`
    * Add `decrypt_nca_header(header_key: bytes, raw: bytes) -> bytes` in the crypto/NCA section after `meta_title_of`: decrypt the `0xC00` region as six `0x200` sectors by calling the existing `xts_decrypt_sector` with sector numbers 0..5 and concatenating the plaintext
    * _Design: Components and Interfaces → NCA header decryption. Requirements: 2.1_

  * [x] 3.2 Write unit tests for `decrypt_nca_header`
    * In `tests/test_title.py`, build a `0xC00` plaintext, encrypt it sector-by-sector with `nintendo_xts_encrypt`, and assert `decrypt_nca_header` round-trips it exactly; include a short/truncated input edge case
    * _Design: Components and Interfaces → NCA header decryption. Requirements: 2.1_

  * [x] 3.3 Implement `exefs_section` with the None path only
    * Add `exefs_section(f: BufferedReader, nca_at: int, header: bytes, key_area_key: Callable[[int], bytes | None] | None) -> bytes | None`: walk the `FsEntry` array at `FS_ENTRY_AT` and `FsHeader` array at `FS_HEADER_AT` to find the first PartitionFS (`FsType == 1`) section, compute the absolute offset `nca_at + HEADER_REGION + StartOffset*0x200` and length `(EndOffset - StartOffset)*0x200`, and for `EncryptionType == 1` (None) return the raw section bytes; return `None` for any other encryption type or when no PartitionFS section is present (the AES-CTR branch is added in task 6)
    * _Design: Components and Interfaces → Section location and decryption; Scope and risk (synthetic path). Requirements: 2.1, 4.3_

  * [x] 3.4 Write unit tests for `exefs_section` (None path)
    * In `tests/test_title.py`, use the task 2 builders to assert `exefs_section` returns the exact exefs bytes for a None-encrypted section and returns `None` when the section is absent or declares an unsupported `EncryptionType`
    * _Design: Components and Interfaces → Section location and decryption. Requirements: 2.1, 4.3_

* [x] 4. Implement `module_id_of` and the ModuleId extraction chain
  * [x] 4.1 Implement `module_id_of`
    * Add `module_id_of(path: Path, header_key: bytes, key_area_key: Callable[[int], bytes | None] | None) -> bytes | None`: iterate `package_files(f)`, for each `.nca`/`.ncz` read the `0xC00` region, `decrypt_nca_header`, verify `NCA3`/`NCA2` magic at `0x200`, select the first program NCA (`ContentType == PROGRAM` at `0x205`), call `exefs_section`, parse the returned PFS0 with the existing `partition` logic over a `reader()`-equivalent `BufferedReader(BytesIO(...))` to find the entry named `main`, verify `NSO_MAGIC` at offset 0, and return bytes `[MODULE_ID_AT : MODULE_ID_AT + MODULE_ID_SIZE]`; return `None` for no program NCA, no exefs, missing `main`, wrong magic, or a short read
    * Let genuinely corrupt headers raise `MalformedPackageError` (existing handler); return `None` only for the expected "cannot reach main NSO" cases
    * _Design: Components and Interfaces → Program-NCA location and Main NSO read; Error Handling. Requirements: 2.1, 2.2, 4.3_

  * [x] 4.2 Write property test for ModuleId round-trip
    * In `tests/test_title.py`, seeded `random.Random(0)`, at least 100 iterations: generate random `ModuleId` byte sequences of length 0..32, build a synthetic program-NCA package embedding each, extract via `module_id_of`, assert byte-equality (same bytes, same length)
    * `# Feature: build-id, Property 1: ModuleId round-trip`
    * **Validates: Requirements 2.1, 6.2**

  * [x] 4.3 Write property test for hex rendering
    * In `tests/test_title.py`, seeded `random.Random(0)`, at least 100 iterations: generate random byte sequences of length 0..32, render as uppercase hex, assert the string is uppercase, `len == 2 * len(bytes)`, and `bytes.fromhex(rendered.lower())` equals the original
    * `# Feature: build-id, Property 2: Build-ID hex rendering preserves the exact bytes`
    * **Validates: Requirements 2.2**

* [x] 5. Implement update-version reading and the per-base-game resolver
  * [x] 5.1 Implement `update_version_of`
    * Add `update_version_of(path: Path, header_key: bytes) -> int | None`: read the update version from the package's meta NCA (CNMT), returning `None` when no readable version is available (the resolver treats `None` as merely newer than the base)
    * _Design: Components and Interfaces → Newest-update selection; function signatures. Requirements: 3.2_

  * [x] 5.2 Implement `build_id_of`
    * Add `build_id_of(packages: Sequence[tuple[Path, set[int]]], base: int, header_key: bytes, key_area_key: Callable[[int], bytes | None] | None) -> bytes | None`: classify each scanned package's title IDs by role via the existing low-12-bits logic (base, update = `base + UPDATE_FLAG`, DLC ignored); among update packages pick the greatest readable `update_version_of` (any update beats the base when versions are unreadable, chosen deterministically by sorted path), else the base package; call `module_id_of` on the chosen package and return its bytes or `None`
    * Import `Sequence` from `collections.abc` (covariant read-only parameter, per house convention)
    * _Design: Components and Interfaces → Newest-update selection; In-memory types. Requirements: 3.1, 3.2, 3.3, 3.4, 4.2_

  * [x] 5.3 Write property test for newest-update selection
    * In `tests/test_title.py`, seeded `random.Random(0)`, at least 100 iterations: generate a base plus a random set of update packages with distinct versions and distinct embedded `ModuleId`s (including runs with unreadable versions); assert the resolved Build ID is the max-version update's, that any update beats the base, and that a base-only input resolves to the base's `ModuleId`
    * `# Feature: build-id, Property 3: Newest-update selection is monotonic in version`
    * **Validates: Requirements 3.1, 3.2, 3.3**

  * [x] 5.4 Write property test for DLC exclusion
    * In `tests/test_title.py`, seeded `random.Random(0)`, at least 100 iterations: generate a base (and optionally an update) plus random DLC packages with distinct embedded `ModuleId`s; assert the resolved Build ID is never any DLC's `ModuleId`
    * `# Feature: build-id, Property 4: A DLC is never the Build-ID source`
    * **Validates: Requirements 3.4**

* [x] 6. Implement the real-crypto exefs path (AES-CTR + key area)
  * [x] 6.1 Add the AES-CTR branch to `exefs_section`
    * Extend `exefs_section` for `EncryptionType == 3` (AesCtr): recover the section key from the decrypted key area at `KEY_AREA_AT` using `key_area_key(generation)`, seed the CTR counter from the section's crypto settings, and AES-CTR-decrypt the section bytes; lazy-import the cipher in the same crypto section as the existing `from Crypto.Cipher import AES` discipline so a default (no `-b`) run never loads pycryptodome; return `None` when the key-area key is missing or the section is otherwise un-keyable (per-package skip, never a crash). The titlekey (rights-ID) variant is a staged boundary: return `None` so it is skipped per-package rather than implemented now
    * _Design: Scope and risk (real-crypto path); Components and Interfaces → Section location and decryption; Error Handling. Requirements: 2.1, 4.3_

* [x] 7. Wire the CLI flag, keys precondition, and output into `main()`
  * [x] 7.1 Add the `-b`/`--build-id` flag and the up-front keys precondition
    * In `main()`, add `parser.add_argument('-b', '--build-id', action='store_true', dest='build_id', help=...)`; after the folder check and the `-k` existence check but before the per-package loop, when `args.build_id` is set, enforce the precondition: if no keys source exists (no `-k`, no default `prod.keys`), or the keys file yields no `header_key`, write a one-line setup error to stderr and `sys.exit(1)` before processing any package (distinct from the default warn-once-and-skip)
    * _Design: Components and Interfaces → CLI wiring; Keys precondition; Error Handling table. Requirements: 1.1, 2.3, 4.1_

  * [x] 7.2 Add the lazy `key_area_key` accessor and resolve/print Build IDs
    * Add a sibling lazy accessor `key_area_key(generation)` beside the existing `header_key()` closure, caching parsed `key_area_key_application_XX` values from the keys file (a missing key-area key is not fatal up front). Collect the scanned `(path, title_ids)` pairs during the existing folder scan; when `build_id` is set, for each base game call `build_id_of` and print `f'{app:016X} {module_id.hex().upper()}'`; when a game resolves no Build ID, skip it with a one-line stderr message and continue; when unset, print `f'{app:016X}'` exactly as today. Preserve the exit contract after skips (`0` one game, `3` several, `1` nothing-resolved/setup error, `2` bad args) and never print a key value
    * _Design: Components and Interfaces → CLI wiring; Data flow; Error Handling. Requirements: 1.2, 1.3, 1.4, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3_

* [x] 8. Checkpoint - Ensure all tests pass
  * Ensure all tests pass, ask the user if questions arise.

* [x] 9. CLI example, edge, and preservation tests
  * [x] 9.1 Write the flag-acceptance and known-value CLI tests
    * In `tests/test_cli.py`: assert argparse accepts `-b`/`--build-id` (1.1); assert a synthetic package with a known embedded Build ID prints `<TitleID> <BuildID>` with the expected uppercase hex separated by a single ASCII space (6.2, 1.3, 1.4), using `run_cli` and `write_keys`
    * _Design: Testing Strategy → Example and edge tests. Requirements: 1.1, 1.3, 1.4, 6.2_

  * [x] 9.2 Write the keys-precondition and skip CLI tests
    * In `tests/test_cli.py`: `-b` with no keys source exits `1` with a one-line setup error and empty stdout before any package output (2.3, 4.1, 6.3); `-b` with a keys file lacking `header_key` exits `1` up front (4.1); a ticket-only game is skipped with a one-line stderr message while a synthetic program-NCA game still prints its Build ID (4.2, 6.3); a package with no program NCA / unreadable `main` is skipped while others print (4.3)
    * _Design: Testing Strategy → Example and edge tests; Error Handling. Requirements: 2.3, 4.1, 4.2, 4.3, 6.3_

  * [x] 9.3 Write the exit-code-contract CLI tests
    * In `tests/test_cli.py`: representative folders yielding one game (`0`), several games (`3`), and all-skipped/nothing-resolved (`1`) under `-b`; confirm bad args give `2` via argparse
    * _Design: Testing Strategy → Example and edge tests; Error Handling table. Requirements: 4.4, 4.5_

  * [x] 9.4 Write the default-output property and unchanged-behavior tests
    * In `tests/test_cli.py`, seeded `random.Random(0)`, at least 100 iterations: for each generated mixed folder assert the no-`-b` run's stdout and exit code are byte-identical to the default-mode baseline, and that no program NCA is decrypted / no `main` NSO is read; add an example test asserting a default run emits no Build IDs and matches the existing one-line-per-Title-ID format
    * `# Feature: build-id, Property 5: Default output is unchanged when the flag is absent`
    * **Validates: Requirements 1.2, 5.1, 5.2, 5.3, 6.4**

* [x] 10. Gated real-fixture integration tests
  * [x] 10.1 Write the real-crypto fixture tests
    * In `tests/test_cli.py`, gated with `@test.skip_if(not HAVE_REAL_NSPS, reason=NO_REAL_NSPS)` and additionally on `HAVE_REAL_KEYS` (the real-crypto path needs real keys): for each present `<TitleID>-<BuildID>.nsp` fixture, run `-b` with `REAL_KEYS` and assert the printed Build ID equals the uppercase hex after the hyphen in the filename (via the task 1.2 helper)
    * _Design: Testing Strategy → Integration tests. Requirements: 6.5, 6.6_
    * Note (user-approved scope expansion): the real fixtures all use titlekey (non-zero `RightsId`) program NCAs, so 10.1 also covered the titlekey path: `read_titlekeks`, `ticket_of`, `title_key_of` and a `titlekek` callback threaded through `build_id_of` → `module_id_of` → `exefs_section`/`_ctr_decrypt_section` (as `title_key`); synthetic `ticket`/`titlekey_package`/`write_titlekek_keys` builders; unit and CLI tests for the round trip and the missing-ticket, missing-titlekek, and personalized/unsupported-ticket skips; a synthetic key-area AES-CTR round trip; the `_TITLEKEY_PATH` gate and the old skip test removed. See Requirement 7 and the design's "Titlekey flow". The open decision is resolved (user-approved) as first-8-bytes: the real 32-byte ModuleId is the filename Build ID plus 8 further non-zero bytes plus 16 zero bytes, and `-b` prints only the first 8 bytes (16 uppercase hex digits) via `render_build_id`, so it equals the filename Build ID.

* [x] 11. Final verification and docs
  * [x] 11.1 Run the full verification gate and update docs
    * Run `uvx pyrefly check src/nx_title_id/__init__.py tests/builders.py tests/test_title.py tests/test_cli.py` (zero diagnostics), `uv run ruff check`, `uv run ruff format --check`, and `uv run tryke test` (never bare `tryke`); fix any findings
    * Update `README.md` with `-b`/`--build-id` usage and the `<TitleID> <BuildID>` output form, and add a `## Unreleased` entry to `CHANGELOG.md`
    * Per the project rule, DO NOT COMMIT; leave all changes staged for the human
    * _Design: Overview; Error Handling (no-secrets invariant). Requirements: 1.1, 1.3, 1.4, 5.3_

* [x] 12. Refresh project documentation
  * [x] 12.1 Update comprehensive.md, quickstart.md, AGENTS.md, and README.md for the Build-ID feature
    * Re-derive every line reference from the current `src/nx_title_id/__init__.py`, regenerate the function and constant inventories, and document the Build-ID flow, titlekey flow, real NCA layout, newest-update rule, first-8-bytes rendering, test builders, fixtures named `<TitleID>-<BuildID>.nsp`, and the `build-id` spec, following the steering files (`comprehensive-and-quickstart`, `generate-agents-md`, `root-level-readme`, `markdown-style`)
    * Run `rumdl check` and `rumdl fmt` on the four files; DO NOT COMMIT
    * _Design: Overview; Testing strategy. Requirements: 1.1, 1.3, 1.4, 5.3_

## Notes

* Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; top-level tasks are never optional.
* The synthetic (None-encryption) path and its property/example tests come first (tasks 1-5, 9) so the feature is provably testable in CI without real assets; the real-crypto AES-CTR path (task 6) and gated integration tests (task 10) follow.
* Each property test uses a seeded `random.Random(0)` loop (never generated in a discovery-time `for` loop), runs at least 100 iterations, and carries the `# Feature: build-id, Property N: {text}` tag.
* All code keeps zero Pyrefly diagnostics, Ruff-clean formatting/linting, specific type annotations, the `# ---` section ordering, and the single documented lazy crypto import; key values are never printed.
* The exit-code contract (`0`/`1`/`2`/`3`) is preserved after per-package skips.

## Task dependency graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2", "3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3"] },
    { "id": 4, "tasks": ["3.4", "4.1"] },
    { "id": 5, "tasks": ["4.2", "4.3", "5.1"] },
    { "id": 6, "tasks": ["5.2", "6.1"] },
    { "id": 7, "tasks": ["5.3", "5.4", "7.1"] },
    { "id": 8, "tasks": ["7.2"] },
    { "id": 9, "tasks": ["9.1", "9.2", "9.3", "9.4"] },
    { "id": 10, "tasks": ["10.1"] },
    { "id": 11, "tasks": ["11.1"] },
    { "id": 12, "tasks": ["12.1"] }
  ]
}
```
