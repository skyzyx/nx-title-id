"""
Print the application (base game) title ID of the Nintendo Switch game packages in a folder.

A game's base, its updates, and its DLC ship as separate packages that a user
keeps together, so the one fact worth printing is the base game they all belong
to. Resolution tries the cheapest, most trustworthy source first and only falls
back to decryption when nothing else answers:

1. A ticket's name inside the package (<rights ID>.tik, whose first 16 hex
    digits are the title ID). This comes from the package's own contents, so
    it is trusted over anything in the file name;

2. A [title ID] tag in the file name, which is user-editable and can be wrong,
    so it is only consulted when there is no ticket;

3. The package's meta NCAs, whose headers are decrypted with the prod.keys
    header_key (AES-128-XTS). This is last because it needs user-supplied keys
    and real crypto, which most packages let us avoid.

Every source yields a set, so a single multi-title cartridge contributes every
game it carries. Updates (…800) and DLC (base + 0x1000 + index) are folded to
their base game, so one game still prints one line. Key values are never
printed, because the point of the tool is to read IDs, not expose secrets.
"""

import argparse
import re
import struct
import sys
from collections.abc import Callable
from io import BufferedReader
from pathlib import Path

__version__ = '1.0.0'

EXTENSIONS = ('.nsp', '.nsz', '.xci', '.xcz')
DEFAULT_KEYS: Path = Path.home() / '.switch' / 'prod.keys'
META = 1  # the meta NCA is the only content type we read, so name just that one

# A real package holds about a dozen files in a few kilobytes of header, so any
# header declaring far more than that is corrupt or hostile. Capping the
# declared sizes before we act on them stops a bad header from making us
# allocate and read up to the whole (possibly multi-gigabyte) file into memory.
# 0x10000 sits just above the 0xFFFF that the malformed-package tests declare,
# so those still reach the truncated-read path rather than tripping this guard.
MAX_ENTRIES = 0x10000
MAX_STRINGS_SIZE = 0x100000  # 1 MiB, generous for a name table that is normally tens of bytes


class MalformedPackageError(Exception):
    """
    Raised when a package's headers are truncated or inconsistent. It is
    deliberately not a ValueError so that main() can tell a single bad package
    (skip it, keep going) apart from a bad keys file (a problem with the run
    itself), which is signalled with ValueError.
    """


def partition(f: BufferedReader, at: int, magic: bytes, entry_size: int) -> list[tuple[str, int]]:
    """
    Parse a PFS0 or HFS0 partition header at offset `at` into [(name, absolute
    data offset)]. NSP and XCI containers are both just these partitions with
    different magic and entry sizes, so one parser serves both. A wrong or
    short magic returns [] rather than raising, because "this isn't the
    partition we expected" is a normal, recoverable outcome for a caller that
    is probing a container.
    """
    f.seek(at)
    head: bytes = f.read(16)

    # Too short to be a header, or the wrong container: not an error, just
    # nothing here for the caller to use.
    if len(head) < 16 or head[:4] != magic:
        return []

    count, strings_size = struct.unpack_from('<II', head, 4)

    # Reject absurd declared sizes before the reads below, so a corrupt header
    # can never drive a huge allocation. The message names the limit so a
    # report reads as a sanity failure, not a generic truncation.
    if count > MAX_ENTRIES:
        raise MalformedPackageError(
            f'{magic.decode()} header at {at:#x} declares {count} entries (limit {MAX_ENTRIES})'
        )

    if strings_size > MAX_STRINGS_SIZE:
        raise MalformedPackageError(
            f'{magic.decode()} header at {at:#x} declares a {strings_size}-byte string table (limit {MAX_STRINGS_SIZE})'
        )

    entries, strings = f.read(count * entry_size), f.read(strings_size)

    # A header that promises more than the file delivers is corrupt; report it
    # so the caller skips this one package instead of silently losing entries.
    if len(entries) < count * entry_size or len(strings) < strings_size:
        raise MalformedPackageError(f'truncated {magic.decode()} header at {at:#x}')

    # Entry data offsets in the format are relative to the end of the header, so
    # fold in that base once here and hand callers absolute offsets they can
    # seek to directly.
    data = at + 16 + count * entry_size + strings_size
    out: list[tuple[str, int]] = []

    for i in range(count):
        offset, _size, name_at = struct.unpack_from('<QQI', entries, i * entry_size)
        end: int = strings.find(b'\0', name_at)

        # A name offset with no terminating NUL means the string table is
        # malformed; a real name is always NUL-terminated.
        if end < 0:
            raise MalformedPackageError(f'unterminated file name in {magic.decode()} header at {at:#x}')

        # Decode leniently: a stray byte in a file name should not sink the
        # whole package when we only compare names against known suffixes.
        name: str = strings[name_at:end].decode(errors='replace')
        out.append((name, data + offset))

    return out


