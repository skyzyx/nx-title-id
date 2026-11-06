# Design Document

## Overview

This feature adds an opt-in `-b`/`--build-id` flag to `nx-title-id`. With the flag, each output line gains the game's Build ID — the `ModuleId` digest read from the `main` NSO executable inside the game's program NCA — printed as `<TitleID> <BuildID>` (single ASCII space; the Build ID is the first 8 bytes of the 32-byte `ModuleId`, as 16 uppercase hex digits, the form nxdumptool and Atmosphere show). Without the flag, behavior is byte-for-byte unchanged.

The Build ID is strictly more expensive to resolve than the Title ID. It cannot come from a ticket name or a filename tag, and it cannot be read from the single NCA header sector the tool already decrypts for the meta-NCA fallback. Reaching it requires decrypting a program NCA's filesystem down to the `exefs` partition, finding the file named `main`, and reading the 32-byte `ModuleId` at offset `0x40` of its NSO header (of which the first 8 bytes are printed). Because this path always needs keys, `-b` promotes usable keys from an optional fallback to a required precondition: the run fails up front (exit `1`) when keys are missing, and skips any individual game whose Build ID cannot be resolved while continuing with the rest.

The design keeps the project's single-module shape (`src/nx_title_id/__init__.py`), its resolution-in-trust-order philosophy, and its per-package skip policy. It adds a small set of functions for NCA section decryption and NSO parsing, a per-base-game Build-ID resolver that honors the newest-update rule, and CLI wiring for the flag and the up-front keys check.

### Scope and risk (stated honestly)

Reading a Build ID from a real retail program NCA is a genuinely larger crypto surface than anything the tool does today. The current code decrypts only NCA _header_ sectors with the hand-rolled XTS routine. The Build ID lives in _section data_ (the `exefs` PFS0), which on a retail standard-crypto NCA is:

1. Protected by a per-NCA key area. The `EncryptedKeyArea` (4 × `0x10` at `0x300` in the decrypted header) must be decrypted with `key_area_key_application_XX` from `prod.keys` to recover the section keys.
2. Stored under AES-CTR (`EncryptionType` `3`) for the `exefs`, with the counter seeded from the section's crypto settings — a second cipher mode beyond the header XTS.
3. For title-rights (ticket) NCAs, keyed from the titlekey rather than the key-area key, which additionally needs `titlekek_XX` and the ticket's encrypted titlekey. All three real retail fixtures are of this kind (non-zero `RightsId`), so this path is required for Requirement 6.5.

This design treats the work in two clearly separated paths so the feature is fully testable in CI without real assets:

* **Synthetic path (always exercised in CI).** The test builder produces a program NCA whose `exefs` section is declared `EncryptionType` `1` (None). The production code's section reader handles the None case by reading the section bytes directly after the `0xC00` header region. This exercises the entire locate-program-NCA → parse-FsHeader → read-exefs-PFS0 → find-`main` → extract-`ModuleId` chain deterministically, with no retail crypto.
* **Real-crypto path (exercised only when real fixtures and real keys are present).** For retail NCAs the section reader decrypts the key area with `key_area_key_application_XX`, then AES-CTR-decrypts the `exefs` section. The titlekey variant (implemented) reads the common-type ticket `<rights ID>.tik` from the same package, AES-128-ECB-decrypts its title key block with `titlekek_XX`, and uses the result directly as the AES-CTR section key. A game that needs a path the build cannot serve (personalized ticket, missing ticket, missing key) is a per-package skip (Requirement 4.3), never a crash.

This staging is safe because Requirement 4 already defines "cannot resolve the Build ID" as a per-package skip. Any NCA the section reader cannot decrypt (unsupported encryption, missing key-area key, missing ticket or titlekek, personalized ticket) resolves to "no readable main NSO" and is skipped with a one-line message, exactly as the requirements specify.

## Architecture

