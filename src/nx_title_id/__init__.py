"""
Print the application (base game) title ID of the Nintendo Switch game packages in a folder.

Packages: .nsp and .nsz (a PFS0 archive), .xci and .xcz (a cartridge image: the card header's root HFS0, whose "secure"
partition holds the game's files). Each package's title ID comes from, in order:

1. A ticket's name inside the package (<rights ID>.tik, whose first 16 hex
    digits are the title ID);

2. A [title ID] tag in the file name;

3. The package's meta NCAs: their headers decrypted with prod.keys header_key
    (AES-128-XTS), the lowest title ID among them (the base games, before its
    updates or DLCs).

Updates (…800) and DLC (base + 0x1000 + index) count as their base game. Key
values are never printed.
"""

import argparse
import re
import struct
import sys
from io import BufferedReader
from pathlib import Path
from typing import Any

__version__ = '1.0.0'

EXTENSIONS = ('.nsp', '.nsz', '.xci', '.xcz')
DEFAULT_KEYS: Path = Path.home() / '.switch' / 'prod.keys'
META = 1  # NCA content type: 0 program, 1 meta, 2 control, 3 manual, 4 data, 5 public data


class MalformedPackageError(Exception):
    """
    A package whose headers are truncated or inconsistent. Not a ValueError,
    so main() can skip the one package instead of treating it as a keys error.
    """


def partition(f: BufferedReader, at: int, magic: bytes, entry_size: int):
    """
    The entries of a PFS0 or HFS0 partition header at offset `at`: [(name,
    absolute data offset)], or [] when the magic is wrong. Both start with
    magic, entry count, string table size, reserved; each entry starts with data
    offset (u64), size (u64), name offset (u32).
    """
    f.seek(at)
    head: bytes = f.read(16)

    if len(head) < 16 or head[:4] != magic:
        return []

    count, strings_size = struct.unpack_from('<II', head, 4)
    entries, strings = f.read(count * entry_size), f.read(strings_size)

    if len(entries) < count * entry_size or len(strings) < strings_size:
        raise MalformedPackageError(f'truncated {magic.decode()} header at {at:#x}')

    data = at + 16 + count * entry_size + strings_size  # entry data offsets are relative to the end of the header
    out: list[Any] = []

    for i in range(count):
        offset, _size, name_at = struct.unpack_from('<QQI', entries, i * entry_size)
        end: int = strings.find(b'\0', name_at)

        if end < 0:
            raise MalformedPackageError(f'unterminated file name in {magic.decode()} header at {at:#x}')

        name: str = strings[name_at:end].decode(errors='replace')
        out.append((name, data + offset))

    return out


def package_files(f: BufferedReader):
    """
    The files inside an open package: an NSP/NSZ's PFS0, or an XCI/XCZ's
    secure partition. [(name, absolute offset)], [] when it is neither.
    """

    f.seek(0)
    head: bytes = f.read(0x140)

    if head[:4] == b'PFS0':
        return partition(f, 0, b'PFS0', 0x18)

    if head[0x100:0x104] == b'HEAD':  # card header: the root HFS0's offset at 0x130
        if len(head) < 0x138:
            raise MalformedPackageError('truncated card header')

        root = struct.unpack_from('<Q', head, 0x130)[0]

        for name, at in partition(f, root, b'HFS0', 0x40):
            if name == 'secure':
                return partition(f, at, b'HFS0', 0x40)

    return []


def is_package(path) -> bool:
    with open(path, 'rb') as f:
        head: bytes = f.read(0x104)

    return head[:4] == b'PFS0' or head[0x100:0x104] == b'HEAD'


# ---- meta NCA fallback ----
def read_header_key(keys_path) -> bytes:
    """
    header_key (32 bytes: the XTS data key, then the tweak key) from a prod.keys
    file.
    """
    for line in Path(keys_path).read_text(errors='replace').splitlines():
        m: re.Match[str] | None = re.fullmatch(r'\s*header_key\s*=\s*([0-9A-Fa-f]{64})\s*', line)

        if m:
            return bytes.fromhex(m.group(1))

    raise ValueError(f'{keys_path} has no header_key')


def xts_decrypt_sector(header_key, data: bytes, sector: int) -> bytes:
    """
    AES-128-XTS decryption of one 0x200-byte sector with Nintendo's tweak: the
    sector number in big endian (standard XTS uses little endian). pycryptodome
    has no XTS mode, so it is built on AES-ECB.
    """
    from Crypto.Cipher import (
        AES,  # imported here: packages with a ticket or a tag never need it
    )

    data_key, tweak_key = AES.new(header_key[:16], AES.MODE_ECB), AES.new(header_key[16:], AES.MODE_ECB)
    t: int = int.from_bytes(tweak_key.encrypt(sector.to_bytes(16, 'big')), 'little')
    out = bytearray()

    for i in range(0, len(data), 16):
        tb: bytes = t.to_bytes(16, 'little')
        block = bytes(a ^ b for a, b in zip(data[i : i + 16], tb))
        out += bytes(a ^ b for a, b in zip(data_key.decrypt(block), tb))
        t <<= 1  # multiply the tweak by x in GF(2^128)

        if t >> 128:
            t ^= (1 << 128) | 0x87

    return bytes(out)


