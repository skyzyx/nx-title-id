# Quick Flow Summary

A short tour of how `nx-title-id` turns a folder of Nintendo Switch packages into one base-game title ID per game, and, with `-b`, a Build ID. For the full audit, see [the comprehensive architecture audit](comprehensive.md).

Line references point at `src/nx_title_id/__init__.py` (1144 lines) unless noted otherwise. Binary-format details (`PFS0`, `HFS0`, `NCA`, `XTS`, tickets, the NCA AES-CTR counter) come from the file's docstrings and comments, which cite switchbrew and hactool. The `-b` path was checked against three real retail dumps; the rest of the format facts were not re-checked against an external specification.

## Entrypoint

| How it's invoked                 | What runs                                                            |
|----------------------------------|----------------------------------------------------------------------|
| `uvx nx-title-id <folder>`       | Console script `nx-title-id = "nx_title_id:main"` (`pyproject.toml`) |
| `uv run nx-title-id <folder>`    | Same console script, from the project's virtual environment          |
| `python -m nx_title_id <folder>` | `src/nx_title_id/__main__.py`, which imports and calls `main()`      |
| `nx_title_id.main([...])`        | `main(argv: list[str] \| None = None)`, line 868                     |

## Primary flow

```text
main(argv)
  ├─ parse: folder, -k/--keys, -b/--build-id, -V/--version
  ├─ validate folder; resolve keys (-k, else ~/.switch/prod.keys if present)
  ├─ with -b: require prod.keys and a header_key up front (else exit 1)
  ├─ for each file in folder (sorted, non-recursive, .nsp/.nsz/.xci/.xcz):
  │    ├─ skip "._*" files
  │    ├─ (in one try) stat + is_package() + title_ids_of(); error → skip this file
  │    ├─ title_ids_of()      → set[int]
  │    │    1. tickets        every <32 hex>.tik → first 16 hex digits
  │    │    2. file name tag  [16 hex digits]
  │    │    3. meta NCAs      decrypt NCA headers with header_key (lazy, cached)
  │    └─ application_of()    each update/DLC ID → its base game ID, folded into apps
  ├─ for each base game (sorted):
  │    ├─ default: print "<TitleID>"
  │    └─ with -b: build_id_of() → module_id_of() → print "<TitleID> <BuildID>" or skip the game
  └─ exit 0 for one line, 3 for several, 1 for none
```

1. `main()` parses arguments and exits early if the folder isn't a directory or an explicit keys path doesn't exist (lines 875-910).

2. `header_key()`, `key_area_key()` and `titlekek()` are lazy closures that read `prod.keys` only when first needed (lines 912-1011).

3. Each candidate file is checked by magic bytes, then resolved by `title_ids_of()` (line 808), which returns every title ID the package declares, trying the cheapest, most reliable source first.

4. `application_of()` (line 849) folds updates (`…800`) and DLC (`base + 0x1000 + index`) into their base game.

5. With `-b`, `build_id_of()` (line 731) picks one package per base game: the newest update, else the base, never DLC. `module_id_of()` (line 590) then:
   * decrypts the NCA header of each `.nca`/`.ncz` until it finds the program NCA (content type `0`);
   * for a titlekey NCA (non-zero RightsId), reads the package's own `<rights ID>.tik` and decrypts the title key with `titlekek_XX`; otherwise derives the section key from `key_area_key_application_XX`;
   * decrypts the exefs section (AES-CTR, or as-is for the synthetic `None` encryption) and reads `main`, an NSO;
   * returns the 32-byte ModuleId, and `render_build_id()` (line 799) prints its first 8 bytes as 16 uppercase hex digits.

6. A game that cannot be resolved (ticket- or tag-only, a missing key or ticket, a personalized ticket) is skipped with a stderr line. Exit `0` means one game printed, `3` several, and `1` nothing printed or a setup error. The decision is made on the lines actually printed.

## Module roles