The module keeps its existing shape and ordering (imports, constants, module vars, the `MalformedPackageError` class, private functions, then public functions, with `# ---` section separators). New code slots in alongside the existing resolution functions. All imports stay global except the one already-documented lazy `from Crypto.Cipher import AES` inside `xts_decrypt_sector()`; the new AES-CTR usage reuses that same deferred import discipline by living in the same crypto section and importing lazily only on the real-crypto path, so a default (no `-b`) run never loads pycryptodome.

### New constants

```python
NSO_MAGIC = b'NSO0'          # main executable magic at offset 0 of the NSO header
MODULE_ID_AT = 0x40          # ModuleId offset within the NSO header
MODULE_ID_SIZE = 0x20        # ModuleId is up to 32 bytes
BUILD_ID_SIZE = 8            # tools show only the first 8 bytes as the Build ID
PROGRAM = 0                  # NCA content type for a program NCA (exefs lives here)
HEADER_REGION = 0xC00        # the AES-XTS-encrypted NCA header region
FS_ENTRY_AT = 0x240          # FsEntry array (4 x 0x10) in the decrypted header
FS_HEADER_AT = 0x400         # FsHeader array (one 0x200 block per section)
KEY_AREA_AT = 0x300          # EncryptedKeyArea (4 x 0x10) in the decrypted header
UPDATE_FLAG = 0x800          # low-bits value marking an update title ID
```

`META = 1` already exists; `PROGRAM = 0` is its sibling for the program NCA.

### New functions and where they fit

All of the following live in the crypto / NCA section of the module, after `meta_title_of` and before or beside `title_ids_of`:

```python
def decrypt_nca_header(header_key: bytes, raw: bytes) -> bytes:
    """
    Decrypt the 0xC00-byte NCA header region (6 sectors) with the header key,
    returning the plaintext header. Reuses xts_decrypt_sector per 0x200 sector,
    sectors numbered from 0.
    """
```

```python
def exefs_section(
    f: BufferedReader,
    nca_at: int,
    header: bytes,
    key_area_key: Callable[[int], bytes | None] | None,
) -> bytes | None:
    """
    Return the decrypted bytes of the program NCA's exefs PartitionFS section,
    or None when no exefs section is present or it cannot be decrypted. Walks
    the FsEntry/FsHeader arrays to find the PartitionFS (FsType 1) section,
    reads its raw bytes relative to the end of the 0xC00 header region, and
    decrypts per EncryptionType: 1 (None) returns the bytes as-is (the
    synthetic path); 3 (AesCtr) decrypts with a section key recovered from the
    key area; unsupported or unkeyable sections return None.
    """
```

```python
def module_id_of(path: Path, header_key: bytes, key_area_key: Callable[[int], bytes | None] | None) -> bytes | None:
    """
    Decrypt the package's program NCA far enough to read the main NSO's
    ModuleId, returning the exact MODULE_ID_SIZE bytes present or None when the
    package carries no program NCA or no readable main NSO. Finds a program NCA
    (content type 0), decrypts its header, reads its exefs, locates the file
    named "main" in that PFS0, and returns NSO header bytes
    [MODULE_ID_AT : MODULE_ID_AT + MODULE_ID_SIZE] after checking the NSO0
    magic.
    """
```

```python
def update_version_of(path: Path, header_key: bytes) -> int | None:
    """
    Read the update version of a package from its meta NCA (the CNMT), or None
    when no readable version is available. Used to pick the newest update when
    several updates for one base game are present; a None result means
    "readable update version unavailable", which the resolver treats as merely
    newer than the base.
    """
```

```python
def build_id_of(
    packages: Sequence[tuple[Path, set[int]]],
    base: int,
    header_key: bytes,
    key_area_key: Callable[[int], bytes | None] | None,
) -> bytes | None:
    """
    Resolve the Build ID to report for one base game, honoring the newest-update
    rule: prefer the newest update package (greatest readable update version,
    any update over the base when versions are unreadable), fall back to the
    base package, and never use a DLC package. Returns the ModuleId bytes or
    None when no base/update package yields a readable main NSO.
    """
```