def package_files(f: BufferedReader) -> list[tuple[str, int]]:
    """
    List the files in an open package, hiding the difference between the two
    container shapes from callers. An NSP/NSZ is a single PFS0 at offset 0; an
    XCI/XCZ is a card image whose game files live in a "secure" partition
    nested inside its root HFS0. Anything else returns [] so a mislabeled file
    resolves to no title ID rather than an error.
    """

    f.seek(0)
    head: bytes = f.read(0x140)

    if head[:4] == b'PFS0':
        return partition(f, 0, b'PFS0', 0x18)

    # A card image identifies itself by "HEAD" at 0x100 and stores the offset of
    # its root HFS0 at 0x130.
    if head[0x100:0x104] == b'HEAD':
        # The offset we need lives at 0x130, so a header that stops short of
        # 0x138 cannot be trusted to point anywhere valid.
        if len(head) < 0x138:
            raise MalformedPackageError('truncated card header')

        root: int = struct.unpack_from('<Q', head, 0x130)[0]

        # The root HFS0 lists several partitions; only "secure" holds the game
        # files, so walk to it and parse that nested partition.
        for name, at in partition(f, root, b'HFS0', 0x40):
            if name == 'secure':
                return partition(f, at, b'HFS0', 0x40)

    return []


def is_package(path: Path) -> bool:
    """
    A cheap magic-byte check used to gate the real work. The folder scan trusts
    a file's extension only so far; checking the magic first stops a stray file
    that merely has a package extension (and perhaps a [title ID] tag) from
    being mined for a bogus ID.
    """
    with open(path, 'rb') as f:
        head: bytes = f.read(0x104)

    return head[:4] == b'PFS0' or head[0x100:0x104] == b'HEAD'


# The functions below are the meta NCA fallback: only reached when a package
# offers neither a ticket nor a tag, because they are the only ones that need
# the user's keys and decryption.
def read_header_key(keys_path: Path) -> bytes:
    """
    Pull the 32-byte header_key (the XTS data key followed by the tweak key) out
    of a prod.keys file. The file holds many keys in `name = hex` lines and only
    this one is relevant, so scan for it by name and ignore the rest. Absence is
    a ValueError (a problem with the run's keys) rather than a
    MalformedPackageError (a problem with one package).
    """
    for line in Path(keys_path).read_text(errors='replace').splitlines():
        # Tolerate the spacing and letter case that real prod.keys files vary
        # in, so a valid file is not rejected over cosmetics.
        m: re.Match[str] | None = re.fullmatch(r'\s*header_key\s*=\s*([0-9A-Fa-f]{64})\s*', line)

        if m:
            return bytes.fromhex(m.group(1))

    raise ValueError(f'{keys_path} has no header_key')


def xts_decrypt_sector(header_key: bytes, data: bytes, sector: int) -> bytes:
    """
    Decrypt one 0x200-byte NCA header sector. This is hand-rolled on AES-ECB
    because pycryptodome ships no XTS mode, and because Nintendo feeds the
    sector number to the tweak in big-endian order where standard XTS uses
    little-endian, so a stock XTS routine would not match these headers anyway.
    """

    # Deferred on purpose, against the project's imports-are-global rule:
    # packages resolved by a ticket or a tag never reach this function, so most
    # runs should not pay to load pycryptodome at all.
    from Crypto.Cipher import AES

    data_key, tweak_key = AES.new(header_key[:16], AES.MODE_ECB), AES.new(header_key[16:], AES.MODE_ECB)
    # Nintendo's big-endian sector tweak, encrypted once, then advanced per
    # block below. Held as an int so the GF(2^128) multiply is a plain shift.
    t: int = int.from_bytes(tweak_key.encrypt(sector.to_bytes(16, 'big')), 'little')
    out = bytearray()

    for i in range(0, len(data), 16):
        tb: bytes = t.to_bytes(16, 'little')
        # XTS sandwiches the ECB decrypt between two XORs with the tweak.
        block = bytes(a ^ b for a, b in zip(data[i : i + 16], tb))
        out += bytes(a ^ b for a, b in zip(data_key.decrypt(block), tb))
        t <<= 1  # advance the tweak by multiplying by x in GF(2^128)

        # A shift that overflows 128 bits has to be reduced by the field's
        # modulus; 0x87 is the low byte of that polynomial for AES-XTS.
        if t >> 128:
            t ^= (1 << 128) | 0x87

    return bytes(out)