def meta_title_of(path, header_key):
    """
    The lowest title ID among the package's meta NCAs, or None when it has none.
    NCA header: sector 0 holds the signatures; sector 1 (0x200) starts with the
    magic (NCA3/NCA2), then distribution type (u8), content type (u8) and, at
    0x210, the title ID (u64). An NSZ/XCZ's compressed .ncz keeps that header
    as it is.
    """
    found = None
    with open(path, 'rb') as f:
        for name, at in package_files(f):
            if not name.lower().endswith(('.nca', '.ncz')):
                continue

            f.seek(at + 0x200)
            sector: bytes = f.read(0x200)

            if len(sector) < 0x200:
                continue

            h: bytes = xts_decrypt_sector(header_key, sector, 1)

            if h[:4] not in (b'NCA3', b'NCA2') or h[5] != META:
                continue

            tid = struct.unpack_from('<Q', h, 0x10)[0]
            found = tid if found is None else min(found, tid)

    return found


def title_of(path, header_key=None):
    """
    The title ID of one package: its ticket's name, else a [title ID] tag in its
    name, else (with header_key, a function returning the key) its meta NCAs.
    None when none of them says.
    """
    with open(path, 'rb') as f:
        names: list[Any] = [name for name, _ in package_files(f)]

    for name in names:  # the ticket first: a file name's tag can be wrong
        if re.fullmatch(r'[0-9A-Fa-f]{32}\.tik', name):
            return int(name[:16], 16)

    m: re.Match[str] | None = re.search(r'\[([0-9A-Fa-f]{16})\]', path.name)

    if m:
        return int(m.group(1), 16)

    return meta_title_of(path, header_key()) if header_key else None


def application_of(tid):
    """
    The base game's title ID for a base, update or DLC title ID.
    """
    low = tid & 0xFFF

    if low == 0:
        return tid  # base game

    if low == 0x800:
        return tid & ~0xFFF  # update

    return (tid & ~0xFFF) - 0x1000  # DLC


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog='nx-title-id',
        description='Print the title ID of the game whose packages (.nsp, .nsz, .xci, .xcz) are in a folder.',
    )
    parser.add_argument('folder', type=Path, help='folder holding the game packages')
    parser.add_argument(
        '-k',
        '--keys',
        type=Path,
        help=f'''
        prod.keys, for packages with neither a ticket nor a [title ID] in their
        name (default: {DEFAULT_KEYS} when it exists)
        ''',
    )
    parser.add_argument('-V', '--version', action='version', version=f'%(prog)s {__version__}')
    args: argparse.Namespace = parser.parse_args(argv)

    if not args.folder.is_dir():
        sys.exit(f'not a folder: {args.folder}')

    keys: Path | Any | None = args.keys or (DEFAULT_KEYS if DEFAULT_KEYS.is_file() else None)

    if keys and not keys.is_file():
        sys.exit(f'no such keys file: {keys}')

    cache: list[Any] = []

    def header_key():  # read once, and only when a package needs the meta NCA
        if not cache:
            cache.append(read_header_key(keys))

        return cache[0]

    apps: set[Any] = set()

    for pkg in sorted(p for p in args.folder.iterdir() if p.suffix.lower() in EXTENSIONS and p.is_file()):
        if pkg.name.startswith('._') or pkg.stat().st_size == 0:  # macOS AppleDouble files, empty downloads
            continue

        if not is_package(pkg):  # without this, any file with a package's extension and a tag would count
            print(f'skipping {pkg.name}: neither a PFS0 (NSP / NSZ) nor a cartridge image (XCI / XCZ)', file=sys.stderr)
            continue

        try:
            tid = title_of(pkg, header_key if keys else None)
        except MalformedPackageError as e:  # one bad package shouldn't stop the others
            print(f'skipping {pkg.name}: {e}', file=sys.stderr)
            continue
        except ValueError as e:  # prod.keys without a header_key
            sys.exit(str(e))

        if tid is None:
            why = (
                'no ticket, no [title ID] in its name and no readable meta NCA'
                if keys
                else 'no ticket and no [title ID] in its name (give -k prod.keys to read its meta NCA)'
            )
            print(f'skipping {pkg.name}: {why}', file=sys.stderr)
            continue

        apps.add(application_of(tid))

    if not apps:
        sys.exit(f'no package with a readable title ID in {args.folder}')

    if len(apps) > 1:
        print('more than one game in the folder:', file=sys.stderr)

    for app in sorted(apps):
        print(f'{app:016X}')
