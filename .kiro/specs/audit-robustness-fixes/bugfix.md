# Bugfix Requirements Document

## Introduction

The deep architecture audit (`docs/comprehensive.md`, "Likely or unverified issues" and "Code quality gaps") lists several robustness defects in the `nx-title-id` CLI. Unreadable files crash the run with a traceback, corrupt partition headers can make the tool read very large blocks into memory, a DLC at index `0x800` may be folded to the wrong base game, a `prod.keys` without `header_key` aborts the run and discards IDs already resolved, and a folder with several games exits 0. There are also typing and documentation gaps in the code.

Items are listed in priority order. One item needs research before design, and two items are behavior changes that have been decided:

* **Research prerequisite (Requirement group C):** confirm whether real DLC uses index `0x800` (title ID `base + 0x1000 + 0x800`). The fix in 2.5 applies only if it does. Tests are added either way.
* **Decided (Requirement group D):** for a `prod.keys` without `header_key`, the tool warns once, disables the meta NCA fallback, continues with the remaining packages, and still prints the IDs already resolved (2.6–2.7).
* **Decided (Requirement group E):** a folder with several games prints every ID to stdout and exits with code `3` (2.8). The new exit code is documented in `README.md` ("Exit status") and `CHANGELOG.md` (2.9).
* **Decided (Requirement group G):** multi-title detection within a single package IS now in scope. A package that contains several base games (for example a collection XCI) contributes every distinct base game ID to the folder result set, so it folds into the same group E output and exit code `3` (2.13–2.14). Human-friendly game names remain out of scope for this spec.

Group F (code quality) is lower priority and must not change runtime behavior.

## Bug analysis

### Current behavior (Defect)

#### A. Unreadable files (priority 1)

1.1 WHEN a package file in the folder cannot be opened or read (for example permission denied) THEN the system raises an uncaught `OSError` from `is_package()` or `title_of()`, prints a traceback, and stops the run without printing the title IDs of the other packages

1.2 WHEN the keys file (explicit `-k` or the default `~/.switch/prod.keys`) exists but cannot be read and a package needs the meta NCA fallback THEN the system raises an uncaught `OSError` from `read_header_key()` and prints a traceback

#### B. Large declared counts on corrupt files (priority 2)

1.3 WHEN a PFS0 or HFS0 header declares an entry count or string table size far larger than any real package (for example a corrupt multi-gigabyte file) THEN the system reads `count * entry_size` bytes and `strings_size` bytes into memory without a sanity limit, bounded only by the file size

#### C. DLC index `0x800` (priority 3, research prerequisite)

1.4 WHEN `application_of()` receives a DLC title ID whose low 12 bits are `0x800` (`base + 0x1000 + 0x800`) THEN the system treats it as an update and returns `base + 0x1000` instead of `base` (confirmed by calling the function; whether real DLC uses this index is unverified)

#### D. `prod.keys` without `header_key` (priority 4)

1.5 WHEN the keys file has no `header_key` line and a package needs the meta NCA fallback THEN the system exits non-zero with `… has no header_key`, discarding the title IDs already resolved for other packages and not processing the remaining packages

#### E. Multi-game folders (priority 5)

1.6 WHEN the folder resolves to more than one base game title ID THEN the system exits 0, so scripts that check only the exit code cannot tell one game from several

#### F. Code quality (priority 6)

1.7 WHEN the module is type-checked THEN several parameters and return values are untyped (`path`, `header_key`, `tid`, and the returns of `partition`, `package_files`, `meta_title_of`, `title_of`, `application_of`) and `Any` is used for lists, the keys path, and the `apps` set

1.8 WHEN a reader checks the module against the project's "imports are always global" convention THEN the deferred `from Crypto.Cipher import AES` inside `xts_decrypt_sector()` is not documented as an intentional exception

1.9 WHEN a package is processed THEN the system opens it up to three times (`is_package()`, `title_of()`, `meta_title_of()`)

#### G. Multiple titles in one package (priority 7)

1.10 WHEN a single package contains more than one base game (for example a collection XCI such as `tests/ff.xci`, which carries 6 titles including at least one update) THEN the system resolves it to a single base game ID, because `title_of()` returns one ID per package and `meta_title_of()` collapses every meta NCA to the lowest title ID, so the other base games are silent

### Expected behavior (Correct)

#### A. Unreadable files

2.1 WHEN a package file cannot be opened or read THEN the system SHALL print `skipping <name>: <reason>` to stderr, skip that package, and continue with the others

2.2 WHEN the keys file cannot be read and a package needs the meta NCA fallback THEN the system SHALL exit non-zero with a one-line message naming the keys path and the reason, without a traceback and without printing any key material

#### B. Large declared counts

2.3 WHEN a PFS0 or HFS0 header declares an entry count or string table size above a fixed sanity limit THEN the system SHALL raise `MalformedPackageError` before reading the entries or the string table, so `main()` skips that package with a stderr message

2.4 WHEN the sanity limit is chosen THEN it SHALL be well above any value a real package uses, so no valid package is rejected (the exact values are a design decision)

#### C. DLC index `0x800`