def meta_title_of(path: Path, header_key: bytes) -> set[int]:
    """
    Decrypt every meta NCA header in the package and return the set of title IDs
    they carry. Every one is kept, not just the lowest, so a single package that
    bundles more than one base game still reports all of them; application_of()
    later folds a single game's base/update/DLC entries back together. The NCA
    header layout: sector 0 is signatures, sector 1 (at 0x200) starts with the
    magic (NCA3/NCA2), then distribution type (u8) and content type (u8), with
    the title ID as a u64 at 0x210. An NSZ/XCZ's compressed .ncz keeps this
    header in the clear, so the same read works for it.
    """
    found: set[int] = set()

    with open(path, 'rb') as f:
        for name, at in package_files(f):
            # Only NCA/NCZ entries carry the headers we can read; skip the rest
            # without paying for a decrypt.
            if not name.lower().endswith(('.nca', '.ncz')):
                continue

            f.seek(at + 0x200)
            sector: bytes = f.read(0x200)

            # A header shorter than a full sector cannot be decrypted; move on.
            if len(sector) < 0x200:
                continue

            h: bytes = xts_decrypt_sector(header_key, sector, 1)

            # Wrong magic means either the wrong key or an unsupported NCA
            # version; a non-meta content type is simply not what we want. Both
            # are skipped so a bad key yields no IDs instead of garbage.
            if h[:4] not in (b'NCA3', b'NCA2') or h[5] != META:
                continue

            tid = struct.unpack_from('<Q', h, 0x10)[0]
            found.add(tid)

    return found


def title_ids_of(path: Path, header_key: Callable[[], bytes | None] | None = None) -> set[int]:
    """
    Resolve every title ID a package declares, trying the sources in trust
    order and stopping at the first that answers. Returning a set (rather than a
    single winner) is what lets a multi-title cartridge contribute every game it
    holds. `header_key` is a callable, not the key itself, so the keys file is
    only touched when the meta NCA fallback is actually reached; it returns None
    once that fallback is known to be unavailable (no key in the file), in which
    case this returns an empty set.
    """
    with open(path, 'rb') as f:
        names: list[str] = [name for name, _ in package_files(f)]

    # Tickets come from the package's own contents, so they are trusted over the
    # file name. Collect all of them so a cartridge bundling several games is
    # not reduced to its first ticket.
    tickets: set[int] = {int(name[:16], 16) for name in names if re.fullmatch(r'[0-9A-Fa-f]{32}\.tik', name)}

    # Returning here short-circuits the tag and the meta NCA, which is also what
    # keeps a ticket-resolved package from ever loading the crypto dependency.
    if tickets:
        return tickets

    # A [title ID] tag in the name is next: weaker than a ticket, but it needs
    # no keys, so prefer it over decryption.
    m: re.Match[str] | None = re.search(r'\[([0-9A-Fa-f]{16})\]', path.name)

    if m:
        return {int(m.group(1), 16)}

    # No callable means the caller has no keys to offer, so the meta NCA
    # fallback is off the table.
    if not header_key:
        return set()

    # Only now is the keys file consulted, and only if it actually yielded a key.
    key: bytes | None = header_key()

    return meta_title_of(path, key) if key else set()


def application_of(tid: int) -> int:
    """
    Fold a base, update, or DLC title ID to the base game it belongs to, which
    is the single answer a user wants per game. The low 12 bits encode the role:
    0 is the base game, 0x800 is the update, and anything else is a DLC index.
    Real DLC indexes run 1 to 2000 (0x7D0), so a value of 0x800 is unambiguously
    an update and never collides with a DLC.
    """
    low = tid & 0xFFF

    if low == 0:
        return tid  # already a base game

    if low == 0x800:
        return tid & ~0xFFF  # an update shares the base's high bits

    return (tid & ~0xFFF) - 0x1000  # a DLC sits one base-unit above its game


