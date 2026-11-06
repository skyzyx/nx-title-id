# Deep Architecture Audit

A detailed walkthrough of `nx-title-id`: how it starts, what each piece does, where it makes decisions and has side effects, what can go wrong, and why it's built this way. For the short version, see [the quick flow summary](quickstart.md).

## Scope and sources

* **Code audited:** `src/nx_title_id/__init__.py` (the whole implementation, 1144 lines), `src/nx_title_id/__main__.py`, `tests/builders.py`, `tests/test_*.py`, `pyproject.toml`, `README.md`, `CHANGELOG.md`, and the `build-id` spec (`.kiro/specs/build-id/`).

* **Line references** point at `src/nx_title_id/__init__.py` unless noted otherwise. They were re-derived from the current file; if the module changes they go stale.

* **Format facts** (PFS0, HFS0, XCI card header, NCA header layout, ticket layout, Nintendo's XTS tweak, the NCA AES-CTR counter) come from the module's docstrings and comments, which cite switchbrew ("NCA", "Ticket") and hactool/libhac. They were not re-checked against those sources for this audit. What _was_ checked is the behavior against real retail dumps, described under [Verification](#verification).

* **Runtime behavior** marked "reproduced" was confirmed by running the test suite (`uv run tryke test`: 121 tests passed, including the gated tests, because the three real NSPs, `tests/ff.xci`, and a real `~/.switch/prod.keys` were present on the audit machine). Everything else comes from reading the code.

## Entry points

| Entry point                  | Defined in                           | Resolves to                                                   |
|------------------------------|--------------------------------------|---------------------------------------------------------------|
| `nx-title-id` console script | `pyproject.toml` `[project.scripts]` | `nx_title_id:main`                                            |
| `python -m nx_title_id`      | `src/nx_title_id/__main__.py`        | `from nx_title_id import main; main()`                        |
| `uvx nx-title-id`            | PyPI distribution `nx-title-id`      | The console script with the same name                         |
| Library call                 | `main(argv=None)`, line 868          | `argparse` parses `argv`, or `sys.argv[1:]` when it is `None` |

Packaging, from `pyproject.toml`:

* Build backend is `hatchling`. The wheel ships `src/nx_title_id`, and the sdist is limited to `src/`, `README.md`, `LICENSE`, and `CHANGELOG.md`.

* The version is dynamic, read from `__version__ = '1.0.0'` (line 34) by `[tool.hatch.version]`.

* `requires-python = ">=3.14"`. The only runtime dependency is `pycryptodome>=4.2,<5`.

## CLI startup and initialization flow

Importing the module (before `main()` runs) defines constants, one table, and one exception class, and does nothing else. There is no I/O at import time, and `pycryptodome` is not imported at import time (it is imported inside three functions, see [lazy imports](#lazy-imports)).

### Constants

| Name                  | Line | Value / purpose                                                                                          |
|-----------------------|------|----------------------------------------------------------------------------------------------------------|
| `__version__`         | 34   | `'1.0.0'`, single source of the package version                                                          |
| `EXTENSIONS`          | 36   | `('.nsp', '.nsz', '.xci', '.xcz')`, matched case-insensitively                                           |
| `DEFAULT_KEYS`        | 37   | `~/.switch/prod.keys`, resolved from `Path.home()` at import time                                        |
| `META`                | 38   | `1`, the NCA content type for meta NCAs                                                                  |
| `NSO_MAGIC`           | 44   | `b'NSO0'`, a mismatch means the walk never reached the main NSO                                          |
| `MODULE_ID_AT`        | 45   | `0x40`, where the ModuleId sits in the NSO header                                                        |
| `MODULE_ID_SIZE`      | 46   | `0x20`, the ModuleId span that is read (32 bytes)                                                        |
| `BUILD_ID_SIZE`       | 47   | `8`, how many leading ModuleId bytes are shown as the Build ID                                           |
| `PROGRAM`             | 48   | `0`, the NCA content type of the program NCA (the only one with the exefs)                               |
| `HEADER_REGION`       | 49   | `0xC00`, the NCA header: six 0x200-byte XTS sectors                                                      |
| `FS_ENTRY_AT`         | 50   | `0x240`, the FsEntry array (16 bytes each, four slots)                                                   |
| `FS_HEADER_AT`        | 51   | `0x400`, the FsHeader array (0x200 bytes each, four slots)                                               |
| `KEY_AREA_AT`         | 52   | `0x300`, the encrypted key area (4 x 0x10)                                                               |
| `UPDATE_FLAG`         | 53   | `0x800`, an update's title ID is base + this                                                             |
| `RIGHTS_ID_AT`        | 54   | `0x230`, the 16-byte RightsId; non-zero marks a titlekey NCA                                             |
| `TICKET_BODY_AT`      | 61   | `dict[int, int]`: signature type to body offset (seven types, `0x10000` to `0x10006`)                    |
| `TICKET_KEY_BLOCK_AT` | 70   | `0x40`, body offset of the 0x100-byte title key block                                                    |
| `TICKET_KEY_TYPE_AT`  | 71   | `0x141`, body offset of the title key type (0 common, 1 personalized)                                    |
| `TICKET_READ_SIZE`    | 72   | `0x500`, the fixed window read for a ticket (largest body offset 0x240 + 0x2C0-byte body)                |
| `META_VERSION_AT`     | 81   | `0x18`, where `update_version_of` reads a u32 version in the decrypted sector 1 (a synthetic convention) |
| `MAX_ENTRIES`         | 89   | `0x10000`, the sanity cap on a partition's declared entry count                                          |
| `MAX_STRINGS_SIZE`    | 90   | `0x100000` (1 MiB), the sanity cap on a partition's string table                                         |

`MalformedPackageError` (line 93) is deliberately not a `ValueError`, so `main()` can tell one bad package (skip it) from a bad keys file (a problem with the run, signalled with `ValueError`).

`META_VERSION_AT` is not a real CNMT layout. The module comment (lines 74-81) says it is a test/synthetic convention shared with the builder's `meta_nca`, and that a zero u32 means "no readable version". On a real package the u32 at `0x18` of the decrypted sector 1 is not a version the code can rely on, so real update ordering falls back to the sorted-path tie-break (see [Newest-update rule](#newest-update-rule)).

### `main()` startup

In order:

1. **Argument parsing** (lines 875-897): positional `folder` (`Path`), optional `-k/--keys` (`Path`), optional `-b/--build-id` (`store_true`, `dest='build_id'`), and `-V/--version`, which prints `nx-title-id 1.0.0` and exits 0. Bad arguments get `argparse`'s exit code 2.

2. **Folder validation** (line 899): if `folder` is not a directory, exit with `not a folder: …`.

3. **Keys resolution** (line 905): `args.keys`, else `DEFAULT_KEYS` if it is a file, else `None`.

4. **Keys validation** (line 909): an explicit `-k` path that is not a file exits with `no such keys file: …`. Only existence is checked here.

5. **Lazy `header_key()` closure** (lines 912-950): reads `prod.keys` through `read_header_key()` on first call and caches the result in a one-element `list[bytes | None]`. A missing `header_key` warns once (`warning: …; skipping packages that need it`) and caches `None`; an `OSError` on the keys file is fatal (`cannot read keys file …`).

6. **`-b` precondition** (lines 954-965), only with `-b`; see [the Build-ID flow](#build-id-flow-b).

7. **Lazy `key_area_key()` and `titlekek()` accessors** (lines 967-1011): each parses `prod.keys` on first call (`read_key_area_keys()`, `read_titlekeks()`), caches the parsed dict, and returns `dict.get(generation)`, so a missing generation is `None`, not an error. An `OSError` is fatal with the same one-line message. Each accessor has an unreachable `if keys is None` guard that exists only to narrow the type.

## Command-specific flows

There is one command. These are its stages.

### Folder scan

`main()` loops over `sorted(p for p in args.folder.iterdir() if p.suffix.lower() in EXTENSIONS and p.is_file())` (line 1018).

* Non-recursive. Subfolders are ignored.
* Sorted by path, so output and stderr messages are deterministic.
* `is_file()` follows symlinks.

Per file:

1. Skip names starting with `._` (macOS AppleDouble files), silently (line 1021).

2. One `try` wraps the per-package work (lines 1028-1056): `pkg.stat()` (zero-byte files are skipped silently), `is_package()`, and `title_ids_of()`. A `MalformedPackageError` (line 1048) or an `OSError` (line 1054) from any of them skips just that file with a `skipping <name>: …` stderr message and continues.

3. `is_package()` (line 198) reads the first `0x104` bytes and accepts `PFS0` at offset 0 or `HEAD` at offset `0x100`. Anything else is skipped with `skipping <name>: neither a PFS0 (NSP / NSZ) nor a cartridge image (XCI / XCZ)`.

4. `title_ids_of()` returns the set of title IDs the package declares (see below).

5. An empty set is skipped with a stderr message whose wording depends on whether keys are available and whether the keys file had a `header_key` (lines 1061-1077).

6. Otherwise every ID is folded through `application_of()` into the `apps` set (line 1081), and `(pkg, tids)` is appended to `scanned` (line 1082), which `-b` uses to find each game's packages.

### Package container parsing

`package_files(f)` (line 164) reads `0x140` bytes from offset 0:

* **NSP/NSZ:** `PFS0` at 0 means `partition(f, 0, b'PFS0', 0x18)`.

* **XCI/XCZ:** `HEAD` at `0x100` means read the root HFS0 offset (u64) at `0x130`, parse the root `HFS0` (entry size `0x40`), find the entry named `secure`, and parse that `HFS0` too. A card header shorter than `0x138` bytes raises `MalformedPackageError` (line 185).

* Anything else returns `[]`.

`partition(f, at, magic, entry_size)` (line 102):

* Reads a 16-byte header: magic, entry count (u32), string table size (u32), reserved.

* **Sanity guards (before any further read):** an entry count above `MAX_ENTRIES` (line 124) or a string table above `MAX_STRINGS_SIZE` (line 129) raises `MalformedPackageError` naming the declared size and the limit.

* Reads `count * entry_size` bytes of entries, then the string table; a short read raises `MalformedPackageError('truncated …')` (line 139).

* Each entry starts with data offset (u64), size (u64), name offset (u32). Names are NUL-terminated and decoded with `errors='replace'`; an unterminated name raises `MalformedPackageError` (line 154).

* Returns `[(name, absolute_offset)]`, where data offsets are relative to the end of the header.

* Returns `[]` on a short header or the wrong magic (line 116).

The same `partition()` also parses the exefs PFS0 under `-b`, over an in-memory `BufferedReader(BytesIO(...))` (line 657).

### Title ID resolution

`title_ids_of(path, header_key)` (line 808) returns a `set[int]` of every title ID the package declares, from the first source that yields anything:

1. **Tickets.** Every entry matching `[0-9A-Fa-f]{32}\.tik` (line 824); the first 16 hex digits of each rights ID is a title ID. All of them, so a multi-title cartridge contributes every ticket. If any ticket is found, the function returns here, before the tag and the meta NCA.

2. **Filename tag.** The first `[16 hex digits]` match in `path.name` (line 833), case-insensitive, as a one-element set.

3. **Meta NCAs.** Only when a `header_key` callable was passed and returns a key (lines 840-846). It calls `meta_title_of(path, key)`. The callable returns `None` once the fallback is disabled, in which case the result is an empty set.

`meta_title_of(path, key)` (line 371):

* For every entry ending in `.nca` or `.ncz`, reads the second 0x200-byte sector of its header (`at + 0x200`, line 391).

* Decrypts it with `xts_decrypt_sector(key, sector, 1)`.

* Keeps it if the magic is `NCA3` or `NCA2` and the content type (byte 5) is `META` (line 403). The title ID is the u64 at `0x10` of that sector (absolute header offset `0x210`).

* Returns the set of every qualifying meta-NCA title ID, or an empty set.

* Per the docstring, an NSZ/XCZ's compressed `.ncz` keeps this header in the clear, so the same read works for it.

`xts_decrypt_sector(key, data, sector)` (line 337):

* Splits the 32-byte key into a data key and a tweak key (AES-128 each, ECB).

* Tweak = `AES_tweak(sector as 16-byte big-endian)`. Standard XTS uses little-endian here; Nintendo uses big-endian.

* For each 16-byte block: XOR with the tweak, ECB-decrypt, XOR again, then multiply the tweak by x in GF(2^128) (shift left; on overflow XOR with `0x87`).

### Base game normalization

`application_of(tid)` (line 849) looks at the low 12 bits:

| `tid & 0xFFF` | Interpreted as | Result                    |
|---------------|----------------|---------------------------|
| `0x000`       | Base game      | `tid`                     |
| `0x800`       | Update         | `tid & ~0xFFF`            |
| anything else | DLC            | `(tid & ~0xFFF) - 0x1000` |

Real DLC indexes run 1 to 2000 (0x7D0), so a low-12-bit value of `0x800` is always an update, never a DLC (docstring of `application_of`, line 849).

### Build-ID flow (`-b`)

`-b` is opt-in. Without it, `main()` prints the bare title ID (line 1093) and none of the code below runs.

**Precondition (lines 954-965).** Under `-b` a usable `header_key` is required up front, so the run fails before any package output:

* No keys source (no `-k`, no default `prod.keys`): `sys.exit('-b/--build-id needs prod.keys; pass -k or install one at <DEFAULT_KEYS>')`, exit 1.

* A keys file with no `header_key`: `header_key()` first prints its `warning: …` line and caches `None`, then `sys.exit('-b/--build-id needs a header_key in <keys>')`, exit 1.

**Lazy keys.** `header_key()`, `key_area_key(generation)` and `titlekek(generation)` are closures in `main()`. Each reads `prod.keys` at most once and only when first needed, so a run that never reaches a program NCA never parses the key-area or titlekek lines. A missing `key_area_key_application_XX` or `titlekek_XX` is `None` and becomes a per-game skip, never a fatal error.

**Per base game (lines 1090-1126).** For each base game in sorted order, `build_id_of(scanned, app, hk, key_area_key, titlekek)` runs inside a `try`. `MalformedPackageError` and `OSError` print `skipping game <TitleID>: …` and continue; a `None` result prints `skipping game <TitleID>: no Build ID (no readable program NCA, ticket, or key in <keys>)` and continues (lines 1106-1124). Otherwise the line is `<TitleID> <BuildID>` (line 1126).

**Locating the program NCA.** `module_id_of(path, header_key, key_area_key, titlekek)` (line 590) iterates `package_files()`. For each entry ending in `.nca` or `.ncz` it reads `HEADER_REGION` (0xC00) bytes and calls `decrypt_nca_header()` (line 412), which XTS-decrypts six 0x200 sectors numbered 0 to 5 by reusing `xts_decrypt_sector`. It skips an NCA whose decrypted magic at `0x200` is not `NCA3`/`NCA2`, and one whose content type at `0x205` is not `PROGRAM`. The first program NCA wins.

**Decrypting the exefs section.** `exefs_section(f, nca_at, header, key_area_key, title_key)` (line 520) walks the four section slots in the decrypted header and uses the first PartitionFS section. The layout it reads (switchbrew "NCA", per the code comments; confirmed in practice by the real fixtures):

| Field                   | Where                                     | Use                                                           |
|-------------------------|-------------------------------------------|---------------------------------------------------------------|
| FsEntry StartOffset     | u32 at `FS_ENTRY_AT + i * 0x10`           | Section start, in 0x200-byte blocks from the **NCA start**    |
| FsHeader FsType         | u8 at `FS_HEADER_AT + i * 0x200 + 0x2`    | `1` = PartitionFS, the only type that is accepted             |
| FsHeader HashType       | u8 at FsHeader `+ 0x3`                    | Not read (real NCAs set 2 or 3)                               |
| FsHeader EncryptionType | u8 at FsHeader `+ 0x4`                    | `1` = None, `3` = AesCtr, anything else is unsupported        |
| PfsOffset, PfsSize      | two u64 at FsHeader `+ 0x40` and `+ 0x48` | The PFS0 sits at `StartOffset * 0x200 + PfsOffset` in the NCA |
| CTR upper counter       | u64 (little-endian) at FsHeader `+ 0x140` | Upper half of the AES-CTR counter                             |

Per `EncryptionType`:

* **`1` (None).** Returns `PfsSize` bytes at `nca_at + data_at` unchanged. This is the synthetic CI path: the test builder writes the exefs in the clear so the whole chain is testable with no retail assets.

* **`3` (AesCtr).** Reads from the 0x10-aligned offset below `data_at`, decrypts with `_ctr_decrypt_section()` (line 450), and drops the lead-in. The counter is `struct.pack('>QQ', upper_counter, nca_offset >> 4)`: the upper u64 stored little-endian at FsHeader `+ 0x140` but packed big-endian, then the **NCA-relative** 16-byte block index (line 513). Counting from the NCA start, not the package, is what makes a real section decrypt to PFS0 (comment, lines 507-511).

* **Anything else, or no PartitionFS section.** Returns `None`: that package is skipped.

**Section key.** `_ctr_decrypt_section` branches on `RightsId` (the 16 bytes at `0x230`):

* **Zero RightsId (key-area crypto).** The key generation comes from `_key_generation()` (line 432): the greater of the bytes at `0x206` and `0x220`, with 0 and 1 both meaning generation 0, otherwise value - 1. `key_area_key(generation)` supplies `key_area_key_application_XX`; the 4 x 0x10 EncryptedKeyArea at `KEY_AREA_AT` is AES-128-ECB-decrypted with it, and row 2 (bytes `0x20:0x30`) is the section key. A missing key-area key returns `None`.

* **Non-zero RightsId (titlekey crypto).** The already-decrypted title key is the CTR key directly, with no key-area step. If `title_key` is `None`, the section is un-keyable and the package is skipped.

**Titlekey (rights-ID) flow.** In `module_id_of`, a non-zero RightsId triggers (lines 635-646):

1. `ticket_of(f, rights_id)` (line 319) looks for an entry named `<rights ID as 32 hex digits>.tik`, case-insensitive, in the **same package**, and reads `TICKET_READ_SIZE` (0x500) bytes.

2. `title_key_of(ticket, rights_id, titlekek)` (line 271) reads the u32 signature type at offset 0 and looks up the body offset in `TICKET_BODY_AT`. The title key type is at body `+ 0x141`; only type `0` (common) is accepted. The key block is at body `+ 0x40`, and its first 16 bytes are AES-128-ECB-decrypted with `titlekek_XX`. The generation is derived from `rights_id[15]` (the master key revision): 0 and 1 both mean generation 0, otherwise value - 1.

3. It returns `None`, which becomes a per-game skip, for: a ticket shorter than 4 bytes, an unrecognised signature type, a truncated body, a personalized ticket (type `1`), a missing `titlekek` callable, or a `titlekek_XX` absent from `prod.keys`. A missing ticket also leaves `title_key` unset and skips the game.

**Reading the Build ID.** The decrypted exefs is parsed as a PFS0; the entry named `main` is read for `MODULE_ID_AT + MODULE_ID_SIZE` (0x60) bytes. A short read or a first four bytes other than `NSO0` returns `None`; a missing `main` also returns `None`. Otherwise `module_id_of` returns the 32 bytes at `0x40` (line 673). It keeps the full field; trimming happens at the print site.

### Newest-update rule

`build_id_of(packages, base, header_key, key_area_key, titlekek)` (line 731) classifies each scanned `(path, title_ids)` pair (line 758): a package whose set contains `base` is a base package, one containing `base + UPDATE_FLAG` is an update package, and anything else (DLC) is never a source. A package can land in both lists.

* With update packages, `update_version_of()` (line 683) is called for each in sorted-path order. The greatest readable version wins; a strict `>` keeps the least path on ties. If no update has a readable version, the least sorted update path is used, so any update still beats the base.

* With no update package, the least sorted base path is used.

* With neither, it returns `None` (for example a ticket- or tag-only game).

`update_version_of` reads a u32 at `META_VERSION_AT` of each meta NCA's decrypted sector 1 and returns the greatest non-zero one, or `None`. As noted above, that offset is a synthetic convention (the code comment says a real CNMT stores the version deeper in the meta NCA), so the "greatest readable version wins" behavior is exercised by the synthetic builders (Property 3), and on real dumps the practical rule is "any update beats the base, ties broken by sorted path". This was not checked against a real CNMT.

### Rendering

`render_build_id(module_id)` (line 799) returns `module_id[:BUILD_ID_SIZE].hex().upper()`: the first 8 bytes of the 32-byte ModuleId as 16 uppercase hex digits. This is the form nxdumptool and Atmosphere show as the Build ID (requirements doc; the real fixtures' file names carry exactly these 16 digits and the tests assert equality).

### Output and exit

* An empty `apps` set exits with `no package with a readable title ID in …` (line 1086).
* Under `-b`, if every game was skipped, `lines` is empty and the run exits with `no game with a readable Build ID in …` (line 1130).
* More than one printed line writes `more than one game in the folder:` to stderr first (line 1136).
* Every line is printed to stdout, sorted by title ID, as `{app:016X}` (default) or `{app:016X} {BuildID}` (`-b`).
* More than one printed line then exits `3` (line 1144).

The decision to warn and exit 3 is made on `len(lines)`, the games actually printed, so a game skipped under `-b` does not turn two games into a "several" result.

| Outcome                                                                         | Exit code | Stream                     |
|---------------------------------------------------------------------------------|-----------|----------------------------|
| One game printed                                                                | 0         | stdout (+ stderr warnings) |
| `--version` / `--help`                                                          | 0         | stdout                     |
| Bad CLI arguments                                                               | 2         | stderr (`argparse`)        |
| Not a folder, missing or unreadable keys file, no package with a readable ID    | 1         | stderr (`sys.exit(str)`)   |
| With `-b`: no `prod.keys`, no `header_key`, or no game with a readable Build ID | 1         | stderr (`sys.exit(str)`)   |
| More than one game printed (every line printed first)                           | 3         | stdout + stderr warning    |

## File and module responsibilities

| File                          | Responsibility                                                                                                         |
|-------------------------------|------------------------------------------------------------------------------------------------------------------------|
| `pyproject.toml`              | Metadata, the one runtime dependency, console script, hatchling wheel/sdist targets, dynamic version, Tryke `src` path |
| `src/nx_title_id/__init__.py` | The whole implementation: containers, key loading, XTS, ticket/titlekey, AES-CTR section reader, Build ID, CLI         |
| `src/nx_title_id/__main__.py` | `python -m` shim (3 lines)                                                                                             |
| `tests/builders.py`           | Synthetic package, NCA, ticket, and key-file builders, plus the real-fixture discovery helpers                         |
| `tests/test_containers.py`    | `partition`, `package_files`, `is_package`                                                                             |
| `tests/test_title.py`         | Key parsing, XTS, meta NCAs, resolution, `application_of`, and the Build-ID functions                                  |
| `tests/test_cli.py`           | End-to-end CLI in a subprocess                                                                                         |
| `README.md`                   | User-facing usage, resolution order, the `-b` Build ID section, exit status table, troubleshooting, tests              |
| `CHANGELOG.md`                | Keep a Changelog history; the robustness fixes and the `-b` flag are under `## Unreleased`                             |
| `uv.lock`                     | Locked dependency versions for `uv sync`                                                                               |

Function inventory in `__init__.py` (lines are `def` lines; "Imports Crypto" marks the three lazy imports):

| Function               | Line | Reads from disk                         | Pure | Returns                | Can raise                          |
|------------------------|------|-----------------------------------------|------|------------------------|------------------------------------|
| `partition`            | 102  | Header + entries + strings              | No   | `list[tuple[str,int]]` | `MalformedPackageError`            |
| `package_files`        | 164  | `0x140` bytes, then partitions          | No   | `list[tuple[str,int]]` | Same as `partition`                |
| `is_package`           | 198  | `0x104` bytes                           | No   | `bool`                 | `OSError`                          |
| `read_header_key`      | 214  | The whole `prod.keys`                   | No   | `bytes`                | `ValueError` (no key), `OSError`   |
| `read_key_area_keys`   | 233  | The whole `prod.keys`                   | No   | `dict[int, bytes]`     | `OSError`                          |
| `read_titlekeks`       | 254  | The whole `prod.keys`                   | No   | `dict[int, bytes]`     | `OSError`                          |
| `title_key_of`         | 271  | None                                    | Yes  | `bytes \| None`        | `ImportError` (imports Crypto)     |
| `ticket_of`            | 319  | `TICKET_READ_SIZE` bytes of one ticket  | No   | `bytes \| None`        | Same as `package_files`            |
| `xts_decrypt_sector`   | 337  | None                                    | Yes  | `bytes`                | `ImportError` (imports Crypto)     |
| `meta_title_of`        | 371  | `0x200` bytes per NCA/NCZ               | No   | `set[int]`             | Same as `package_files`            |
| `decrypt_nca_header`   | 412  | None                                    | Yes  | `bytes`                | `ImportError` (via XTS)            |
| `_key_generation`      | 432  | None                                    | Yes  | `int`                  | None                               |
| `_ctr_decrypt_section` | 450  | None                                    | Yes  | `bytes \| None`        | `ImportError` (imports Crypto)     |
| `exefs_section`        | 520  | The exefs section of one NCA            | No   | `bytes \| None`        | `OSError`                          |
| `module_id_of`         | 590  | `0xC00` per NCA, ticket, exefs          | No   | `bytes \| None`        | `MalformedPackageError`, `OSError` |
| `update_version_of`    | 683  | `0x200` bytes per NCA/NCZ               | No   | `int \| None`          | Same as `package_files`            |
| `build_id_of`          | 731  | Via `update_version_of`, `module_id_of` | No   | `bytes \| None`        | Same as `module_id_of`             |
| `render_build_id`      | 799  | None                                    | Yes  | `str`                  | None                               |
| `title_ids_of`         | 808  | Via `package_files`, `meta_title_of`    | No   | `set[int]`             | Same as above                      |
| `application_of`       | 849  | None                                    | Yes  | `int`                  | None                               |
| `main`                 | 868  | Folder listing, `stat`, `prod.keys`     | No   | `None`                 | Exits through `sys.exit`           |

`_key_generation` and `_ctr_decrypt_section` are private. The file's section-order convention puts private functions before public ones, but both currently sit after public functions in the file; this audit did not change that.

Every parameter, return, and notable local carries a specific type annotation; there is no `Any` in the module.

### Lazy imports

`from Crypto.Cipher import AES` appears exactly three times, each inside a function and each commented as a deliberate exception to the "imports are always global" convention:

* `title_key_of`, line 312, after all the early `None` returns.
* `xts_decrypt_sector`, line 348.
* `_ctr_decrypt_section`, line 475.

A default run that resolves every package by ticket or tag reaches none of them, so it never loads `pycryptodome` (from reading the code; `title_ids_of` returns before `meta_title_of`). Under `-b` the header decryption in `module_id_of` always reaches `xts_decrypt_sector`.

## Decision points and side effects

Decision points:

* **Keys source** (line 905): explicit `-k` wins, then the default path if it exists, otherwise no meta fallback.

* **Explicit keys must exist** (line 909): `-k missing.keys` fails even if no package would need it.

* **`-b` precondition** (lines 954-965): a usable `header_key` is mandatory, and its absence is a fatal setup error rather than warn-once-skip.

* **Package filter** (lines 1018-1041): extension, then `._`, then zero-byte, then magic bytes.

* **Per-package isolation** (lines 1028-1056): `stat`/`is_package`/`title_ids_of` share one `try`; a `MalformedPackageError` or `OSError` skips only that package.

* **Resolution order** (lines 824-846): tickets, then tag, then meta NCAs, each contributing a set.

* **Container type** (lines 164-195): PFS0 or card header, otherwise nothing.

* **Partition sanity guards** (lines 124-134): entry count and string table size caps before any body read.

* **Meta NCA selection** (lines 391-407): valid magic, `META` content type; every match is collected.

* **Base mapping** (lines 857-866): the low-12-bit rules above.

* **Program NCA selection** (lines 626-633): first NCA with valid magic and content type `PROGRAM`.

* **Section crypto** (lines 565-588): `EncryptionType` 1 reads as-is, 3 decrypts with AES-CTR, anything else skips.

* **Key source for the section** (lines 478-503): zero RightsId uses the key area, non-zero uses the ticket's titlekey.

* **Source package for a game** (lines 758-792): newest update, else base, never DLC.

* **Missing `header_key` is a warning without `-b`** (lines 939-942): the first package that needs it triggers one stderr warning, the key caches as `None`, and packages needing the fallback are skipped while the rest still print. Under `-b` the same condition is fatal.

* **Unreadable keys file is fatal** (lines 945-946): an `OSError` reading `prod.keys` exits `1` with a one-line message, not a traceback.

* **Several games exit 3** (line 1143): decided on the games actually printed.

Side effects:

* **Reads only.** Nothing is written to disk.

* **Without `-b`, header-only reads.** Each package is opened up to three times (`is_package`, `title_ids_of`, `meta_title_of`), and only partition tables and one 0x200-byte sector per NCA are read.

* **With `-b`, more than headers is read.** For each base game, `module_id_of` reads the 0xC00-byte header region of each NCA until it finds the program NCA, the matching ticket (0x500 bytes) for a titlekey NCA, and the exefs section (`PfsSize` bytes, rounded out to a 16-byte boundary). `update_version_of` re-reads a 0x200-byte sector per NCA of each update package. A multi-gigabyte package body is still never read in full. A package can be opened many more times than three under `-b`, once per `is_package`, `title_ids_of`, `update_version_of` and `module_id_of` call.

* **`prod.keys` is read at most once per accessor.** `header_key()`, `key_area_key()` and `titlekek()` each parse the file at most once (success or failure is cached for `header_key`; the other two cache the parsed dict), so a `-b` run can read the file up to three times.

* stdout carries title IDs (and Build IDs under `-b`) only. Everything else goes to stderr, so the output is safe to pipe.

* Key material stays in memory and never appears in output or error messages. Error messages name the keys path, not its contents. The titlekey and section key are likewise never printed.

## Risks, gaps, and follow-up inspections

### Resolved issues

These were raised by a prior audit and are fixed by the `audit-robustness-fixes` spec (see `CHANGELOG.md`, `## Unreleased`). All are covered by `tests/test_containers.py`, `tests/test_cli.py`, and `tests/test_title.py`.

* **Malformed packages (A, B).** An unterminated name, a truncated entry or string table, a truncated card header, or an oversized declared count or string table now raises `MalformedPackageError`, which `main()` skips with a `skipping <file>: …` message. The oversized-count path is capped by `MAX_ENTRIES` / `MAX_STRINGS_SIZE` before any large read.

* **Unreadable files (A).** An `OSError` (for example permission denied) from `stat`, `is_package()`, or `title_ids_of()` skips just that package with a stderr message instead of surfacing a traceback. An unreadable keys file exits `1` with a one-line error.

* **Missing `header_key` (D).** A keys file without `header_key` now warns once and skips only the packages that need the meta fallback; IDs already found from other packages are still printed.

* **Several games (E) and multi-title cartridges (G).** `title_ids_of()` and `meta_title_of()` return sets, so every base game a folder or a single cartridge carries is collected. More than one base game prints every ID and exits `3`.

* **Loose typing (F).** Every signature and notable local is annotated; `Any` is gone.

From the `build-id` spec (`.kiro/specs/build-id/`):

* **Titlekey (rights-ID) programs.** All three real retail fixtures use titlekey program NCAs, so the key-area path alone could not read them. The ticket and titlekek path (`read_titlekeks`, `ticket_of`, `title_key_of`) was added and is exercised by synthetic and real tests. The open question of whether the Build ID is the first 8 or all 32 ModuleId bytes was resolved as the first 8 (`render_build_id`).

* **Layout assumptions that only real fixtures exposed.** The comments in `exefs_section` and `_ctr_decrypt_section` record that FsType is at FsHeader `+ 0x2` (not `+ 0x3`, which is HashType), that FsEntry blocks are counted from the NCA start (not from the end of the header region), that the PFS0 sits at `PfsOffset` within the section, and that the CTR counter uses the NCA-relative block index. The spec's task 3.3 still describes the section start as `HEADER_REGION + StartOffset * 0x200`; the code is the authority and uses the NCA-relative form.

### Verification

`uv run tryke test` ran 121 tests across `tests/test_containers.py`, `tests/test_title.py` and `tests/test_cli.py`, all passing, on a machine with the real fixtures and keys. On a clone without them the gated tests skip.

**Synthetic builders** (`tests/builders.py`) cover: `nso`, `exefs`, `program_nca` (None encryption by default; AES-CTR via `section_key`, with a titlekey when `rights_id` is non-zero or via the key area when `key_area_key` is given), `meta_nca` (with an optional version), `rights_id_of`, `ticket` (any signature type and key type), `titlekey_package`, `write_titlekek_keys`, and `program_package`. The AES-CTR, AES-ECB, and XTS sides of the builders use the `cryptography` package, an independent reference for the production code's `pycryptodome` use.

**Seeded property tests** (`random.Random(0)` loops, no Hypothesis dependency), tagged `# Feature: build-id, Property N`:

* Property 1, ModuleId round-trip (`tests/test_title.py`, `module_id_round_trip`).
* Property 2, rendering is the first 8 bytes as uppercase hex (`hex_rendering`).
* Property 3, newest-update selection is monotonic in version (`newest_update_selection`).
* Property 4, a DLC is never the Build-ID source (`dlc_exclusion`).
* Property 5, default output is unchanged without `-b` (`tests/test_cli.py`, `default_output_unchanged_without_flag`).

**Unit and CLI tests** cover `decrypt_nca_header`, `exefs_section` (None path, offsets, absent section, unsupported encryption), `read_titlekeks`, `title_key_of` (signature types `0x10003` to `0x10006`, revision-to-generation mapping, personalized and truncated tickets), the titlekey round trip and its skips (no ticket, no titlekek, personalized), a key-area AES-CTR round trip with a counter check, the `-b` precondition errors, per-game skips, exit codes 0, 1, 2 and 3, and an assertion that no real key value appears in stdout or stderr.

**Real fixtures.** The three base-game NSPs in `tests/` are named `<TitleID>-<BuildID>.nsp` (for example `01009C100390E000-1816F8A3E4F09B33.nsp`, a gitignored file) and are discovered by `glob` in `builders.py` (`_real_nsp_path`), not by a fixed name. With real keys, `-b` run on each one prints a Build ID equal to the 16 digits after the hyphen (`real_build_id_matches_filename`), and the three together print three sorted lines and exit 3. The real meta NCA path, the real ticket layout, and the real multi-title cartridge (`tests/ff.xci`, exit 3 with keys) are checked the same way.

**What was verified against real data:** container layout, tickets, header decryption (XTS), the NCA section layout, titlekey generation and decryption, AES-CTR section decryption, the exefs PFS0, the NSO `ModuleId` offset, and the first-8-bytes rendering, all via the three retail titlekey fixtures. **What was taken only from switchbrew/hactool (as cited in code comments) and not verified against real data:** the key-area (zero RightsId) path, which is exercised only by a synthetic round trip against an independent AES reference; ticket body offsets for the signature types the fixtures do not use (the tests exercise four of the seven in `TICKET_BODY_AT`, `0x10003` to `0x10006`, with a builder table that is an independent copy of the production one, so `0x10000` to `0x10002` are untested); and the update-version offset (`META_VERSION_AT`, synthetic by design).

`.github/workflows/test.yml` was not reviewed for this audit and may not run the suite yet. Pyrefly and Ruff were not re-run for this audit, which changed documents only.

### Likely or unverified issues

* **DLC index `0x800` is read as an update (documented).** `application_of()` tests `low == 0x800` before the DLC branch, so `base + 0x1000 + 0x800` maps to `base + 0x1000` instead of `base`. Real DLC indexes are documented as 1 to 2000 (0x7D0), so this is expected rather than a defect.

* **Ticket-only detection limit.** Without keys, a cartridge is resolved by its tickets alone; a cartridge carrying no tickets (or fewer than it has games) may resolve to fewer IDs.

* **Update ordering on real dumps is unverified.** `update_version_of` reads a synthetic offset, so on a real package the value it returns is not a real update version; the actual rule that decides between two real updates is therefore partly the sorted-path tie-break. The fixtures carry one package per game, so multi-update selection was not checked against real data.

* **Only the first program NCA is read.** `module_id_of` stops at the first valid program NCA and uses only the first PartitionFS section; a package with several program NCAs (for example a multi-title cartridge) is read once.

* **The key-area path is untested on retail data.** Every real fixture is a titlekey NCA.

* **Many opens per package under `-b`.** The package file is opened repeatedly. This is harmless on a local disk; a single-open refactor was deliberately deferred.

* **The README's `-b` troubleshooting depends on message text.** If the `sys.exit` strings change, update the README's troubleshooting headings.

### Documented limitations

From `README.md`: XCIs with the `0x1000`-byte key area at the start, and `NCA0` headers from very early titles, are not supported. Cartridge images rarely carry tickets, so they usually need a filename tag or keys. Under `-b`, personalized tickets are unsupported.

## Design rationale

* **Cheapest reliable source first.** Ticket names and filename tags need no keys and no crypto. The meta NCA is the last resort because it needs user-supplied `prod.keys` and decryption.

* **Ticket over tag.** The ticket comes from the package's own contents, and filenames are routinely renamed or mistagged (comment in `title_ids_of()`, `README.md`).

* **Sets everywhere, folded at the end.** Returning every declared title ID as a set keeps the resolution path honest about multi-title packages; `application_of()` then folds to base games so a single game still prints a single line.

* **Lazy, cached keys and a lazy crypto import.** Most packages never need keys, so the tool does not touch `prod.keys` or load `pycryptodome` unless one does. That keeps runs fast, keeps key exposure to a minimum, and means a missing or broken keys file only matters when it is actually needed. Caching keeps warnings to once per run.

* **Fail soft per package, fail fast on setup.** One corrupt, oversized, or unreadable package should not sink a whole folder, so each package's work is isolated in a `try` and a failure becomes a skip message. Under `-b` the same softness applies per game. A condition that makes the whole run pointless (bad arguments, not a folder, no keys under `-b`) exits before any package output.

* **Sanity caps on declared sizes.** A corrupt header could otherwise ask the tool to read gigabytes into memory; `MAX_ENTRIES` and `MAX_STRINGS_SIZE` reject absurd declarations before any large read while staying far above any real package.

* **Hand-rolled XTS on ECB.** `pycryptodome` has no XTS mode, and Nintendo's NCA headers use a big-endian sector tweak, which a stock XTS implementation would not match anyway (docstring, line 337).

* **Why `-b` is opt-in.** The Build ID cannot come from a ticket name or a tag; it needs the program NCA decrypted down to `main`, which needs keys, more reads, and more CPU. Keeping it behind a flag leaves the default run cheap, keyless, and byte-for-byte unchanged (Property 5).

* **Why the first 8 bytes.** The ModuleId is 32 bytes, but the Build ID that nxdumptool and Atmosphere show is its first 8. On the real fixtures the 32-byte ModuleId is the 8 filename bytes, then 8 further non-zero bytes, then 16 zero bytes (design doc), and only the first 8 match the file name. So `module_id_of` returns the full field and `render_build_id` drops the rest at the single print site.

* **Why a synthetic None-encryption path.** Real retail exefs sections are AES-CTR-encrypted and need real keys, which a CI clone does not have. The builder writes the same NCA layout with `EncryptionType` 1 (None), so the header decrypt, section walk, PFS0 parse, NSO read, newest-update rule, and CLI behavior are all testable with no assets. The real AES-CTR path has its own synthetic round trip against an independent reference, plus the gated real-fixture tests.

* **Why layout bugs only surfaced on real fixtures.** The synthetic builder and the reader were written from the same understanding of the format, so a shared wrong assumption passes every synthetic test. The FsType offset, the NCA-relative block offsets, and the PfsOffset were corrected only when real NCAs failed to decrypt to a PFS0. This is why the gated real-fixture tests exist and why the code comments say what they were checked against.

* **Why the titlekey path was in scope.** All three real fixtures are titlekey NCAs, so without it no real game could be read. The ticket lookup is inside the same package, which is already the unit the tool trusts for a title ID.

* **Base normalization for one answer per game.** Packages and folders often mix a base game with updates and DLC. Folding every ID to its base gives the one answer a user wants per game. The Build ID follows the same fold, using the newest update (never DLC) because an update replaces the base executable.

* **Magic-byte gate.** Checking `PFS0`/`HEAD` before trusting the filename stops a stray file with a package extension and a tag from producing a bogus ID.

* **Clean stdout and a meaningful exit code.** Only IDs go to stdout and diagnostics go to stderr, so `$(nx-title-id dir)` works in shell scripts; the `0`/`3` split lets a script tell one game from several without parsing `stderr`, and under `-b` it is decided on the games actually printed.

* **Single module, single dependency.** The whole tool is one file with one optional-at-runtime dependency. That keeps `uvx nx-title-id` installs small and fast.