2.5 WHEN research confirms that real DLC can use index `0x800` THEN `application_of()` SHALL map `base + 0x1000 + 0x800` to `base`, while updates (`base + 0x800`) still map to `base`. WHEN research shows real DLC never uses that index THEN the current mapping SHALL be kept and the limitation documented. In both cases tests SHALL pin the chosen behavior for base, update, DLC index 1, and DLC index `0x800`

#### D. `prod.keys` without `header_key`

2.6 WHEN the keys file has no `header_key` and a package needs the meta NCA fallback THEN the system SHALL print one warning to stderr naming the keys path, disable the meta NCA fallback for the rest of the run, and continue processing the remaining packages

2.7 WHEN the meta NCA fallback has been disabled this way THEN each package that still has no title ID SHALL be skipped with a stderr message that says the keys file has no `header_key`, and the title IDs resolved from other packages SHALL still be printed

#### E. Multi-game folders

2.8 WHEN the folder resolves to more than one base game title ID THEN the system SHALL print every title ID to stdout as today, print the warning to stderr, and exit with code `3`, which differs from the existing error exits (`1`, `2`)

2.9 WHEN the multi-game exit behavior changes THEN `README.md` ("Exit status") and `CHANGELOG.md` SHALL document the new exit code

#### F. Code quality

2.10 WHEN the module is type-checked THEN every function parameter and return value SHALL have a specific annotation, `Any` SHALL be replaced with specific types where they can be determined, and `pyrefly check` SHALL report zero errors

2.11 WHEN a reader looks at `xts_decrypt_sector()` THEN a comment SHALL state that the deferred `Crypto` import is an intentional exception to the global-imports convention, so packages resolved by ticket or tag never load `pycryptodome`

2.12 WHERE reducing repeated opens is done (optional) THEN each package SHALL be opened at most once per run with no change in output

#### G. Multiple titles in one package

2.13 WHEN a package is resolved THEN the system SHALL collect a title ID from every ticket entry and every qualifying meta NCA in that package, fold each through `application_of()`, and contribute every distinct base game ID to the folder result set

2.14 WHEN a single package yields two or more distinct base game IDs THEN the system SHALL trigger the group E multi-game output (print every title ID to stdout, print the warning to stderr) and exit with code `3`

2.15 IF `prod.keys` is unavailable or has no `header_key` THEN multi-title detection SHALL rely on tickets alone, so a multi-title cartridge that carries no tickets MAY resolve to one or zero base game IDs; this is a known limitation of detection without keys, not a defect

### Unchanged behavior (Regression prevention)

3.1 WHEN the tool runs THEN the system SHALL CONTINUE TO write only title IDs (16 uppercase hex digits, one per line, sorted) to stdout and every diagnostic to stderr

3.2 WHEN a package resolves through its ticket name or a `[title ID]` filename tag THEN the system SHALL CONTINUE TO not read `prod.keys` and not import `pycryptodome`

3.3 WHEN several packages need the meta NCA fallback THEN the system SHALL CONTINUE TO read `prod.keys` at most once per run

3.4 WHEN a package's title ID is resolved THEN the system SHALL CONTINUE TO try the ticket name first, then the filename tag, then the meta NCA

3.5 WHEN `application_of()` receives a base game ID (low 12 bits `0x000`), an update ID (`0x800` on a base game), or a DLC ID with an index other than `0x800` THEN the system SHALL CONTINUE TO return the same base game ID as today

3.6 WHEN a package has a valid PFS0 or HFS0 header within the sanity limits THEN the system SHALL CONTINUE TO parse it and return the same entries as today

3.7 WHEN a package is truncated, has an unterminated name, has the wrong magic, or yields no title ID THEN the system SHALL CONTINUE TO skip it with the same stderr messages as today

3.8 WHEN the folder argument is not a folder, an explicit `-k` path does not exist, or no package has a readable title ID THEN the system SHALL CONTINUE TO exit `1` with the same messages, and bad CLI arguments SHALL CONTINUE TO exit `2`

3.9 WHEN the folder resolves to exactly one base game THEN the system SHALL CONTINUE TO print it and exit `0`

3.10 WHEN any error or warning is printed THEN the system SHALL CONTINUE TO never print key values

3.11 WHEN `uv run tryke test` runs THEN the existing tests in `tests/` SHALL CONTINUE TO pass, except tests that pin behavior this spec deliberately changes (groups D, E, and G), which are updated to the decided behavior

3.12 WHEN a package contains exactly one base game across all of its titles (for example a base game together with its update and its DLC) THEN the system SHALL CONTINUE TO resolve it to exactly one base game ID and, when it is the only game in the folder, exit `0`

3.13 WHEN a package is resolved THEN the system SHALL CONTINUE TO try the ticket entries first, then the filename tag, then the meta NCAs, and SHALL CONTINUE TO read `prod.keys` and import `pycryptodome` only when a meta NCA is needed

3.14 WHEN multi-title detection runs THEN the system SHALL CONTINUE TO write only title IDs (16 uppercase hex digits, one per line, sorted) to stdout, every diagnostic to stderr, and never print key values

3.15 WHEN the real multi-title fixture `tests/ff.xci` is referenced THEN it SHALL CONTINUE TO be gitignored like the other real packages and used by tests only when it is present and, for the meta NCA path, only when `prod.keys` is available
