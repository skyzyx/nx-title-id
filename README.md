# nx-title-id

Given a folder containing `.nsp` or `.xci` files, this will lookup the Title ID.

If you keep a library of dumped games, you already know the problem: a single game is scattered across a base `.nsp`, an update, and a pile of DLC, each with a cryptic file name. `nx-title-id` reads the packages themselves and prints the base-game title ID they all belong to, so you can rename, sort, de-duplicate, or match against a database without opening a single file by hand.

It is fast and quiet: it reads only the headers (never the multi-gigabyte body), it prints clean title IDs to `stdout` and everything else to `stderr` so it drops straight into a shell pipeline, and it never needs your keys unless a package gives it no other way to answer (although keys are definitely _recommended_).

## Prerequisites

> [!IMPORTANT]
> Zero Nintendo-copyrighted content is in this repository. You must provide your own dumps.

* A package to inspect: `.nsp` or `.nsz` (an NSP archive), or `.xci` or `.xcz` (a cartridge image).
* Python 3.14 or newer, if you install from source.
* Optionally (but _recommended_), a `prod.keys` file. It is only needed for packages that carry neither a ticket nor a `[title ID]` tag in their name, where the title ID has to be read from the encrypted meta NCA. Key values are never printed.

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
| `2`  | Bad command-line arguments.                                                                              |
| `3`  | More than one game: every title ID is printed and a warning goes to stderr.                              |

A keys file without `header_key` is a warning, not an error: packages that need the meta NCA fallback are skipped, and the rest still print.

Exit code `3` also covers a single multi-title cartridge that holds more than one base game (e.g., _Final Fantasy I-VI Pixel Remaster_, _Super Mario Galaxy 1 & 2_). You should extract each game into its own `.nsp` with `nstool`, `4NXCI`, or similar.

## Troubleshooting

### “skipping <file>: no ticket and no [title ID] in its name (give -k prod.keys to read its meta NCA)”

The package has no ticket and no tag, so it needs the keys. Pass `-k path/to/prod.keys`, or put the file at `~/.switch/prod.keys`.

### “warning: <keys> has no header_key; skipping packages that need it”

The keys file is missing the `header_key` entry. Packages resolvable by ticket or tag still print; only ones that need decryption are skipped.

You can re-dump your latest keys for your firmware with `lockpick` (a.k.a., `lockpick_rcm`).

### “cannot read keys file <path>”

The keys file exists but could not be read (for example, permission denied). Check its permissions.

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

* Most tests build **synthetic** packages in memory (fake `PFS0`/`HFS0` containers and `NCA` headers), so the suite needs no real game files and no real keys to pass.

* The hand-rolled AES-XTS routine is checked against an independent reference implementation (the `cryptography` package), so a decryption bug cannot hide behind the tool's own code.

* Tests that require **local-only assets** (real dumped NSPs, a real cartridge image, or your real `~/.switch/prod.keys`) skip themselves cleanly when those assets are absent, so a fresh clone still gets a green run. They run automatically when the assets are present.

  * These files are named `<title-id>.nsp` so that the file is evaluated against the value in its name; or `ff.xci` for the multi-title _Final Fantasy I-VI Pixel Remaster_.

* The CLI is exercised end-to-end in a subprocess with an isolated `HOME`, so a run can never read your real keys or write into the repository.

## License

MIT. See [LICENSE](LICENSE).
