# Quick Flow Summary

A short tour of how `nx-title-id` turns a folder of Nintendo Switch packages into one base-game title ID per game. For the full audit, see [the comprehensive architecture audit](comprehensive.md).

Line references point at `src/nx_title_id/__init__.py` unless noted otherwise. Binary-format details (`PFS0`, `HFS0`, `NCA`, `XTS`) come from the docstrings in that file and were not checked against an external specification.

## Entrypoint

| How it's invoked                 | What runs                                                            |
|----------------------------------|----------------------------------------------------------------------|
| `uvx nx-title-id <folder>`       | Console script `nx-title-id = "nx_title_id:main"` (`pyproject.toml`) |
| `uv run nx-title-id <folder>`    | Same console script, from the project's virtual environment          |
| `python -m nx_title_id <folder>` | `src/nx_title_id/__main__.py`, which imports and calls `main()`      |
| `nx_title_id.main([...])`        | `main(argv: list[str]`                                               |

## Primary flow

```text
main(argv)
  ├─ parse: folder, -k/--keys, -V/--version
  ├─ validate folder; resolve keys (-k, else ~/.switch/prod.keys if present)
  ├─ for each file in folder (sorted, non-recursive, .nsp/.nsz/.xci/.xcz):
  │    ├─ skip "._*" files
  │    ├─ (in one try) stat + is_package() + title_ids_of(); OSError → skip this file
  │    ├─ is_package()        magic check: PFS0 at 0x0, or HEAD at 0x100
  │    ├─ title_ids_of()      → set[int]
  │    │    1. tickets        every <32 hex>.tik → first 16 hex digits
  │    │    2. file name tag  [16 hex digits]
  │    │    3. meta NCAs      decrypt NCA headers with header_key (lazy, cached)
  │    └─ application_of()    each update/DLC ID → its base game ID, folded into apps
  └─ print each distinct base ID (sorted, 16 uppercase hex digits); exit 3 if more than one
```

1. `main()` parses arguments and exits early if the folder isn't a directory or an explicit keys path doesn't exist (lines 250–274).

2. It builds a `header_key()` closure that reads `prod.keys` once, only when a package actually needs the meta NCA fallback (lines 277–305).

3. Each candidate file is checked by magic bytes, then resolved by `title_ids_of()` (line 203), which returns the set of every title ID the package declares, trying the cheapest, most reliable source first.

4. `application_of()` (line 233) folds updates (`…800`) and DLC (`base + 0x1000 + index`) into their base game, and every result goes into a set.

5. If the set is empty it exits `1`. If it holds exactly one base game it prints that line and exits `0`. If it holds more than one, a warning goes to stderr, every ID is printed to stdout, and it exits `3`.

## Module roles

| File / function        | Role                                                                                    |
|------------------------|-----------------------------------------------------------------------------------------|
| `pyproject.toml`       | Hatchling build, `nx-title-id` console script, version read from `__init__.py`          |
| `__main__.py`          | `python -m nx_title_id` support                                                         |
| `partition()`          | Parses a PFS0 or HFS0 header into `[(name, absolute offset)]`, with size sanity guards  |
| `package_files()`      | Lists the files in an NSP/NSZ (PFS0) or an XCI/XCZ's `secure` HFS0 partition            |
| `is_package()`         | Cheap magic-byte check so mislabeled files are skipped                                  |
| `read_header_key()`    | Pulls `header_key` out of `prod.keys`                                                   |
| `xts_decrypt_sector()` | AES-128-XTS on one 0x200 sector, with Nintendo's big-endian tweak, built on AES-ECB     |
| `meta_title_of()`      | Decrypts each NCA/NCZ header and returns the set of meta-NCA title IDs                  |
| `title_ids_of()`       | Ticket → filename tag → meta NCA resolution order, returning every declared ID as a set |
| `application_of()`     | Maps a base, update, or DLC title ID to the base game's ID                              |
| `main()`               | CLI, folder scan, skip/exit policy, output                                              |

## Design decisions

* **Single module, standard library first.** Steps 1 and 2 need only the standard library. `pycryptodome` is a declared dependency, but it's imported inside `xts_decrypt_sector()` (line 153), so it only loads when a package needs the meta NCA. The deferred import carries a comment marking it as a deliberate exception to the "imports are always global" convention.

* **Ticket over filename.** The ticket name comes from the package contents, while filename tags are user-editable and can be wrong (comment in `title_ids_of()`, `README.md`).

* **Keys are lazy and never printed.** `prod.keys` is read at most once, only when needed, and key values never appear in output (module docstring, `README.md`).

* **Hand-rolled XTS.** `pycryptodome` has no XTS mode, and Nintendo uses a big-endian sector tweak, so XTS is built on ECB (docstring, line 144).

* **Sets, not a single winner.** `title_ids_of()` and `meta_title_of()` return every title ID a package declares, so a multi-title cartridge contributes every base game it carries. `application_of()` folds them, so one game still reports one line.

* **One base game per line.** The tool reduces everything to base IDs; one game prints one line and exits `0`, several games print each line and exit `3`.

* **Header-only reads.** Only partition headers and one 0x200-byte NCA sector per file are read, so multi-gigabyte packages are cheap to scan.

* **Fail soft per package.** A malformed package, an unreadable file, or a package that needs an absent `header_key` is skipped with a stderr message; the remaining packages still print.

## Risks or unknowns

* **DLC index `0x800` is read as an update (documented).** `application_of()` checks `low == 0x800` before the DLC branch, so `base + 0x1800` maps to `base + 0x1000`. Real DLC indexes run 1 to 2000 (`0x7D0`), so a low-12-bit `0x800` is always an update, never a DLC (docstring, line 236).

* **Unsupported layouts (documented):** XCIs with the 0x1000-byte key area at the start, and NCA0 headers (`README.md`).

* **Ticket-only detection limit.** Without keys, a cartridge is resolved by its tickets alone; a cartridge that carries no tickets (or fewer than it has games) may resolve to fewer IDs. This is a known limitation, not a defect.

* **Format facts unverified.** The `PFS0`, `HFS0`, `NCA`, and `XTS` details come from the docstrings, not an external specification.
