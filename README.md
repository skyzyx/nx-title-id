# nx-title-id

Prints the title ID of the Nintendo Switch game whose packages (`.nsp`, `.nsz`, `.xci`, `.xcz`) are in a folder.

```text
uvx nx-title-id <folder> [-k prod.keys]
```

Base games, updates and DLC in the same folder all count as their base game, so a folder with one game prints one line:

```text
$ uvx nx-title-id ~/Games/Super\ Mario\ Odyssey
0100000000010000
```

## How the title ID is found

For each package, in order:

1. **The ticket's name.** A package's ticket is named after its rights ID (`<32 hex digits>.tik`), whose first 16 digits are the title ID. This wins over the file name, since tags in file names can be wrong.
2. **A `[title ID]` tag in the file name**, such as `Game [0100000000010000][v0].nsp`.
3. **The meta NCA.** For a package with neither, the NCA headers are decrypted with `header_key` from `prod.keys`, and the lowest title ID among the meta NCAs is used. The keys file is `-k`, else `~/.switch/prod.keys` when it exists; it is read only when a package needs it, and no key value is printed.

Steps 1 and 2 use only the Python standard library. Step 3 uses [pycryptodome](https://pypi.org/project/pycryptodome/) for AES.

Cartridge images (`.xci`, `.xcz`) seldom carry a ticket, so they usually need a tag or the keys. Cartridge dumps with the 0x1000-byte key area at the start, and NCA0 headers from very early titles, are not read.

## Exit status

`0` when at least one title ID was printed. Non-zero, with a message, when the path is not a folder, no package has a readable title ID, the keys file is missing or has no `header_key`. When the folder holds more than one game, every title ID is printed and a warning goes to stderr.

## Development

```text
uv run nx-title-id <folder>       # run from this folder
uv run tryke test                 # run the tests
uv build                          # sdist and wheel into dist/
uv publish                        # upload to PyPI
```