The signature of `build_id_of` takes the already-scanned `(path, title_ids)` pairs so it can group a game's base and update packages without re-reading every file's title IDs. `main()` builds that list once during the folder scan it already performs.

### How the pieces connect to the existing module

* `title_ids_of` is unchanged. Under `-b`, `main()` still resolves title IDs exactly as today (ticket → tag → meta NCA), then additionally uses the scanned package list to resolve a Build ID per base game.
* `application_of` is unchanged and still folds base/update/DLC to the base game. The Build-ID resolver reuses the same low-12-bits role logic to classify a package's title IDs as base, update, or DLC.
* `xts_decrypt_sector` is reused by `decrypt_nca_header` for every header sector. The AES-CTR section decryption is new and lazy-imported alongside it.
* The `header_key()` closure in `main()` is reused as the lazy header-key source. A sibling lazy accessor supplies `key_area_key_application_XX`.

### Keys: What prod.keys must supply and the up-front precondition

Under `-b`, "usable keys" means a keys file that at minimum yields `header_key`, because the program NCA header must be decrypted before anything else. The synthetic test path needs only `header_key` (its `exefs` is None-encrypted). The real-crypto path additionally needs `key_area_key_application_XX` (and, for the titlekey variant, `titlekek_XX` plus the ticket titlekey).

The precondition is defined and checked _before processing packages_ (Requirement 4.1):

* If `-b` is given and no keys source is available at all (no `-k` and no default `prod.keys`), `main()` writes a one-line setup error to stderr and exits `1` immediately.
* If a keys source exists but cannot produce `header_key`, that is also a fatal up-front setup error under `-b` (exit `1`), because without `header_key` no Build ID can ever be resolved. This differs from the default run, where a missing `header_key` is only a warn-once-and-skip. The precondition check reads the keys file once at startup under `-b` to confirm `header_key` is present.

A missing `key_area_key_application_XX` is _not_ an up-front failure, because a folder of synthetic/None-encrypted or already-decryptable packages may still resolve. Instead, a package that needs a key-area key the file lacks resolves to no Build ID and is skipped per-package (Requirement 4.3).

### Data flow

```text
folder scan (existing)
  -> for each package: title_ids_of(pkg)  (ticket | tag | meta NCA)   [unchanged]
  -> collect (path, title_ids) pairs and fold to base games via application_of
      |
      | -- without -b: print one TitleID per base game                 [unchanged] |
      |----------------------------------------------------------------------------|
      '-- with -b (keys precondition already passed up front):
            for each base game:
              build_id_of(packages, base, header_key, key_area_key)
                -> pick newest update package (or base) for this base game
                -> module_id_of(chosen package)
                     -> find program NCA (content type 0)
                     -> decrypt_nca_header -> walk FsEntry/FsHeader
                     -> exefs_section (None | AES-CTR) -> PFS0
                     -> read "main" NSO -> bytes[0x40:0x60]
              if a ModuleId resolved: print "<TitleID> <BuildID>"
                (BuildID = render_build_id: first 8 bytes, 16 uppercase hex)
              else: skip this game with a one-line stderr message
```

## Components and interfaces

### Program-NCA location (`module_id_of`)

A program NCA is any package file whose decrypted NCA header has `ContentType` (`0x205`) equal to `PROGRAM` (`0`). `module_id_of` iterates `package_files(f)`, and for each `.nca`/`.ncz` entry reads the `0xC00` header region, decrypts it with `decrypt_nca_header`, checks the `NCA3`/`NCA2` magic at `0x200`, and selects the first program NCA. (A package can hold several NCAs; only the program NCA carries the `exefs`.)

### NCA header decryption (`decrypt_nca_header`)

