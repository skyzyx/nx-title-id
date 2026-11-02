# nx-title-id Deep Architecture Audit

A detailed walkthrough of `nx-title-id`: how it starts, what each piece does, where it makes decisions and has side effects, what can go wrong, and why it's built this way. For the short version, see [the quick flow summary](quickstart.md).

## Scope and sources

* **Code audited:** `src/nx_title_id/__init__.py` (the whole implementation), `src/nx_title_id/__main__.py`, `pyproject.toml`, `README.md`.
* **Line references** point at `src/nx_title_id/__init__.py` unless noted otherwise.
* **Format facts** (PFS0, HFS0, XCI card header, NCA header layout, Nintendo's XTS tweak) come from the module and function docstrings. I haven't checked them against an external specification.
* **Runtime behavior** marked "reproduced" was confirmed by running the CLI against small crafted files. Everything else comes from reading the code.

## Entry points

| Entry point                  | Defined in                           | Resolves to                                                  |
|------------------------------|--------------------------------------|--------------------------------------------------------------|
| `nx-title-id` console script | `pyproject.toml` `[project.scripts]` | `nx_title_id:main`                                           |
| `python -m nx_title_id`      | `src/nx_title_id/__main__.py`        | `from nx_title_id import main; main()`                       |
| `uvx nx-title-id`            | PyPI distribution `nx-title-id`      | The console script with the same name                        |
| Library call                 | `main(argv=None)`, line 198          | `argparse` parses `argv`, or `sys.argv[1:]` when it's `None` |

Packaging, from `pyproject.toml`:

* Build backend is `hatchling`. The wheel ships `src/nx_title_id`, and the sdist is limited to `src/`, `README.md`, `LICENSE`, and `CHANGELOG.md`.
* The version is dynamic, read from `__version__ = '1.0.0'` (line 28) by `[tool.hatch.version]`.
* `requires-python = ">=3.14"`. The only runtime dependency is `pycryptodome>=4.2,<5`.

## CLI startup and initialization flow

Importing the module (before `main()` runs) defines a few constants and nothing else:

| Name           | Line | Value / purpose                                                   |
|----------------|------|-------------------------------------------------------------------|
| `__version__`  | 28   | `'1.0.0'`, single source of the package version                   |
| `EXTENSIONS`   | 30   | `('.nsp', '.nsz', '.xci', '.xcz')`, matched case-insensitively    |
| `DEFAULT_KEYS` | 31   | `~/.switch/prod.keys`, resolved from `Path.home()` at import time |
| `META`         | 32   | `1`, the NCA content type for meta NCAs                           |

Import has no I/O side effects. `pycryptodome` isn't imported here.

`main()` startup, in order:

1. **Argument parsing** (lines 199–213): positional `folder` (`Path`), optional `-k/--keys` (`Path`), and `-V/--version`, which prints `nx-title-id 1.0.0` and exits 0. Bad arguments get `argparse`'s exit code 2.
2. **Folder validation** (line 216): if `folder` isn't a directory, exit with `not a folder: …`.
3. **Keys resolution** (line 219): `args.keys`, else `DEFAULT_KEYS` if it's a file, else `None`.
4. **Keys validation** (line 221): an explicit `-k` path that isn't a file exits with `no such keys file: …`. It's only checked for existence here. The contents aren't read yet.
5. **Lazy key closure** (lines 224–230): `header_key()` reads `prod.keys` through `read_header_key()` on first call and caches the 32-byte key in a one-element list.

## Command-specific flows

There's one command. These are its stages.

### Folder scan

`main()` loops over `sorted(p for p in args.folder.iterdir() if p.suffix.lower() in EXTENSIONS and p.is_file())` (line 234).

* Non-recursive. Subfolders are ignored.
* Sorted by path, so output and stderr messages are deterministic.
* `is_file()` follows symlinks.

Per file:

1. Skip names starting with `._` (macOS AppleDouble files) and zero-byte files, silently (line 235).
2. `is_package()` (line 83) reads the first `0x104` bytes and accepts `PFS0` at offset 0 or `HEAD` at offset `0x100`. Anything else is skipped with a stderr message (line 238).
3. `title_of()` resolves the title ID (see below). A `ValueError` from it exits the whole run (lines 243–245).
4. A `None` result is skipped with a stderr message whose wording depends on whether keys are available (lines 247–253).
5. Otherwise `application_of(tid)` goes into the `apps` set.

### Package container parsing

`package_files(f)` (line 61) reads `0x140` bytes from offset 0:

* **NSP/NSZ:** `PFS0` at 0 means `partition(f, 0, b'PFS0', 0x18)`.
* **XCI/XCZ:** `HEAD` at `0x100` means read the root HFS0 offset (u64) at `0x130`, parse the root `HFS0` (entry size `0x40`), find the entry named `secure`, and parse that `HFS0` too.
* Anything else returns `[]`.

`partition(f, at, magic, entry_size)` (line 35):

* Reads a 16-byte header: magic, entry count (u32), string table size (u32), reserved.
* Reads `count * entry_size` bytes of entries, then the string table.
* Each entry starts with data offset (u64), size (u64), name offset (u32). Names are NUL-terminated in the string table and decoded with `errors='replace'`.
* Returns `(name, absolute_offset)`, where data offsets are relative to the end of the header.
* Returns `[]` on a short read or the wrong magic.

### Title ID resolution

`title_of(path, header_key)` (line 162) tries three sources and stops at the first hit:

1. **Ticket name.** Any entry matching `[0-9A-Fa-f]{32}\.tik`. The first 16 hex digits of the rights ID are the title ID.
2. **Filename tag.** The first `[16 hex digits]` match in `path.name`, case-insensitive.
3. **Meta NCA.** Only when a `header_key` callable was passed, meaning keys are available. It calls `meta_title_of(path, header_key())`.

`meta_title_of(path, key)` (line 131):

* For every entry ending in `.nca` or `.ncz`, reads the second 0x200-byte sector of its header (`at + 0x200`).
* Decrypts it with `xts_decrypt_sector(key, sector, 1)`.
* Keeps it if the magic is `NCA3` or `NCA2` and the content type (byte 5) is `META`. The title ID is the u64 at `0x10` of that sector (absolute header offset `0x210`).
* Returns the minimum title ID across the qualifying NCAs, or `None`.
* Per the docstring, an NSZ/XCZ's `.ncz` keeps the NCA header uncompressed, which is why the same read works for both.

`xts_decrypt_sector(key, data, sector)` (line 105):

* Splits the 32-byte key into a data key and a tweak key (AES-128 each, ECB).
* Tweak = `AES_tweak(sector as 16-byte big-endian)`. Standard XTS uses little-endian here; Nintendo uses big-endian.
* For each 16-byte block: XOR with the tweak, ECB-decrypt, XOR again, then multiply the tweak by x in GF(2^128) (shift left; on overflow XOR with `0x87`).
* `from Crypto.Cipher import AES` sits inside this function (line 111).

### Base game normalization

`application_of(tid)` (line 183) looks at the low 12 bits:

| `tid & 0xFFF` | Interpreted as | Result                    |
|---------------|----------------|---------------------------|
| `0x000`       | Base game      | `tid`                     |
| `0x800`       | Update         | `tid & ~0xFFF`            |
| anything else | DLC            | `(tid & ~0xFFF) - 0x1000` |

### Output and exit

* An empty `apps` set exits with `no package with a readable title ID in …` (line 259).
* More than one ID writes `more than one game in the folder:` to stderr (line 262).
* Every ID is printed to stdout, sorted, as `{app:016X}`: 16 uppercase hex digits, one per line.

| Outcome                                                                 | Exit code | Stream                     |
|-------------------------------------------------------------------------|-----------|----------------------------|
| At least one ID printed                                                 | 0         | stdout (+ stderr warnings) |
| `--version` / `--help`                                                  | 0         | stdout                     |
| Bad CLI arguments                                                       | 2         | stderr (`argparse`)        |
| Not a folder, missing keys file, no IDs, keys file without `header_key` | 1         | stderr (`sys.exit(str)`)   |
| Uncaught exception (see risks)                                          | 1         | stderr traceback           |

## File and module responsibilities

| File                          | Responsibility                                                                                       |
|-------------------------------|------------------------------------------------------------------------------------------------------|
| `pyproject.toml`              | Metadata, the one runtime dependency, console script, hatchling wheel/sdist targets, dynamic version |
| `src/nx_title_id/__init__.py` | The whole implementation: container parsing, key loading, XTS, resolution, CLI                       |
| `src/nx_title_id/__main__.py` | `python -m` shim                                                                                     |
| `README.md`                   | User-facing usage, resolution order, exit status, dev commands                                       |
| `uv.lock`                     | Locked dependency versions for `uv sync`                                                             |

Function inventory in `__init__.py`:

| Function             | Line | Reads from disk                | Pure | Can raise                                  |
|----------------------|------|--------------------------------|------|--------------------------------------------|
| `partition`          | 35   | Header + entries + strings     | No   | `MalformedPackageError`                    |
| `package_files`      | 61   | `0x140` bytes, then partitions | No   | Same as `partition`                        |
| `is_package`         | 83   | `0x104` bytes                  | No   | `OSError`                                  |
| `read_header_key`    | 91   | The whole `prod.keys`          | No   | `ValueError` (no key), `OSError`           |
| `xts_decrypt_sector` | 105  | None                           | Yes  | `ImportError` if `pycryptodome` is missing |
| `meta_title_of`      | 131  | `0x200` bytes per NCA/NCZ      | No   | Same as `package_files`                    |
| `title_of`           | 162  | Via `package_files`            | No   | Same as above                              |
| `application_of`     | 183  | None                           | Yes  | None                                       |
| `main`               | 198  | Folder listing, `stat`         | No   | Exits through `sys.exit`                   |

## Decision points and side effects

Decision points:

* **Keys source** (line 219): explicit `-k` wins, then the default path if it exists, otherwise no meta fallback.
* **Explicit keys must exist** (line 221): `-k missing.keys` fails even if no package would need it.
* **Package filter** (lines 234–238): extension, then `._`/empty, then magic bytes.
* **Resolution order** (lines 171–180): ticket, then tag, then meta NCA.
* **Container type** (lines 70–80): PFS0 or card header, otherwise nothing.
* **Meta NCA selection** (lines 151–157): valid magic, `META` content type, lowest ID.
* **Base mapping** (lines 187–195): the low-12-bit rules above.
* **Error policy:** a `MalformedPackageError` or an unresolved package is skipped with a stderr message. A `ValueError` (a `prod.keys` without `header_key`) is fatal for the run.

Side effects:

* **Reads only.** Nothing is written to disk.
* Each package is opened two or three times (`is_package`, `title_of`, `meta_title_of`). Each open reads only headers.
* `prod.keys` is read at most once per run.
* stdout carries title IDs only. Everything else goes to stderr, so the output is safe to pipe.
* Key material stays in memory and never appears in output or error messages. The `ValueError` message names the keys path, not its contents.

## Risks, gaps, and follow-up inspections

### Resolved issues

* **Malformed packages (fixed).** An unterminated name or a truncated entry table, string table, or card header used to stop the run (`subsection not found`) or crash with a `struct.error` traceback. `partition()` and `package_files()` now raise `MalformedPackageError`, which isn't a `ValueError`, and `main()` skips that one package with a message. Covered by `tests/test_containers.py` and `tests/test_cli.py`.
* **Tests.** `uv run tryke test` runs the tests in `tests/` against synthetic packages, plus real base-game NSPs (`tests/<title ID>.nsp`, gitignored) when they're present. The meta NCA path is checked against those NSPs too when `~/.switch/prod.keys` exists. NCA headers are encrypted with `cryptography`'s AES-XTS, so `xts_decrypt_sector()` is checked against an independent implementation. `.github/workflows/test.yml` wasn't reviewed and may not run them yet.

### Likely or unverified issues

* **DLC index `0x800` would be read as an update.** `application_of()` tests `low == 0x800` before the DLC branch, so `base + 0x1000 + 0x800` maps to `base + 0x1000` instead of `base` (confirmed by calling the function). I haven't checked whether real DLC uses that index.
* **Large declared counts on corrupt files.** `f.read(count * entry_size)` is bounded by file size but not by a sanity limit. A corrupt multi-gigabyte package could make it read a large block into memory.
* **A bad `prod.keys` is fatal mid-run.** A keys file without `header_key` stops the run the first time a package needs it, and title IDs already resolved for other packages are discarded.
* **Unreadable files.** An `OSError` (for example permission denied) from `is_package()` or `read_header_key()` isn't caught and surfaces as a traceback.
* **Multi-game folders exit 0.** The warning goes to stderr, so scripts that only check the exit code can't tell "one game" from "several".

### Documented limitations

From `README.md`: XCIs with the 0x1000-byte key area at the start, and NCA0 headers from very early titles, aren't supported. Cartridge images rarely carry tickets, so they usually need a filename tag or keys.

### Code quality gaps

* Several parameters and returns are untyped (`path`, `header_key`, `tid`, the return values of `partition`/`package_files`/`title_of`), and `Any` is used for lists and the `apps` set. Pyrefly reports 0 errors only under its `basic` preset (no `pyrefly.toml`).
* The deferred `Crypto` import conflicts with the project's "imports are always global" convention. It's deliberate (see the next section), but it should be called out as a documented exception.
* Each package is opened up to three times. That's harmless for header-only reads, but it could be folded into one open.

## Design rationale

* **Cheapest reliable source first.** Ticket names and filename tags need no keys and no crypto. The meta NCA is the last resort because it needs user-supplied `prod.keys` and decryption.
* **Ticket over tag.** The ticket comes from the package's own contents, and filenames are routinely renamed or mistagged (line 171, `README.md`).
* **Lazy, cached keys and a lazy crypto import.** Most packages never need keys, so the tool doesn't touch `prod.keys` or load `pycryptodome` unless one does. That keeps runs fast, keeps key exposure to a minimum, and means a missing or broken keys file only matters when it's actually needed.
* **Hand-rolled XTS on ECB.** `pycryptodome` has no XTS mode, and Nintendo's NCA headers use a big-endian sector tweak, which a stock XTS implementation wouldn't match anyway (docstring, line 105).
* **Header-only reads.** Only partition tables and one 0x200-byte sector per NCA are read, so a folder of multi-gigabyte packages scans quickly, and NSZ/XCZ work without decompression because `.ncz` keeps the NCA header as-is.
* **Lowest meta ID plus base normalization.** Packages and folders often mix a base game with updates and DLC. Taking the lowest meta ID and folding every ID to its base gives the one answer a user wants per folder.
* **Magic-byte gate.** Checking `PFS0`/`HEAD` before trusting the filename stops a stray file with a package extension and a tag from producing a bogus ID (comment on line 238).
* **Clean stdout.** Only title IDs go to stdout and diagnostics go to stderr, so `$(nx-title-id dir)` works in shell scripts.
* **Single module, single dependency.** The whole tool is about 265 lines in one file, with one optional-at-runtime dependency. That keeps `uvx nx-title-id` installs small and fast.
