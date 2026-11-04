"""
Builders for synthetic Nintendo Switch packages, so the tests need no real game
files or real keys. Layouts mirror what nx_title_id parses: PFS0 (NSP/NSZ), an
XCI card header with a root HFS0 holding a "secure" HFS0, and NCA headers
encrypted with AES-128-XTS using Nintendo's big-endian sector tweak.
"""

import struct
import subprocess
import sys
from dataclasses import dataclass
from io import BufferedReader, BytesIO
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# ------------------------------------------------------------------------------
# CONSTANTS

# A made-up 32-byte header_key: the XTS data key, then the tweak key.
HEADER_KEY: bytes = bytes(range(32))

BASE: int = 0x0100000000010000
UPDATE: int = BASE + 0x800
DLC_1: int = BASE + 0x1000 + 1
OTHER_BASE: int = 0x0100000000020000

PFS0_ENTRY_SIZE: int = 0x18
HFS0_ENTRY_SIZE: int = 0x40
CARD_HEADER_SIZE: int = 0x200
SECTOR: int = 0x200

# NCA content types, as in nx_title_id.META.
PROGRAM: int = 0
META: int = 1

# Real base-game NSPs named <title ID>.nsp, kept out of git by tests/.gitignore,
# so tests using them skip when they're absent (a fresh clone, CI).
TESTS_DIR: Path = Path(__file__).parent
REAL_TITLE_IDS: tuple[int, ...] = (0x01004B800AF5A000, 0x01009C100390E000, 0x0100D9B00B666000)
HAVE_REAL_NSPS: bool = all((TESTS_DIR / f'{tid:016X}.nsp').is_file() for tid in REAL_TITLE_IDS)
NO_REAL_NSPS: str = 'the real .nsp files are not in tests/'

# The user's real keys; only read by tests that skip without them.
REAL_KEYS: Path = Path.home() / '.switch' / 'prod.keys'
HAVE_REAL_KEYS: bool = REAL_KEYS.is_file()

# A real multi-title collection cartridge, gitignored like the real NSPs, so
# the tests using it skip when it's absent (a fresh clone, CI).
HAVE_FF_XCI: bool = (TESTS_DIR / 'ff.xci').is_file()
NO_FF_XCI: str = 'tests/ff.xci is not present'

# ------------------------------------------------------------------------------
# CLASSES


@dataclass
class Run:
    """
    The result of running the CLI in a subprocess.
    """

    code: int
    stdout: str
    stderr: str


# ------------------------------------------------------------------------------
# PRIVATE FUNCTIONS


def _strings_and_offsets(names: list[str]) -> tuple[bytes, list[int]]:
    """
    A NUL-terminated string table and the offset of each name in it.
    """
    table = bytearray()
    offsets: list[int] = []

    for name in names:
        offsets.append(len(table))
        table += name.encode() + b'\0'

    return bytes(table), offsets


def _partition(magic: bytes, entry_size: int, files: list[tuple[str, bytes]]) -> bytes:
    """
    A PFS0 or HFS0 partition: header, entries, string table, then file data.
    """
    strings, name_offsets = _strings_and_offsets([name for name, _ in files])
    entries = bytearray()
    data = bytearray()

    for (_, body), name_at in zip(files, name_offsets, strict=True):
        entry: bytes = struct.pack('<QQI', len(data), len(body), name_at)
        entries += entry.ljust(entry_size, b'\0')
        data += body

    head: bytes = magic + struct.pack('<III', len(files), len(strings), 0)

    return head + bytes(entries) + strings + bytes(data)


# ------------------------------------------------------------------------------
# PUBLIC FUNCTIONS


def pfs0(files: list[tuple[str, bytes]]) -> bytes:
    """
    An NSP/NSZ: a PFS0 partition holding `files`.
    """
    return _partition(b'PFS0', PFS0_ENTRY_SIZE, files)


def hfs0(files: list[tuple[str, bytes]]) -> bytes:
    """
    An HFS0 partition holding `files`.
    """
    return _partition(b'HFS0', HFS0_ENTRY_SIZE, files)


def xci(secure_files: list[tuple[str, bytes]], partition_name: str = 'secure') -> bytes:
    """
    An XCI/XCZ: a card header whose root HFS0 (right after the header) holds
    an "update" partition and a partition named `partition_name` with
    `secure_files`.
    """
    root: bytes = hfs0([('update', hfs0([])), (partition_name, hfs0(secure_files))])
    header = bytearray(CARD_HEADER_SIZE)
    header[0x100:0x104] = b'HEAD'
    struct.pack_into('<Q', header, 0x130, CARD_HEADER_SIZE)

    return bytes(header) + root


def real_nsp(title_id: int) -> Path:
    """
    The real NSP for `title_id` in tests/.
    """
    return TESTS_DIR / f'{title_id:016X}.nsp'


def reader(data: bytes) -> BufferedReader:
    """
    An in-memory file, the same type open(path, 'rb') returns.
    """
    return BufferedReader(BytesIO(data))


def nintendo_xts_encrypt(key: bytes, data: bytes, sector: int) -> bytes:
    """
    AES-128-XTS encryption of one sector with a big-endian sector tweak, using
    the cryptography package as an independent reference implementation.
    """
    encryptor = Cipher(algorithms.AES(key), modes.XTS(sector.to_bytes(16, 'big'))).encryptor()

    return encryptor.update(data) + encryptor.finalize()


def nca_header(title_id: int, content_type: int, magic: bytes = b'NCA3') -> bytes:
    """
    The first two sectors of an NCA: sector 0 (signatures, zeros here), then
    sector 1 with the magic, distribution type, content type and title ID,
    encrypted with HEADER_KEY.
    """
    plain = bytearray(SECTOR)
    plain[0:4] = magic
    plain[5] = content_type
    struct.pack_into('<Q', plain, 0x10, title_id)

    return bytes(SECTOR) + nintendo_xts_encrypt(HEADER_KEY, bytes(plain), 1)


def ticket_name(title_id: int) -> str:
    """
    A ticket's file name: the 32-digit rights ID (title ID, then key generation).
    """
    return f'{title_id:016x}{0:016x}.tik'


def write_keys(path: Path, key: bytes = HEADER_KEY) -> Path:
    """
    A prod.keys file with a header_key and an unrelated key around it.
    """
    lines: list[str] = [
        f'aes_kek_generation_source = {"00" * 16}',
        f'header_key = {key.hex()}',
        f'key_area_key_application_00 = {"11" * 16}',
    ]
    path.write_text('\n'.join(lines) + '\n')

    return path


def run_cli(args: list[str], home: Path) -> Run:
    """
    Run `python -m nx_title_id` with HOME set to `home`, so the default
    ~/.switch/prod.keys is the test's, never the user's.
    """
    done: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, '-m', 'nx_title_id', *args],
        capture_output=True,
        text=True,
        env={'HOME': str(home), 'PATH': '/usr/bin:/bin'},
        check=False,
    )

    return Run(done.returncode, done.stdout, done.stderr)