The first `0xC00` bytes of an NCA are AES-XTS with `0x200`-byte sectors numbered from `0`. `decrypt_nca_header` decrypts six sectors by calling the existing `xts_decrypt_sector` with sector numbers `0..5`, returning the concatenated `0xC00` plaintext. Sector `1` (offset `0x200`) is the NCA header proper the existing meta path already reads; sectors `2..5` (offsets `0x400..0xBFF`) hold the four `0x200` FsHeaders.

### Section location and decryption (`exefs_section`)

Within the decrypted header:

* The `FsEntry` array is at `FS_ENTRY_AT` (`0x240`), four entries of `0x10`. Each entry has `StartOffset` (u32 @ `0x0`) and `EndOffset` (u32 @ `0x4`) in `0x200`-byte blocks counted from the start of the NCA (not from the end of the header region). The section starts at `nca_at + StartOffset*0x200`.
* The `FsHeader` for section _i_ is at `FS_HEADER_AT + i*0x200` (`0x400 + i*0x200`) inside the decrypted header. `FsType` is a u8 at `0x2` (`1` = PartitionFS), `HashType` a u8 at `0x3`, and `EncryptionType` a u8 at `0x4` (`1` = None, `2` = AesXts, `3` = AesCtr). The PFS0 sits at `PfsOffset` within the section; `PfsOffset` and `PfsSize` are u64s at `FsHeader+0x40` and `FsHeader+0x48` (HierarchicalSha256 layout), so the PFS0 starts at `nca_at + StartOffset*0x200 + PfsOffset`.

`exefs_section` finds the first section whose `FsType` is PartitionFS, reads its raw bytes, and decrypts by `EncryptionType`:

* `1` (None): return the raw bytes unchanged. This is the synthetic CI path.
* `3` (AesCtr): obtain the section key, then AES-CTR-decrypt. Lazy-imports the cipher like the XTS path.
  * Zero `RightsId`: decrypt the key area (`KEY_AREA_AT`) with `key_area_key(generation)` and take row 2 (bytes `0x20..0x30`).
  * Non-zero `RightsId`: the caller-supplied, already-decrypted `title_key` is the section key directly (no key-area step).
  * CTR construction: the 16-byte counter block is the upper 8 bytes (the u64 little-endian at `FsHeader+0x140`, packed big-endian) followed by the 8-byte block index `nca_offset >> 4` (big-endian), where `nca_offset` is the 0x10-aligned offset of the first byte read, relative to the NCA start. The read is aligned down to 0x10 and the lead-in dropped after decrypting.
* Anything else (including `2`/AesXts for the exefs, which retail exefs does not use), or a missing key (key-area key, ticket, or titlekek): return `None`, which flows up to a per-package skip.

Returning the whole `exefs` PFS0 lets the caller reuse `partition`-style parsing against an in-memory reader to find `main`.

### Titlekey flow (`ticket_of`, `title_key_of`, `read_titlekeks`)

For a program NCA with a non-zero `RightsId`, `module_id_of` does the following before calling `exefs_section`:

1. `ticket_of(f, rights_id)` finds the package file named `<32 hex digits of the rights ID>.tik` (case-insensitive) and reads a fixed `TICKET_READ_SIZE` window.
2. `title_key_of(ticket, rights_id, titlekek)` reads the signature type (u32 at 0) and looks up where the ticket body starts (`TICKET_BODY_AT`: RSA-4096 `0x10000`/`0x10003` → `0x240`, RSA-2048 `0x10001`/`0x10004` → `0x140`, ECDSA `0x10002`/`0x10005` → `0x80`, HMAC-SHA1-160 `0x10006` → `0x40`; an unknown type returns `None`).
3. The title key type at body `+0x141` must be `0` (common). Type `1` (personalized, RSA-2048 block) returns `None`.
4. The generation is the rights ID's last byte (the master key revision) with `0` and `1` both mapping to generation `0`, else `value - 1`, the same normalization as `_key_generation`.
5. The first 16 bytes of the title key block (body `+0x40`) are AES-128-ECB-decrypted with `titlekek_<generation>` from `prod.keys`; the result is the section key.