def main(argv: list[str] | None = None) -> None:
    """
    The CLI: scan a folder, resolve each package, and print one line per base
    game. The exit code is meaningful on its own so scripts can branch without
    parsing stderr: 0 for exactly one game, 3 for several, and 1 for a setup or
    input problem.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
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

    # An explicit -k wins; otherwise fall back to the standard location only if
    # it is actually there, so a machine without prod.keys simply has no meta
    # NCA fallback rather than an error.
    keys: Path | None = args.keys or (DEFAULT_KEYS if DEFAULT_KEYS.is_file() else None)

    # A -k path the user named but that does not exist is a mistake worth
    # reporting up front, even if no package would have needed it.
    if keys and not keys.is_file():
        sys.exit(f'no such keys file: {keys}')

    cache: list[bytes | None] = []  # empty until read, then holds [key] or [None]

    def header_key() -> bytes | None:
        """
        Read prod.keys at most once, and only when a package first needs the
        meta NCA fallback. The result is cached so a missing header_key warns a
        single time and an unreadable file is reported once, rather than once
        per package. Only called when `keys` is set.
        """

        if cache:
            return cache[0]

        # Unreachable at runtime (the call site guards on `keys`), but the
        # explicit branch is what lets the type checker see `keys` as a Path
        # below without an unsound annotation.
        if keys is None:
            cache.append(None)

            return None

        try:
            cache.append(read_header_key(keys))

        # A keys file that lacks header_key is not fatal: warn once, cache the
        # miss as None, and let packages that need it be skipped while the rest
        # still resolve.
        except ValueError as e:
            print(f'warning: {e}; skipping packages that need it', file=sys.stderr)
            cache.append(None)

        # An unreadable keys file is a genuine setup error, so fail the run with
        # a one-line message instead of letting a raw OSError traceback escape.
        except OSError as e:
            sys.exit(f'cannot read keys file {keys}: {e.strerror or e}')

        return cache[0]

    apps: set[int] = set()

    # Sorted so stdout and the skip messages are deterministic; is_file()
    # follows symlinks on purpose, so a symlinked package counts.
    for pkg in sorted(p for p in args.folder.iterdir() if p.suffix.lower() in EXTENSIONS and p.is_file()):
        # macOS writes "._name" AppleDouble siblings next to real files; they
        # are never packages, so skip them silently rather than reporting them.
        if pkg.name.startswith('._'):
            continue

        # One try covers stat, the magic check, and resolution so that any
        # filesystem or format problem with a single package is a skip, never a
        # crash that loses the packages already resolved.
        try:
            # A zero-byte file is a half-finished download, not a package worth
            # a diagnostic.
            if pkg.stat().st_size == 0:
                continue

            # Gate on the magic before trusting the extension, so a renamed
            # non-package cannot produce a bogus ID from its tag.
            if not is_package(pkg):
                print(
                    f'skipping {pkg.name}: neither a PFS0 (NSP / NSZ) nor a cartridge image (XCI / XCZ)',
                    file=sys.stderr,
                )

                continue

            # Pass the key closure only when keys exist, so a keyless run never
            # even tries the meta NCA fallback.
            tids: set[int] = title_ids_of(pkg, header_key if keys else None)

        # A corrupt header is this package's problem alone; name it and move on.
        except MalformedPackageError as e:
            print(f'skipping {pkg.name}: {e}', file=sys.stderr)

            continue

        # Permission denied or a vanished file: same policy, skip just this one.
        except OSError as e:
            print(f'skipping {pkg.name}: {e.strerror or e}', file=sys.stderr)

            continue

        # No ID resolved. The reason depends on why, so the message tells the
        # user what would have helped rather than a bare "skipped".
        if not tids:
            why: str

            if not keys:
                why = 'no ticket and no [title ID] in its name (give -k prod.keys to read its meta NCA)'

            # cache == [None] means the keys file was read but had no
            # header_key, so point at that specific cause.
            elif cache == [None]:
                why = f'no ticket, no [title ID] in its name, and {keys} is missing header_key'

            else:
                why = 'no ticket, no [title ID] in its name and no readable meta NCA'

            print(f'skipping {pkg.name}: {why}', file=sys.stderr)

            continue

        # Fold every resolved ID to its base game; the set collapses a game's
        # base/update/DLC and dedupes packages that point at the same game.
        apps.update(application_of(t) for t in tids)

    # Nothing resolved anywhere is a failed run, not an empty-but-fine one.
    if not apps:
        sys.exit(f'no package with a readable title ID in {args.folder}')

    # Warn before the IDs so a human sees the heads-up first, while stdout still
    # carries only the clean list for a consuming script.
    if len(apps) > 1:
        print('more than one game in the folder:', file=sys.stderr)

    for app in sorted(apps):
        print(f'{app:016X}')

    # Several games is a distinct outcome, not a failure: print them all, then
    # signal it with exit 3 so a caller can tell it from the single-game case.
    if len(apps) > 1:
        sys.exit(3)
