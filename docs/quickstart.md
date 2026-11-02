# nx-title-id Quick Flow Summary

A short tour of how `nx-title-id` turns a folder of Nintendo Switch packages into a single base-game title ID. For the full audit, see [the comprehensive architecture audit](comprehensive.md).

Line references point at `src/nx_title_id/__init__.py` unless noted otherwise. Binary-format details (PFS0, HFS0, NCA, XTS) come from the docstrings in that file and were not checked against an external specification.

## Entrypoint

| How it's invoked                 | What runs                                                                            |
|----------------------------------|--------------------------------------------------------------------------------------|
| `uvx nx-title-id <folder>`       | Console script `nx-title-id = "nx_title_id:main"` (`pyproject.toml`)                 |
| `uv run nx-title-id <folder>`    | Same console script, from the project's virtual environment                          |
| `python -m nx_title_id <folder>` | `src/nx_title_id/__main__.py`, which imports and calls `main()`                      |
| `nx_title_id.main([...])`        | `main(argv=None)` (line 198) accepts an argument list, so it can be called from code |

## Primary flow

```text
main(argv)
  ├─ parse: folder, -k/--keys, -V/--version
  ├─ validate folder; resolve keys (-k, else ~/.switch/prod.keys if present)
  ├─ for each file in folder (sorted, non-recursive, .nsp/.nsz/.xci/.xcz):
  │    ├─ skip "._*" and empty files
  │    ├─ is_package()        magic check: PFS0 at 0x0, or HEAD at 0x100
  │    ├─ title_of()
  │    │    1. ticket name    <32 hex>.tik → first 16 hex digits
  │    │    2. file name tag  [16 hex digits]
  │    │    3. meta NCA       decrypt NCA header with header_key (lazy, cached)
  │    └─ application_of()    update/DLC ID → base game ID
  └─ print each distinct base ID as 16 uppercase hex digits
```

1. `main()` parses arguments and exits early if the folder isn't a directory or an explicit keys path doesn't exist (lines 198–222).
2. It builds a `header_key()` closure that reads `prod.keys` once, only when a package actually needs the meta NCA fallback (line 226).
3. Each candidate file is checked by magic bytes, then resolved by `title_of()` (line 162), which tries the cheapest, most reliable source first.
4. `application_of()` (line 183) folds updates (`…800`) and DLC (`base + 0x1000 + index`) into their base game, and the results go into a set.
5. If the set is empty it exits non-zero. If it holds more than one ID, a warning goes to stderr and every ID is still printed to stdout.

## Module roles

| File / function        | Role                                                                                |
|------------------------|-------------------------------------------------------------------------------------|
| `pyproject.toml`       | Hatchling build, `nx-title-id` console script, version read from `__init__.py`      |
| `__main__.py`          | `python -m nx_title_id` support                                                     |
| `partition()`          | Parses a PFS0 or HFS0 header into `[(name, absolute offset)]`                       |
| `package_files()`      | Lists the files in an NSP/NSZ (PFS0) or an XCI/XCZ's `secure` HFS0 partition        |
| `is_package()`         | Cheap magic-byte check so mislabeled files are skipped                              |
| `read_header_key()`    | Pulls `header_key` out of `prod.keys`                                               |
| `xts_decrypt_sector()` | AES-128-XTS on one 0x200 sector, with Nintendo's big-endian tweak, built on AES-ECB |
| `meta_title_of()`      | Decrypts each NCA/NCZ header and returns the lowest meta-NCA title ID               |
| `title_of()`           | Ticket → filename tag → meta NCA resolution order                                   |
| `application_of()`     | Maps a base, update, or DLC title ID to the base game's ID                          |
| `main()`               | CLI, folder scan, skip/exit policy, output                                          |

## Design decisions

* **Single module, standard library first.** Steps 1 and 2 need only the standard library. `pycryptodome` is a declared dependency, but it's imported inside `xts_decrypt_sector()` (line 111), so it only loads when a package needs the meta NCA.
* **Ticket over filename.** The ticket name comes from the package contents, while filename tags are user-editable and can be wrong (comment on line 171, `README.md`).
* **Keys are lazy and never printed.** `prod.keys` is read at most once, only when needed, and key values never appear in output (module docstring, `README.md`).
* **Hand-rolled XTS.** `pycryptodome` has no XTS mode, and Nintendo uses a big-endian sector tweak, so XTS is built on ECB (docstring, line 105).
* **Lowest meta title ID wins.** A package that bundles a base game with its updates or DLC still reports the base game (line 131).
* **One answer per folder.** The tool is built for one game per folder, so it reduces everything to base IDs and warns when there's more than one.
* **Header-only reads.** Only partition headers and one 0x200-byte NCA sector per file are read, so multi-gigabyte packages are cheap to scan.

## Risks or unknowns

* **Malformed packages are skipped (fixed).** Truncated headers and unterminated names raise `MalformedPackageError`, which `main()` reports as `skipping <file>: …` before moving on to the next package. `ValueError` now only comes from a `prod.keys` without `header_key`.
* **DLC index `0x800` would be read as an update.** `application_of()` checks `low == 0x800` before the DLC branch, so `base + 0x1800` maps to `base + 0x1000`. I haven't checked whether real DLC uses that index.
* **Unsupported layouts (documented):** XCIs with the 0x1000-byte key area at the start, and NCA0 headers (`README.md`).
* **Bad `prod.keys` is fatal mid-run.** A keys file without `header_key` stops the run the first time a package needs it, and title IDs already found aren't printed.
* **Loose typing.** Several parameters are untyped or `Any`. Pyrefly reports 0 errors on its `basic` preset, but the code doesn't meet the project's own "avoid `Any`" convention.
