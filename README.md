# nx-title-id

Given a folder containing Nintendo Switch game packages (`.nsp`, `.nsz`, `.xci`, or `.xcz` files), this will look up the Title ID and, optionally, the Build ID.

If you keep a library of dumped games, you already know the problem: a single game is scattered across a base `.nsp`, an update, and a pile of DLC, each with a cryptic file name. `nx-title-id` reads the packages themselves and prints the base-game title ID they all belong to, so you can rename, sort, de-duplicate, or match against a database without opening a single file by hand.

It is fast and quiet: it reads only small parts of each package (the headers, plus the game's executable if you ask for a Build ID) and never the multi-gigabyte body, it prints clean title IDs to `stdout` and everything else to `stderr` so it drops straight into a shell pipeline, and it never needs your keys unless a package gives it no other way to answer (although keys are definitely _recommended_).

## Prerequisites

> [!IMPORTANT]
> Zero Nintendo-copyrighted content is in this repository. You must provide your own dumps.

* A package to inspect: `.nsp` or `.nsz` (an NSP archive), or `.xci` or `.xcz` (a cartridge image).
* Python 3.14 or newer, if you install from source.
* Optionally (but _recommended_), a `prod.keys` file. It is only needed for packages that carry neither a ticket nor a `[title ID]` tag in their name, where the title ID has to be read from the encrypted meta NCA. It is **required** for `-b`/`--build-id` (see [Build ID](#build-id)). Key values are never printed.

## Installation

Run it without installing anything permanently:

```text
uvx nx-title-id <folder>
```

Or install it as a tool on your `$PATH`:

```text
uv tool install nx-title-id
```

## Usage

Point it at a folder. A folder holding one game (its base, update, and DLC) prints one line:

```text
$ uvx nx-title-id "~/Games/League Of Evil"
01009C100390E000
```

Give it the keys file when a package needs the meta NCA fallback.

```text
$ uvx nx-title-id "~/Games/League Of Evil" -k "/path/to/prod.keys"
01009C100390E000
```

When `-k` is omitted, `~/.switch/prod.keys` (or `%USERPROFILE%\.switch\prod.keys`) is used automatically if it exists.

A folder with more than one game prints every title ID (sorted), warns on `stderr`, and exits `3`, so a script can tell _one game_ from _several_ by the exit code alone:

```text
$ uvx nx-title-id ~/Games/Bundle
more than one game in the folder:
0100000000010000
0100000000020000
```

### Build ID

Add `-b` (or `--build-id`) to print each game's Build ID after its title ID, separated by one space:

```text
$ uvx nx-title-id "~/Games/League Of Evil" -b
01009C100390E000 1816F8A3E4F09B33
```

The Build ID is the first 8 bytes of the main NSO's ModuleId, as 16 uppercase hex digits. It is the form nxdumptool and Atmosphere call the Build ID. Without `-b`, the output is unchanged.

It is opt-in because it is the expensive path: the tool has to decrypt the game's program NCA down to its `main` executable (it reads that section, not the whole package), so `-b` always needs `prod.keys`:

* `header_key` is required up front. If it is missing, the run exits `1` with a one-line error before reading any package.

* `key_area_key_application_XX` and/or `titlekek_XX` are needed per game. The `titlekek_XX` is for retail titlekey (rights ID) programs, which are read through the package's own common-type `.tik`.

* A game that cannot be resolved is skipped with a one-line message on `stderr`, and the rest still print. That covers a game known only from a ticket or tag (no program NCA), a missing key, a missing ticket, or a personalized ticket.

The Build ID comes from the newest update package of the base game (the greatest readable update version; any update beats the base when versions are unreadable), otherwise from the base package. DLC is never the source.

Exit codes are the same as below, decided on the games actually printed: `0` for one game, `3` for several, `1` when nothing resolved. Key values are never printed.

### How the title ID is found

For each package, in order:

1. **The ticket's name.** A package's ticket is named after its rights ID (`<32 hex digits>.tik`), whose first 16 digits are the title ID. This wins over the file name, since tags in file names can be wrong.

2. **A `[title ID]` tag in the file name**, such as `Game [0100000000010000][v0].nsp`.

3. **The meta NCA.** For a package with neither, the NCA headers are decrypted with `header_key` from `prod.keys`, and every meta NCA title ID is read.

Steps 1 and 2 use only the Python standard library. Step 3 uses [pycryptodome](https://pypi.org/project/pycryptodome/) for AES.

Base games, updates, and DLC all fold to their base game, so a folder with one game prints one line. A single cartridge that holds several games contributes every one of them.

Cartridge images (`.xci`, `.xcz`) seldom carry a ticket, so they usually need a tag or the keys. Cartridge dumps with the `0x1000`-byte key area at the start, and `NCA0` headers from very early titles, are not read.

### Exit status

| Code | Meaning                                                                                                  |
|------|----------------------------------------------------------------------------------------------------------|
| `0`  | One game: at least one title ID was printed and they all fold to a single base game.                     |
| `1`  | The path is not a folder, the keys file is missing or unreadable, or no package has a readable title ID. |
|      | With `-b`: no usable `prod.keys` (or no `header_key`), or no game has a readable Build ID.               |
| `2`  | Bad command-line arguments.                                                                              |
| `3`  | More than one game: every title ID is printed and a warning goes to stderr.                              |

A keys file without `header_key` is a warning, not an error: packages that need the meta NCA fallback are skipped, and the rest still print.

Exit code `3` also covers a single multi-title cartridge that holds more than one base game (e.g., _Final Fantasy I-VI Pixel Remaster_, _Super Mario Galaxy 1 & 2_). You should extract each game into its own `.nsp` with `nstool`, `4NXCI`, or similar.

## Troubleshooting

### A package is skipped for lack of a title ID

The message is `skipping <file>: no ticket and no [title ID] in its name (give -k prod.keys to read its meta NCA)`. The package has no ticket and no tag, so it needs the keys. Pass `-k path/to/prod.keys`, or put the file at `~/.switch/prod.keys`.

### A warning about header_key

The message is `warning: <keys> has no header_key; skipping packages that need it`. The keys file is missing the `header_key` entry. Packages resolvable by ticket or tag still print; only ones that need decryption are skipped. With `-b` this is fatal instead (see below).

You can re-dump your latest keys for your firmware with `lockpick` (a.k.a., `lockpick_rcm`).

### “cannot read keys file <path>”

The keys file exists but could not be read (for example, permission denied). Check its permissions.

### -b needs prod.keys

The error is `-b/--build-id needs prod.keys; pass -k or install one at <path>`. `-b` needs your keys and none were found. Pass `-k path/to/prod.keys`, or put the file at the path shown (`~/.switch/prod.keys` by default). The run exits `1` before reading any package.

### -b needs a header_key

The error is `-b/--build-id needs a header_key in <keys>`. The keys file `-b` found has no `header_key` line, so no program NCA can be decrypted. You will see the `warning: … has no header_key` line first. Re-dump your keys with `lockpick` (a.k.a., `lockpick_rcm`). The run exits `1` before reading any package.

### A game is skipped under -b

The message is `skipping game <title ID>: no Build ID (no readable program NCA, ticket, or key in <keys>)`. That game has no Build ID that could be read, and the other games still print. Common causes:

* The game is known only from a ticket or a `[title ID]` tag, and none of its packages holds a program NCA.
* The keys file lacks the `key_area_key_application_XX` or `titlekek_XX` entry for that game's key generation. Re-dump your keys with `lockpick` (a.k.a., `lockpick_rcm`).
* A retail game's package has no `.tik` ticket, or its ticket is personalized (tied to one console), which is not supported.
* The program NCA uses an encryption type the tool does not handle.

### A game is skipped with an error under -b

The message is `skipping game <title ID>: <error>`. One of the game's packages is corrupt, truncated, or unreadable (for example, permission denied) when the program NCA was read. The error text says which. The other games still print.

### Every game is skipped under -b

The error is `no game with a readable Build ID in <folder>`, with exit `1`. Every game was skipped under `-b`, so nothing was printed. Read the `skipping game …` lines above it for the causes.

### “skipping <file>: ...”

The file is not a valid package, is a cartridge dump with the leading key area, or uses an `NCA0` header. These layouts are not supported.

### No output and exit `1`

Nothing in the folder resolved to a Title ID. Confirm the folder holds real packages, and supply keys if the packages lack tickets and tags.

### Several IDs when you expected one

The folder (or a single cartridge) genuinely holds more than one base game; the tool exits `3` in that case.

## Running the tests

The suite runs with [Tryke](https://tryke.dev), a Python test runner written in Rust:

```text
uv run tryke test
```

How it works:

* Most tests build **synthetic** packages in memory (fake `PFS0`/`HFS0` containers, `NCA` headers, program NCAs with an `exefs` and a `main` executable, and tickets), so the suite needs no real game files and no real keys to pass.

* The hand-rolled AES-XTS routine, and the AES-CTR and AES-ECB steps that `-b` uses, are checked against an independent reference implementation (the `cryptography` package), so a decryption bug cannot hide behind the tool's own code.

* Five property-style tests (seeded random loops, not a property library) cover the Build ID: the ModuleId round trip, the first-8-bytes rendering, newest-update selection, DLC exclusion, and unchanged output without `-b`.

* Tests that require **local-only assets** (real dumped NSPs, a real cartridge image, or your real `~/.switch/prod.keys`) skip themselves cleanly when those assets are absent, so a fresh clone still gets a green run. They run automatically when the assets are present.

  * These files are named `<title-id>-<build-id>.nsp` (for example `01009C100390E000-1816F8A3E4F09B33.nsp`), so that the title ID and the `-b` Build ID are each evaluated against the values in the name; or `ff.xci` for the multi-title _Final Fantasy I-VI Pixel Remaster_. The `-b` tests also need your real `~/.switch/prod.keys`.

* The CLI is exercised end-to-end in a subprocess with an isolated `HOME`, so a run can never read your real keys or write into the repository.

## License

MIT. See [LICENSE](LICENSE).
