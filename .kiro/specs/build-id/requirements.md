# Requirements Document

## Introduction

`nx-title-id` scans a folder of Nintendo Switch game packages and prints the base-game Title ID for each distinct game it finds. This feature adds an opt-in `-b`/`--build-id` flag that augments each output line with the game's Build ID: the `ModuleId` digest of the main NSO executable inside the package.

Unlike the Title ID (which identifies the game and is constant across its updates and DLC), the Build ID changes whenever a new update or DLC build ships. Resolving it is strictly more expensive than resolving a Title ID: it cannot be read from a ticket name or a filename tag, and it cannot be read from the single NCA header sector the tool already decrypts. It requires decrypting the program NCA's filesystem to reach the `exefs`, locate the `main` NSO, and read the `ModuleId` field from its header. For this reason `-b` makes usable keys a required precondition: the tool fails up front when keys are missing, and it skips any individual game whose Build ID cannot be resolved while continuing to process the rest.

The `ModuleId` field is 32 bytes, but the Build ID that tools such as nxdumptool and Atmosphere show is its first 8 bytes. The System therefore prints the first 8 bytes of the `ModuleId` as 16 uppercase hex digits, which is also the form the ground-truth test fixtures encode in their filenames, `<TitleID>-<BuildID>.nsp` (for example `tests/01004B800AF5A000-B98E9D18B061B2F0.nsp`).