Any `None` along the way leaves `title_key` unset and the package is skipped. Sources: switchbrew "Ticket" and "NCA" pages, hactool, Atmosphere. In `main()`, `titlekek(generation)` is a lazy accessor (parsed by `read_titlekeks`) built like `key_area_key`.

### Main NSO read (`module_id_of` tail)

Given the decrypted `exefs` bytes, `module_id_of` wraps them in a `reader()`-equivalent `BufferedReader` over `BytesIO` and parses the PFS0 with the existing `partition` logic to locate the entry named `main`. It reads the NSO header at that entry's offset, verifies `NSO_MAGIC` (`NSO0`) at offset `0`, and returns the `MODULE_ID_SIZE` bytes at `MODULE_ID_AT`. A missing `main`, a wrong magic, or a short read returns `None`. `module_id_of` keeps the full field; `render_build_id(module_id)` (used by `main()` at the single print site) returns `module_id[:BUILD_ID_SIZE].hex().upper()`.

### Newest-update selection (`build_id_of`)

For a base game `base`, `build_id_of` partitions the scanned packages (by their folded title IDs) into those carrying the base title, those carrying the update title (`base + UPDATE_FLAG`), and DLC (ignored). It then:

1. Among update packages, reads each `update_version_of`; selects the greatest readable version. If several updates have no readable version, any update is treated as newer than the base (Requirement 3.2), so an arbitrary-but-deterministic update (first by sorted path) is chosen.
2. If at least one update package exists, the chosen update package is the Build-ID source; otherwise the base package is (Requirement 3.3).
3. Calls `module_id_of` on the chosen package. If that is `None` (program NCA unreadable), returns `None` so the game is skipped.

A game resolved only by ticket/tag (no package carries a program NCA to decrypt) yields `None` here and is skipped per Requirement 4.2.

### CLI wiring (`main`)

`main()` gains:

* An argparse flag: `parser.add_argument('-b', '--build-id', action='store_true', dest='build_id', help=...)`.
* An up-front keys precondition when `args.build_id` is set, run after the folder check and the `-k` existence check but before the per-package loop. If no keys source exists, or the keys file yields no `header_key`, print a one-line setup error and `sys.exit(1)`.
* Sibling lazy accessors `key_area_key(generation)` and `titlekek(generation)` beside the existing `header_key()` closure, caching parsed `key_area_key_application_XX` and `titlekek_XX` values from the keys file. Both are threaded as callbacks: `build_id_of(packages, base, header_key, key_area_key, titlekek=None)` → `module_id_of(path, header_key, key_area_key, titlekek=None)`, which decrypts the titlekey and passes the plaintext to `exefs_section(f, nca_at, header, key_area_key, title_key=None)` → `_ctr_decrypt_section(..., key_area_key, title_key=None)`.
* After the scan, when `build_id` is set, resolve a Build ID per base game and print `f'{app:016X} {module_id.hex().upper()}'`; when unset, print `f'{app:016X}'` exactly as today.

The exit contract is unchanged (Requirement 4.4): after skips, `0` for one game that resolved a Build ID, `3` for several, `1` for nothing-resolved or the up-front setup errors, `2` for bad args (argparse).

## Data models