| File / function        | Role                                                                                   |
|------------------------|----------------------------------------------------------------------------------------|
| `pyproject.toml`       | Hatchling build, `nx-title-id` console script, version read from `__init__.py`         |
| `__main__.py`          | `python -m nx_title_id` support                                                        |
| `partition()`          | Parses a PFS0 or HFS0 header into `[(name, absolute offset)]`, with size sanity guards |
| `package_files()`      | Lists the files in an NSP/NSZ (PFS0) or an XCI/XCZ's `secure` HFS0 partition           |
| `is_package()`         | Cheap magic-byte check so mislabeled files are skipped                                 |
| `read_header_key()`    | Pulls `header_key` out of `prod.keys`                                                  |
| `read_key_area_keys()` | Pulls every `key_area_key_application_XX` out of `prod.keys`                           |
| `read_titlekeks()`     | Pulls every `titlekek_XX` out of `prod.keys`                                           |
| `xts_decrypt_sector()` | AES-128-XTS on one 0x200 sector, with Nintendo's big-endian tweak, built on AES-ECB    |
| `meta_title_of()`      | Decrypts each NCA/NCZ header and returns the set of meta-NCA title IDs                 |
| `title_ids_of()`       | Ticket → filename tag → meta NCA resolution, returning every declared ID as a set      |
| `application_of()`     | Maps a base, update, or DLC title ID to the base game's ID                             |
| `decrypt_nca_header()` | XTS-decrypts the 0xC00 NCA header region as six sectors                                |
| `ticket_of()`          | Finds and reads a package's `<rights ID>.tik`                                          |
| `title_key_of()`       | Recovers the title key from a common ticket with `titlekek_XX`                         |
| `exefs_section()`      | Locates the exefs PFS0 in the program NCA and decrypts it (None or AES-CTR)            |
| `module_id_of()`       | Program NCA → exefs → `main` NSO → the 32-byte ModuleId                                |
| `update_version_of()`  | Reads an update's version to rank updates                                              |
| `build_id_of()`        | Chooses the newest update (else base) per base game and returns its ModuleId           |
| `render_build_id()`    | First 8 bytes of the ModuleId as 16 uppercase hex digits                               |
| `main()`               | CLI, folder scan, `-b` precondition, skip/exit policy, output                          |

`_key_generation()` (line 432) and `_ctr_decrypt_section()` (line 450) are the private helpers behind `exefs_section()`.

## Design decisions

* **Single module, standard library first.** Ticket and tag resolution need only the standard library. `pycryptodome` is imported inside three functions (`title_key_of` line 312, `xts_decrypt_sector` line 348, `_ctr_decrypt_section` line 475), each commented as a deliberate exception to the "imports are always global" convention. A default run that resolves by ticket or tag never loads it.

* **Ticket over filename.** The ticket name comes from the package contents, while filename tags are user-editable and can be wrong.

* **Keys are lazy and never printed.** Each accessor reads `prod.keys` at most once, only when needed. No key value, title key, or section key appears in output.

* **`-b` is opt-in.** The Build ID needs the program NCA decrypted down to `main`, which costs keys, reads, and CPU. Without the flag, output is unchanged.

* **First 8 bytes of the ModuleId.** That is the Build ID that nxdumptool and Atmosphere show. The full 32-byte field is read, and only the print site trims it.

* **A synthetic `None`-encryption path.** The test builders write the same NCA layout with `EncryptionType` 1, so CI exercises the whole chain with no retail assets. Real AES-CTR is covered by a synthetic round trip plus gated real-fixture tests.

* **Hand-rolled XTS.** `pycryptodome` has no XTS mode, and Nintendo uses a big-endian sector tweak, so XTS is built on ECB (docstring, line 337).

* **Sets, not a single winner.** `title_ids_of()` and `meta_title_of()` return every title ID a package declares, so a multi-title cartridge contributes every base game it carries.

* **Fail soft per package and per game.** A malformed package, an unreadable file, or a game with no readable Build ID is skipped with a stderr message; the rest still print.

* **Reads stay small.** Without `-b`, only partition headers and one 0x200-byte NCA sector per file are read. With `-b`, the 0xC00 header region, the ticket, and the exefs section are read as well; the package body is still never read in full.

## Risks or unknowns

* **DLC index `0x800` is read as an update (documented).** `application_of()` checks `low == 0x800` before the DLC branch, so `base + 0x1800` maps to `base + 0x1000`. Real DLC indexes run 1 to 2000 (`0x7D0`), so this is expected.

* **Unsupported layouts (documented):** XCIs with the 0x1000-byte key area at the start, and NCA0 headers (`README.md`). Under `-b`, personalized tickets are also unsupported.

* **Ticket-only detection limit.** Without keys, a cartridge is resolved by its tickets alone and may resolve to fewer IDs than it has games.

* **The key-area (zero RightsId) path is untested on retail data.** All three real fixtures are titlekey NCAs; the key-area path has only a synthetic round trip.

* **Update ordering is unverified on real dumps.** `update_version_of()` reads a synthetic offset (`META_VERSION_AT`, line 81), so with real updates the choice falls back to "any update beats the base, ties by sorted path".

* **Only the first program NCA is read** for a game's chosen package.

* **Format facts mostly unverified against a specification.** What was exercised on real data: container layout, tickets, header XTS, the NCA section layout, titlekey decryption, AES-CTR, and the NSO ModuleId. The rest is taken from the cited comments.