Source for the Build ID location: the `ModuleId` field at offset `0x40` (`0x20` bytes) of the main NSO executable header, derived from the ELF GNU `.note` build-id section. See [switchbrew.org/wiki/NSO](https://switchbrew.org/wiki/NSO).

## Glossary

* **System**: The `nx-title-id` command-line tool.
* **Title_ID**: The 16-hex-digit (8-byte) identifier of a Nintendo Switch application. Constant across a game's base, updates, and DLC. Printed as 16 uppercase hex characters.
* **Base_Game_Title_ID**: The Title ID whose low 12 bits are zero, to which a game's base, update (`…800`), and DLC (`base + 0x1000 + index`) entries fold via the tool's existing `application_of()` logic.
* **Build_ID**: The first 8 bytes of the `ModuleId` read from the main NSO executable header at offset `0x40` (a 32-byte field). Printed as 16 uppercase hex digits; changes across updates and DLC for the same Base_Game_Title_ID.
* **Program_NCA**: The NCA whose content type is "program" (content type `0`), which holds the `exefs` partition containing the `main` NSO.
* **Main_NSO**: The file named `main` in a Program_NCA's `exefs`; its header carries the `ModuleId` at offset `0x40`.
* **Build_ID_Flag**: The `-b`/`--build-id` command-line flag that switches the System into Build-ID output mode.
* **Keys**: The `prod.keys` file supplying the `header_key` and any further keys needed to decrypt a Program_NCA filesystem down to the Main_NSO.
* **Package**: A single `.nsp`, `.nsz`, `.xci`, or `.xcz` file.

## Requirements

### Requirement 1: Build-ID output flag

**User Story:** As a user cataloguing my Switch dumps, I want a `-b`/`--build-id` flag, so that the tool reports each game's Build ID alongside its Title ID.

#### Acceptance criteria

1. THE System SHALL accept a command-line flag spelled `-b` or `--build-id`.
2. WHERE the Build_ID_Flag is absent, THE System SHALL produce output identical to the current behavior (one Base_Game_Title_ID per line, 16 uppercase hex characters).
3. WHERE the Build_ID_Flag is present, THE System SHALL print, for each distinct Base_Game_Title_ID, the Base_Game_Title_ID followed by its Build_ID on a single line.
4. WHERE the Build_ID_Flag is present, THE System SHALL separate the Base_Game_Title_ID and the Build_ID with a single ASCII space and print the Build_ID as exactly 16 uppercase hexadecimal digits (the first 8 bytes of the `ModuleId`).

### Requirement 2: Determining the build ID

**User Story:** As a user, I want the Build ID read from the package's main executable, so that the reported value matches the actual installed build.

#### Acceptance criteria

1. WHEN the Build_ID_Flag is present and a Package carries a Program_NCA, THE System SHALL read the Build_ID from the `ModuleId` field at offset `0x40` of the Main_NSO header in that Program_NCA's `exefs`.
2. THE System SHALL read the full 32-byte `ModuleId` field and render only its first 8 bytes as 16 uppercase hexadecimal digits, dropping every byte after the eighth, because those bytes are not part of the Build_ID that tools show.
3. WHEN the Build_ID_Flag is present, THE System SHALL require Keys to resolve the Build_ID, because reaching the Main_NSO requires decrypting the Program_NCA filesystem.

### Requirement 7: Titlekey (rights-ID) program NCAs

**User Story:** As a user with retail dumps, I want Build IDs read from program NCAs that are keyed by a ticket titlekey, so that real games work and not only key-area-keyed NCAs.

#### Acceptance criteria

1. WHEN the Build_ID_Flag is present and a Program_NCA has a non-zero `RightsId`, THE System SHALL look for a ticket named `<RightsId as 32 hex digits>.tik` (case-insensitive) in the same Package.
2. WHEN a common-type ticket (title key type `0`) is found, THE System SHALL decrypt its title key block with `titlekek_XX` from Keys, where XX is the generation derived from the last byte of the `RightsId` (`0` and `1` both mapping to generation `0`, otherwise the value minus one), and use the result directly as the AES-CTR section key.
3. THE System SHALL locate the ticket body using the ticket's signature type (RSA-4096, RSA-2048, ECDSA, and HMAC-SHA1-160 variants) and SHALL treat an unrecognized signature type as unsupported.
4. IF the ticket is missing, is personalized (title key type `1`), has an unrecognized signature type, or the required `titlekek_XX` is absent from Keys, THEN THE System SHALL skip that game with a one-line explanation on standard error and continue processing the remaining Packages.
5. THE System SHALL NOT print any key value, including a titlekek or titlekey, to standard output or standard error.

### Requirement 3: Build ID per base game under folding

**User Story:** As a user, I want one Build ID shown per game, so that the output stays one line per base game and reflects the latest build I have installed.

#### Acceptance criteria

1. WHEN the Build_ID_Flag is present and an update Package is present for a Base_Game_Title_ID (an update Title_ID equal to the base plus `0x800`), THE System SHALL report the Build_ID of the newest update Package for that Base_Game_Title_ID.
2. WHEN the Build_ID_Flag is present and multiple update Packages are present for the same Base_Game_Title_ID, THE System SHALL select the newest update as the Package carrying the greatest update version, and WHERE a readable update version is unavailable THE System SHALL treat the presence of any update as newer than the base.
3. IF the Build_ID_Flag is present and no update Package is present for a Base_Game_Title_ID, THEN THE System SHALL report the Build_ID of the base-game Package.
4. WHERE the Build_ID_Flag is present, THE System SHALL derive the reported Build_ID only from base-game and update Packages and SHALL NOT use a DLC Package as the Build_ID source.

### Requirement 4: Behavior when the build ID cannot be determined

**User Story:** As a user running with ticket/tag-only packages or incomplete keys, I want clear, predictable behavior, so that the tool fails fast when keys are missing and otherwise reports every game it can.

#### Acceptance criteria

1. IF the Build_ID_Flag is present and no usable Keys are available (no `-k` given and no default `prod.keys`, or the Keys file lacks the keys needed to decrypt a Program_NCA), THEN THE System SHALL write a one-line setup error to standard error and exit with code `1` before processing Packages.
2. IF the Build_ID_Flag is present and a game was resolved only by a ticket name or a filename tag (so no Program_NCA was decrypted), THEN THE System SHALL skip that game, write a one-line explanation to standard error, and continue processing the remaining Packages.
3. IF the Build_ID_Flag is present and a Package carries no Program_NCA or no readable Main_NSO, THEN THE System SHALL skip that game, write a one-line explanation to standard error, and continue processing the remaining Packages.
4. WHERE the Build_ID_Flag is present, THE System SHALL apply the existing exit-code contract to whatever games resolved a Build_ID after any skips (`0` for exactly one game, `3` for more than one game, `1` for a setup or input error, `2` for bad CLI arguments).
5. IF the Build_ID_Flag is present and no game resolved a Build_ID after skips, THEN THE System SHALL treat this as the existing "nothing resolved" setup error and exit with code `1`.

### Requirement 5: Backward compatibility of default behavior

**User Story:** As an existing script author, I want the default output and keys handling unchanged, so that my pipelines keep working without modification.

#### Acceptance criteria

1. WHERE the Build_ID_Flag is absent, THE System SHALL NOT decrypt any Program_NCA filesystem and SHALL NOT read the Main_NSO.
2. WHERE the Build_ID_Flag is absent, THE System SHALL resolve Title IDs through the existing ticket, filename-tag, and meta-NCA sources in their current trust order.
3. WHERE the Build_ID_Flag is absent, THE System SHALL keep the current standard-output format of one 16-uppercase-hex Title ID per line.

### Requirement 6: Testing the Build-ID feature

**User Story:** As a maintainer, I want tests that validate Build-ID resolution, so that the feature stays correct across changes and works in CI without real game dumps.

#### Acceptance criteria

1. THE System's test suite SHALL provide a synthetic builder that embeds a caller-specified Build_ID into a Package's Main_NSO so a known value can be asserted.
2. WHEN the Build_ID_Flag is exercised against a synthetic Package with a known embedded Build_ID, THE System's test suite SHALL assert the printed Build_ID equals that known value.
3. WHEN the Build_ID_Flag is exercised without usable Keys, THE System's test suite SHALL assert that the System fails up front with a setup error and exit code `1`, and SHALL assert that a game whose Build_ID cannot be resolved under the Build_ID_Flag is skipped with a standard-error message while other games still print.
4. THE System's test suite SHALL assert that default output (without the Build_ID_Flag) is unchanged, satisfying Requirement 5.
5. WHERE the real `.nsp` fixtures named `<TitleID>-<BuildID>.nsp` are present, THE System's test suite SHALL assert the printed Build_ID matches the Build_ID encoded in each fixture filename.
6. IF the real `.nsp` fixtures are absent (a fresh clone or CI), THEN THE System's test suite SHALL skip the fixture-dependent assertions cleanly using the existing `HAVE_REAL_NSPS` flag and `NO_REAL_NSPS` skip reason.