The feature introduces no new persistent data model; it reads existing on-disk Switch structures. The relevant byte layouts (from the requirements' switchbrew citations) are:

### NCA header region (first `0xC00` bytes, AES-XTS, `0x200` sectors, big-endian tweak)

| Field            | Offset            | Size   | Notes                                                                                                                                                                  |
|------------------|-------------------|--------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Magic            | `0x200`           | 4      | `NCA3` or `NCA2`                                                                                                                                                       |
| ContentType      | `0x205`           | 1      | `0` = Program, `1` = Meta                                                                                                                                              |
| ContentSize      | `0x208`           | 8      | total NCA size                                                                                                                                                         |
| RightsId         | `0x230`           | 16     | non-zero means a titlekey (ticket) NCA                                                                                                                                 |
| FsEntry array    | `0x240`           | 4×0x10 | each: StartOffset u32 @0, EndOffset u32 @4, in 0x200 blocks from the NCA start                                                                                         |
| EncryptedKeyArea | `0x300`           | 4×0x10 | decrypted with `key_area_key_application_XX`                                                                                                                           |
| FsHeader\[i\]    | `0x400 + i*0x200` | 0x200  | FsType u8 @0x2 (1=PartitionFS), HashType u8 @0x3, EncryptionType u8 @0x4 (1=None, 2=AesXts, 3=AesCtr), PfsOffset u64 @0x40, PfsSize u64 @0x48, upper CTR u64 LE @0x140 |

FsEntry offsets are in `0x200`-byte blocks relative to the start of the NCA. Observed on the real fixtures: the 32-byte ModuleId begins with the 8 bytes encoded in the fixture file name, followed by 8 further non-zero bytes and 16 zero bytes. Decision (user-approved): the printed Build ID is only the first 8 bytes, so it equals the file name's Build ID; `module_id_of` still returns the full field and `render_build_id` truncates at the single print site.

### Main NSO header

| Field    | Offset | Size | Notes                        |
|----------|--------|------|------------------------------|
| Magic    | `0x0`  | 4    | `NSO0`                       |
| ModuleId | `0x40` | 0x20 | the Build ID, up to 32 bytes |

### In-memory types

* `module_id_of` and `build_id_of` return `bytes | None` (the exact `ModuleId` bytes, length preserved).
* `key_area_key` is a `Callable[[int], bytes | None]` keyed by key generation, mirroring the lazy `header_key` callable.
* The scanned package list passed to the resolver is `Sequence[tuple[Path, set[int]]]` (covariant read-only, per house convention), each pair being a package path and the set of title IDs `title_ids_of` resolved for it.

## Correctness properties

_A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees._

This feature suits property-based testing because the Build-ID path is dominated by pure byte-level parsing and selection logic with large input spaces: the `ModuleId` is an arbitrary byte sequence up to 32 bytes, update-version selection ranges over many version/package combinations, and the default-vs-feature output comparison holds across every folder shape. The synthetic builder (Requirement 6.1) produces a `None`-encryption `exefs` so these properties run deterministically in CI with no retail crypto; the real-crypto path is validated separately by the integration tests in the Testing Strategy.

### Property 1: ModuleId round-trip

_For any_ byte sequence `B` of length up to 32, when `B` is embedded as the `ModuleId` of a synthetic program NCA's `main` NSO and the package is parsed, the extracted `ModuleId` field equals `B` followed by zero padding to the full 32-byte field (`module_id_of` returns the whole field, untruncated). The printed Build ID is derived from that field by Property 2.

**Validates: Requirements 2.1, 6.2.**

### Property 2: Build-ID rendering is the first 8 bytes as uppercase hex

_For any_ `ModuleId` field of 8 to 32 bytes (the extracted field is always the full 32 bytes), `render_build_id` returns exactly 16 uppercase hex digits that decode to the field's first 8 bytes, regardless of the bytes after them.

**Validates: Requirements 2.2.**

### Property 3: Newest-update selection is monotonic in version

_For any_ base game together with a set of its update packages (each carrying a distinct embedded `ModuleId`), the reported Build ID is the one embedded in the update with the greatest readable update version; and _for any_ such set, an update is always preferred over the base package, including when update versions are unreadable (any update counts as newer than the base). When no update package is present, the reported Build ID is the base package's.

**Validates: Requirements 3.1, 3.2, 3.3.**

### Property 4: A DLC is never the Build-ID source

_For any_ base game and _any_ collection of its DLC packages (each carrying its own embedded `ModuleId`), the reported Build ID is never equal to a DLC package's embedded `ModuleId`; it is always the base package's or an update package's.

**Validates: Requirements 3.4.**

### Property 5: Default output is unchanged when the flag is absent

_For any_ folder of packages, running the System without `-b` produces byte-identical standard output and the same exit code as the current default behavior (one 16-uppercase-hex Title ID per base game), and does not decrypt any program NCA filesystem or read a `main` NSO.

**Validates: Requirements 1.2, 5.1, 5.2, 5.3, 6.4.**

## Error handling

Error handling extends the module's existing two-tier model: `MalformedPackageError` and per-package `OSError` are a single package's problem (skip it, keep going); a `ValueError`-class keys problem is the run's problem. The `-b` flag adds one up-front fatal class and reuses the per-package skip for everything else.

| Condition                                                                     | Policy                                  | Exit |
|-------------------------------------------------------------------------------|-----------------------------------------|------|
| `-b` with no keys source (no `-k`, no default `prod.keys`)                    | one-line setup error before any package | `1`  |
| `-b` with a keys file that yields no `header_key`                             | one-line setup error before any package | `1`  |
| `-b`, game resolved only by ticket/tag (no program NCA)                       | per-package skip, one-line stderr       | n/a  |
| `-b`, package has no program NCA or no readable `main` NSO                    | per-package skip, one-line stderr       | n/a  |
| `-b`, program NCA section needs a key the file lacks / unsupported encryption | per-package skip, one-line stderr       | n/a  |
| `-b`, nothing resolved a Build ID after skips                                 | existing nothing-resolved setup error   | `1`  |
| malformed package header (existing `MalformedPackageError`)                   | per-package skip, one-line stderr       | n/a  |
| unreadable package / vanished file (existing `OSError`)                       | per-package skip, one-line stderr       | n/a  |
| bad CLI arguments (argparse)                                                  | argparse usage error                    | `2`  |
| one game resolved a Build ID                                                  | print line, success                     | `0`  |
| several games resolved a Build ID                                             | print each sorted, multi-game warning   | `3`  |

Specific rules:

* The up-front keys precondition (Requirement 4.1) runs after the folder and `-k`-existence checks and before the per-package loop, so a missing-keys `-b` run never touches a package. Under `-b`, a keys file present but lacking `header_key` is fatal here (not the default warn-once), because no Build ID could ever resolve without it.
* `module_id_of` and `exefs_section` return `None` rather than raising for the expected "cannot reach the `main` NSO" cases (no program NCA, no `exefs`, unsupported/un-keyable encryption, missing `main`, bad NSO magic). `None` flows up to a single per-package skip message naming the package and the reason (Requirements 4.2, 4.3). Genuinely corrupt headers still raise `MalformedPackageError` and are skipped by the existing handler.
* A missing `key_area_key_application_XX` is deliberately not an up-front failure: a folder of synthetic/None-encrypted packages can resolve with only `header_key`. Packages that actually need the absent key-area key are skipped per-package.
* The key value is never printed, on stdout or stderr, preserving the existing no-secrets invariant.

## Testing strategy

The feature uses the project's dual approach under Tryke: property-based tests for the pure extraction/selection/rendering logic, and example, integration, and smoke tests for CLI surface, precondition, skip behavior, exit codes, and real fixtures. Tests live beside the existing suite (`tests/test_title.py` for function-level logic, `tests/test_cli.py` for end-to-end CLI), reusing `tests/builders.py`.

### Builder additions (Requirement 6.1)

`tests/builders.py` gains a builder that embeds a caller-specified Build ID into a program NCA's `main` NSO within a synthetic package:

* An NSO blob factory: a buffer with `NSO0` at `0x0` and the caller's `ModuleId` bytes at `0x40`.
* An `exefs` PFS0 (built with the existing `_partition`) containing that `main` NSO.
* A program NCA whose decrypted header declares `ContentType = PROGRAM`, one PartitionFS `FsEntry`/`FsHeader` pair with `EncryptionType = 1` (None) pointing at the `exefs`, encrypted with `HEADER_KEY` via the existing `nintendo_xts_encrypt` reference. A convenience helper assembles base/update/DLC packages carrying such a program NCA plus the title IDs needed for folding, and (for updates) an embedded update version.

The existing `HAVE_REAL_NSPS` computation must be updated from `{tid:016X}.nsp` to the hyphenated `<TitleID>-<BuildID>.nsp` form now on disk, and a helper must expose each fixture's filename-encoded Build ID for Requirement 6.5. This is the one builder change the design calls out explicitly, since the current flag would never see the renamed fixtures.

### Property tests (minimum 100 iterations each, seeded)

Following the project's convention (seeded `random.Random(0)` loops, not Hypothesis; static `@test.cases` labels, never generated in a `for` loop at discovery time), each property is realized by one property-based test iterating ≥ 100 generated inputs. Each test carries a tag comment in the form **Feature: build-id, Property {number}: {property_text}**.

* **ModuleId round-trip (P1):** generate random `ModuleId` byte sequences of varying length (0..32), build a synthetic program-NCA package embedding each, extract via `module_id_of`, assert byte-equality; additionally assert the CLI under `-b` prints its uppercase-hex form.
* **Hex rendering (P2):** generate random byte sequences (0..32 bytes), assert the rendering is uppercase, `len == 2 * len(bytes)`, and `bytes.fromhex(rendered.lower())` equals the original.
* **Newest-update selection (P3):** generate a base plus a random set of update packages with distinct versions and distinct embedded `ModuleId`s (including runs where versions are unreadable), assert the resolved Build ID is the max-version update's, that an update always beats the base, and that a base-only input resolves to the base's.
* **DLC exclusion (P4):** generate a base (and optionally an update) plus random DLC packages with distinct embedded `ModuleId`s, assert the resolved Build ID is never any DLC's.
* **Default-output preservation (P5):** reuse/extend the existing seeded mixed-folder generator; for each folder assert the no-`-b` run's stdout and exit code are byte-identical to the default-mode baseline for that same folder.

### Example and edge tests

* `-b`/`--build-id` is accepted by argparse (Requirement 1.1).
* `-b` with no keys source: exit `1`, one-line setup error, empty stdout, before any package output (Requirements 2.3, 4.1, 6.3).
* `-b` with a keys file lacking `header_key`: fatal up front, exit `1` (Requirement 4.1).
* `-b` known-value end-to-end: a synthetic package with a known embedded Build ID prints `<TitleID> <BuildID>` with the expected uppercase hex (Requirement 6.2).
* `-b` per-package skip: a ticket-only game is skipped with a one-line stderr message while a synthetic program-NCA game still prints its Build ID (Requirements 4.2, 6.3).
* `-b` skip when no program NCA / unreadable `main`: skipped with a message, others print (Requirement 4.3).
* `-b` exit-code contract: representative folders yielding one game (`0`), several games (`3`), and all-skipped/nothing-resolved (`1`); bad args (`2`) via argparse (Requirements 4.4, 4.5).
* Default behavior unchanged: a default run emits no Build IDs and matches the existing format (Requirement 5.3), complementing the P5 preservation test.

### Integration tests (real fixtures, gated)

Real `<TitleID>-<BuildID>.nsp` fixtures exercise the real-crypto path (key-area-key decryption, AES-CTR `exefs`):

* **Requirement 6.5:** for each present fixture, run `-b` with real keys and assert the printed Build ID equals the hex after the hyphen in the filename.
* **Requirement 6.6:** gate these with `@test.skip_if(not HAVE_REAL_NSPS, reason=NO_REAL_NSPS)`; because the real-crypto path also needs real keys, additionally gate on `HAVE_REAL_KEYS` so a clone with fixtures but no `prod.keys` skips cleanly rather than failing.

These are the only tests that depend on the real retail crypto path; CI without real assets runs the full synthetic property/example suite and skips the integration tests.
