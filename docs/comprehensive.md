# Deep Architecture Audit

A detailed walkthrough of `nx-title-id`: how it starts, what each piece does, where it makes decisions and has side effects, what can go wrong, and why it's built this way. For the short version, see [the quick flow summary](quickstart.md).

## Scope and sources

* **Code audited:** `src/nx_title_id/__init__.py` (the whole implementation), `src/nx_title_id/__main__.py`, `pyproject.toml`, `README.md`, `CHANGELOG.md`.

* **Line references** point at `src/nx_title_id/__init__.py` unless noted otherwise.

* **Format facts** (PFS0, HFS0, XCI card header, NCA header layout, Nintendo's XTS tweak) come from the module and function docstrings. They were not checked against an external specification.

* **Runtime behavior** marked "reproduced" was confirmed by running the test suite (`uv run tryke test`) against synthetic and real packages. Everything else comes from reading the code.

## Entry points

| Entry point                  | Defined in                           | Resolves to                                                   |
|------------------------------|--------------------------------------|---------------------------------------------------------------|
| `nx-title-id` console script | `pyproject.toml` `[project.scripts]` | `nx_title_id:main`                                            |
| `python -m nx_title_id`      | `src/nx_title_id/__main__.py`        | `from nx_title_id import main; main()`                        |
| `uvx nx-title-id`            | PyPI distribution `nx-title-id`      | The console script with the same name                         |
| Library call                 | `main(argv=None)`, line 250          | `argparse` parses `argv`, or `sys.argv[1:]` when it is `None` |

Packaging, from `pyproject.toml`:

* Build backend is `hatchling`. The wheel ships `src/nx_title_id`, and the sdist is limited to `src/`, `README.md`, `LICENSE`, and `CHANGELOG.md`.

* The version is dynamic, read from `__version__ = '1.0.0'` (line 28) by `[tool.hatch.version]`.

* `requires-python = ">=3.14"`. The only runtime dependency is `pycryptodome>=4.2,<5`.

## CLI startup and initialization flow

Importing the module (before `main()` runs) defines a few constants and nothing else:

| Name               | Line | Value / purpose                                                   |
|--------------------|------|-------------------------------------------------------------------|
| `__version__`      | 28   | `'1.0.0'`, single source of the package version                   |
| `EXTENSIONS`       | 30   | `('.nsp', '.nsz', '.xci', '.xcz')`, matched case-insensitively    |
| `DEFAULT_KEYS`     | 31   | `~/.switch/prod.keys`, resolved from `Path.home()` at import time |
| `META`             | 32   | `1`, the NCA content type for meta NCAs                           |
| `MAX_ENTRIES`      | 38   | `0x10000`, the sanity cap on a partition's declared entry count   |
| `MAX_STRINGS_SIZE` | 39   | `0x100000` (1 MiB), the sanity cap on a partition's string table  |

Import has no I/O side effects. `pycryptodome` is not imported here.

`main()` startup, in order:

1. **Argument parsing** (lines 252–266): positional `folder` (`Path`), optional `-k/--keys` (`Path`), and `-V/--version`, which prints `nx-title-id 1.0.0` and exits 0. Bad arguments get `argparse`'s exit code 2.

2. **Folder validation** (line 268): if `folder` is not a directory, exit with `not a folder: …`.

3. **Keys resolution** (line 272): `args.keys`, else `DEFAULT_KEYS` if it is a file, else `None`.

4. **Keys validation** (line 274): an explicit `-k` path that is not a file exits with `no such keys file: …`. Only existence is checked here. The contents are not read yet.

5. **Lazy key closure** (lines 277–305): `header_key()` reads `prod.keys` through `read_header_key()` on first call and caches the result in a one-element `list[bytes | None]`. A missing `header_key` warns once and caches `None`; an `OSError` on the keys file is fatal with a one-line message.

## Command-specific flows

There is one command. These are its stages.

### Folder scan

`main()` loops over `sorted(p for p in args.folder.iterdir() if p.suffix.lower() in EXTENSIONS and p.is_file())` (line 309).

* Non-recursive. Subfolders are ignored.
* Sorted by path, so output and stderr messages are deterministic.
* `is_file()` follows symlinks.

Per file:

1. Skip names starting with `._` (macOS AppleDouble files), silently (line 310).

2. One `try` wraps the per-package work (lines 313–337): `pkg.stat()` (zero-byte files are skipped silently), `is_package()`, and `title_ids_of()`. A `MalformedPackageError` or an `OSError` from any of them skips just that file with a `skipping <name>: …` stderr message and continues to the next.

3. `is_package()` (line 120) reads the first `0x104` bytes and accepts `PFS0` at offset 0 or `HEAD` at offset `0x100`. Anything else is skipped with a stderr message.

4. `title_ids_of()` returns the set of title IDs the package declares (see below).

5. An empty set is skipped with a stderr message whose wording depends on whether keys are available and whether the keys file had a `header_key` (lines 339–352).

6. Otherwise every ID is folded through `application_of()` into the `apps` set (line 354).

### Package container parsing

`package_files(f)` (line 95) reads `0x140` bytes from offset 0:

* **NSP/NSZ:** `PFS0` at 0 means `partition(f, 0, b'PFS0', 0x18)`.

* **XCI/XCZ:** `HEAD` at `0x100` means read the root HFS0 offset (u64) at `0x130`, parse the root `HFS0` (entry size `0x40`), find the entry named `secure`, and parse that `HFS0` too. A card header shorter than `0x138` bytes raises `MalformedPackageError`.

* Anything else returns `[]`.

`partition(f, at, magic, entry_size)` (line 49):

* Reads a 16-byte header: magic, entry count (u32), string table size (u32), reserved.

* **Sanity guards (before any further read):** an entry count above `MAX_ENTRIES` or a string table above `MAX_STRINGS_SIZE` raises `MalformedPackageError` naming the declared size and the limit. This keeps a corrupt header from reading up to the whole file into memory.

* Reads `count * entry_size` bytes of entries, then the string table; a short read raises `MalformedPackageError('truncated …')`.

* Each entry starts with data offset (u64), size (u64), name offset (u32). Names are NUL-terminated in the string table and decoded with `errors='replace'`; an unterminated name raises `MalformedPackageError`.

* Returns `[(name, absolute_offset)]`, where data offsets are relative to the end of the header.

* Returns `[]` on a short header or the wrong magic.

### Title ID resolution

`title_ids_of(path, header_key)` (line 203) returns a `set[int]` of every title ID the package declares, from the first source that yields anything:

1. **Tickets.** Every entry matching `[0-9A-Fa-f]{32}\.tik`; the first 16 hex digits of each rights ID is a title ID. All of them, so a multi-title cartridge contributes every ticket. If any ticket is found, the set short-circuits here (before the tag and meta NCA, so a ticket-resolved package never loads `pycryptodome`).

2. **Filename tag.** The first `[16 hex digits]` match in `path.name`, case-insensitive, as a one-element set.

3. **Meta NCAs.** Only when a `header_key` callable was passed and returns a key. It calls `meta_title_of(path, key)`. The callable returns `None` once the fallback is disabled (no `header_key` in the keys file), in which case the result is an empty set.

`meta_title_of(path, key)` (line 169):

* For every entry ending in `.nca` or `.ncz`, reads the second 0x200-byte sector of its header (`at + 0x200`).

* Decrypts it with `xts_decrypt_sector(key, sector, 1)`.

* Keeps it if the magic is `NCA3` or `NCA2` and the content type (byte 5) is `META`. The title ID is the u64 at `0x10` of that sector (absolute header offset `0x210`).

* Returns the set of every qualifying meta-NCA title ID, or an empty set.

* Per the docstring, an NSZ/XCZ's `.ncz` keeps the NCA header uncompressed, which is why the same read works for both.

`xts_decrypt_sector(key, data, sector)` (line 142):

* Splits the 32-byte key into a data key and a tweak key (AES-128 each, ECB).

* Tweak = `AES_tweak(sector as 16-byte big-endian)`. Standard XTS uses little-endian here; Nintendo uses big-endian.

* For each 16-byte block: XOR with the tweak, ECB-decrypt, XOR again, then multiply the tweak by x in GF(2^128) (shift left; on overflow XOR with `0x87`).

* `from Crypto.Cipher import AES` sits inside this function (line 153), with a comment marking it as a deliberate exception to the "imports are always global" convention so ticket/tag-resolved packages never load `pycryptodome`.

### Base game normalization

`application_of(tid)` (line 233) looks at the low 12 bits:

| `tid & 0xFFF` | Interpreted as | Result                    |
|---------------|----------------|---------------------------|
| `0x000`       | Base game      | `tid`                     |
| `0x800`       | Update         | `tid & ~0xFFF`            |
| anything else | DLC            | `(tid & ~0xFFF) - 0x1000` |

Real DLC indexes run 1 to 2000 (0x7D0), so a low-12-bit value of `0x800` is always an update, never a DLC (docstring, line 236).

### Output and exit

* An empty `apps` set exits with `no package with a readable title ID in …` (line 357).
* More than one ID writes `more than one game in the folder:` to stderr (line 360).
* Every ID is printed to stdout, sorted, as `{app:016X}`: 16 uppercase hex digits, one per line.
* More than one game then exits `3` after printing (line 364).

| Outcome                                                                      | Exit code | Stream                     |
|------------------------------------------------------------------------------|-----------|----------------------------|
| One game (one base ID printed)                                               | 0         | stdout (+ stderr warnings) |
| `--version` / `--help`                                                       | 0         | stdout                     |
| Bad CLI arguments                                                            | 2         | stderr (`argparse`)        |
| Not a folder, missing or unreadable keys file, no package with a readable ID | 1         | stderr (`sys.exit(str)`)   |
| More than one game (every ID printed first)                                  | 3         | stdout + stderr warning    |

## File and module responsibilities

| File                          | Responsibility                                                                                       |
|-------------------------------|------------------------------------------------------------------------------------------------------|
| `pyproject.toml`              | Metadata, the one runtime dependency, console script, hatchling wheel/sdist targets, dynamic version |
| `src/nx_title_id/__init__.py` | The whole implementation: container parsing, key loading, XTS, resolution, CLI                       |
| `src/nx_title_id/__main__.py` | `python -m` shim                                                                                     |
| `README.md`                   | User-facing usage, resolution order, exit status table, dev commands                                 |
| `CHANGELOG.md`                | Keep a Changelog history; the robustness fixes are under `## Unreleased`                             |
| `uv.lock`                     | Locked dependency versions for `uv sync`                                                             |

Function inventory in `__init__.py`:

| Function             | Line | Reads from disk                | Pure | Returns     | Can raise                                  |
|----------------------|------|--------------------------------|------|-------------|--------------------------------------------|
| `partition`          | 49   | Header + entries + strings     | No   | `list[...]` | `MalformedPackageError`                    |
| `package_files`      | 95   | `0x140` bytes, then partitions | No   | `list[...]` | Same as `partition`                        |
| `is_package`         | 120  | `0x104` bytes                  | No   | `bool`      | `OSError`                                  |
| `read_header_key`    | 128  | The whole `prod.keys`          | No   | `bytes`     | `ValueError` (no key), `OSError`           |
| `xts_decrypt_sector` | 142  | None                           | Yes  | `bytes`     | `ImportError` if `pycryptodome` is missing |
| `meta_title_of`      | 169  | `0x200` bytes per NCA/NCZ      | No   | `set[int]`  | Same as `package_files`                    |
| `title_ids_of`       | 203  | Via `package_files`            | No   | `set[int]`  | Same as above                              |
| `application_of`     | 233  | None                           | Yes  | `int`       | None                                       |
| `main`               | 250  | Folder listing, `stat`         | No   | `None`      | Exits through `sys.exit`                   |

Every parameter, return, and notable local now carries a specific type annotation; there is no `Any` in the module.

## Decision points and side effects

Decision points:

* **Keys source** (line 272): explicit `-k` wins, then the default path if it exists, otherwise no meta fallback.

* **Explicit keys must exist** (line 274): `-k missing.keys` fails even if no package would need it.

* **Package filter** (lines 309–328): extension, then `._`, then zero-byte, then magic bytes.

* **Per-package isolation** (lines 313–337): `stat`/`is_package`/`title_ids_of` share one `try`; a `MalformedPackageError` or `OSError` skips only that package.

* **Resolution order** (lines 211–230): tickets, then tag, then meta NCAs, each contributing a set.

* **Container type** (lines 104–117): PFS0 or card header, otherwise nothing.

* **Partition sanity guards** (lines 62–72): entry count and string table size caps before any body read.

* **Meta NCA selection** (lines 183–199): valid magic, `META` content type; every match is collected.

* **Base mapping** (lines 240–247): the low-12-bit rules above.

* **Missing `header_key` is a warning** (lines 294–296): the first package that needs it triggers one stderr warning, the key caches as `None`, and packages needing the fallback are skipped while the rest still print.

* **Unreadable keys file is fatal** (lines 298–299): an `OSError` reading `prod.keys` exits `1` with a one-line message, not a traceback.

* **Several games exit 3** (lines 363–364): after printing every ID.

Side effects:

* **Reads only.** Nothing is written to disk.

* Each package is opened two or three times (`is_package`, `title_ids_of`, `meta_title_of`). Each open reads only headers.

* `prod.keys` is read at most once per run (success or failure is cached).

* stdout carries title IDs only. Everything else goes to stderr, so the output is safe to pipe.

* Key material stays in memory and never appears in output or error messages. Error messages name the keys path, not its contents.

## Risks, gaps, and follow-up inspections

### Resolved issues

These were raised by a prior audit and are fixed by the `audit-robustness-fixes` spec (see `CHANGELOG.md`, `## Unreleased`). All are covered by `tests/test_containers.py`, `tests/test_cli.py`, and `tests/test_title.py`.

* **Malformed packages (A, B).** An unterminated name, a truncated entry or string table, a truncated card header, or an oversized declared count or string table now raises `MalformedPackageError`, which `main()` skips with a `skipping <file>: …` message. The oversized-count path is capped by `MAX_ENTRIES` / `MAX_STRINGS_SIZE` before any large read.

* **Unreadable files (A).** An `OSError` (for example permission denied) from `stat`, `is_package()`, or `title_ids_of()` skips just that package with a stderr message instead of surfacing a traceback. An unreadable keys file exits `1` with a one-line error.

* **Missing `header_key` (D).** A keys file without `header_key` now warns once and skips only the packages that need the meta fallback; IDs already found from other packages are still printed.

* **Several games (E) and multi-title cartridges (G).** `title_ids_of()` and `meta_title_of()` return sets, so every base game a folder or a single cartridge carries is collected. More than one base game prints every ID and exits `3`, so a script can distinguish "one game" from "several" by the exit code alone.

* **Loose typing (F).** Every signature and notable local is now specifically annotated; `Any` is gone. The deferred `Crypto` import is documented as a deliberate exception. Pyrefly reports 0 errors.

### Verification

`uv run tryke test` runs the tests in `tests/` against synthetic packages, plus real base-game NSPs (`tests/<title ID>.nsp`, gitignored) and a real multi-title cartridge (`tests/ff.xci`, gitignored) when they are present. The meta NCA path is checked against those files when `~/.switch/prod.keys` exists. NCA headers in the synthetic fixtures are encrypted with `cryptography`'s AES-XTS, so `xts_decrypt_sector()` is checked against an independent implementation.

Seeded-loop property tests stand in for a property generator (no `hypothesis` dependency) and assert that stdout holds only sorted, unique `^[0-9A-F]{16}$` lines with no key value on either stream. The full suite passes with Pyrefly at 0 errors and Ruff clean. `.github/workflows/test.yml` was not reviewed and may not run them yet.

### Likely or unverified issues

* **DLC index `0x800` is read as an update (documented).** `application_of()` tests `low == 0x800` before the DLC branch, so `base + 0x1000 + 0x800` maps to `base + 0x1000` instead of `base` (confirmed by calling the function). Real DLC indexes are documented as 1 to 2000 (0x7D0), so this is expected rather than a defect, but it was not checked against an external specification.

* **Ticket-only detection limit.** Without keys, a cartridge is resolved by its tickets alone; a cartridge carrying no tickets (or fewer than it has games) may resolve to fewer IDs. This is a known limitation, not a defect.

* **Three opens per package.** `is_package`, `title_ids_of`, and `meta_title_of` each open the file. That is harmless for header-only reads; an optional single-open refactor was deliberately deferred.

### Documented limitations

From `README.md`: XCIs with the `0x1000`-byte key area at the start, and `NCA0` headers from very early titles, are not supported. Cartridge images rarely carry tickets, so they usually need a filename tag or keys.

## Design rationale

* **Cheapest reliable source first.** Ticket names and filename tags need no keys and no crypto. The meta NCA is the last resort because it needs user-supplied `prod.keys` and decryption.

* **Ticket over tag.** The ticket comes from the package's own contents, and filenames are routinely renamed or mistagged (comment in `title_ids_of()`, `README.md`).

* **Sets everywhere, folded at the end.** Returning every declared title ID as a set keeps the resolution path honest about multi-title packages; `application_of()` then folds to base games so a single game still prints a single line. This is what lets a multi-title cartridge report every game it carries.

* **Lazy, cached keys and a lazy crypto import.** Most packages never need keys, so the tool does not touch `prod.keys` or load `pycryptodome` unless one does. That keeps runs fast, keeps key exposure to a minimum, and means a missing or broken keys file only matters when it is actually needed. Caching the result (including a failed read as `None`) keeps the warning to once per run.

* **Fail soft per package.** One corrupt, oversized, or unreadable package should not sink a whole folder, so each package's work is isolated in a `try` and a failure becomes a skip message. Only a genuinely fatal condition (bad arguments, not a folder, unreadable keys file, nothing resolvable) exits non-zero.

* **Sanity caps on declared sizes.** A corrupt header could otherwise ask the tool to read gigabytes into memory; `MAX_ENTRIES` and `MAX_STRINGS_SIZE` reject absurd declarations before any large read while staying far above any real package.

* **Hand-rolled XTS on ECB.** `pycryptodome` has no XTS mode, and Nintendo's NCA headers use a big-endian sector tweak, which a stock XTS implementation would not match anyway (docstring, line 144).

* **Header-only reads.** Only partition tables and one 0x200-byte sector per NCA are read, so a folder of multi-gigabyte packages scans quickly, and NSZ/XCZ work without decompression because `.ncz` keeps the NCA header as-is.

* **Base normalization for one answer per game.** Packages and folders often mix a base game with updates and DLC. Folding every ID to its base gives the one answer a user wants per game.

* **Magic-byte gate.** Checking `PFS0`/`HEAD` before trusting the filename stops a stray file with a package extension and a tag from producing a bogus ID.

* **Clean stdout and a meaningful exit code.** Only title IDs go to stdout and diagnostics go to stderr, so `$(nx-title-id dir)` works in shell scripts; the `0`/`3` split lets a script tell one game from several without parsing `stderr`.

* **Single module, single dependency.** The whole tool is one file with one optional-at-runtime dependency. That keeps `uvx nx-title-id` installs small and fast.
