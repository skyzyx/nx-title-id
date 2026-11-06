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

# The main NSO header carries NSO0 at offset 0 and the ModuleId (the Build ID)
# at 0x40, mirroring nx_title_id.NSO_MAGIC/MODULE_ID_AT/MODULE_ID_SIZE. The
# buffer pads the ModuleId span to MODULE_ID_SIZE so a short Build ID still sits
# at the right offset, while the caller keeps the exact byte length to assert.
NSO_MAGIC: bytes = b'NSO0'
MODULE_ID_AT: int = 0x40
MODULE_ID_SIZE: int = 0x20

# Real base-game NSPs named <TitleID>-<BuildID>.nsp, kept out of git by
# tests/.gitignore, so tests using them skip when they're absent (a fresh
# clone, CI). The Build ID after the hyphen is the ground truth for the -b
# fixture tests, so each present fixture is discovered by globbing its Title ID.
TESTS_DIR: Path = Path(__file__).parent
REAL_TITLE_IDS: tuple[int, ...] = (0x01004B800AF5A000, 0x01009C100390E000, 0x0100D9B00B666000)


def _real_nsp_path(title_id: int) -> Path | None:
    """
    The on-disk <TitleID>-<BuildID>.nsp fixture for `title_id`, or None when no
    such fixture is present.
    """
    matches: list[Path] = sorted(TESTS_DIR.glob(f'{title_id:016X}-*.nsp'))

    return matches[0] if matches else None


HAVE_REAL_NSPS: bool = all(_real_nsp_path(tid) is not None for tid in REAL_TITLE_IDS)
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


def nso(module_id: bytes) -> bytes:
    """
    A main NSO blob: NSO0 at offset 0 and `module_id` (the Build ID, 0..32
    bytes) at MODULE_ID_AT, padded to MODULE_ID_SIZE so a short Build ID still
    lands at the right offset. The caller keeps `module_id`'s exact length to
    assert the extracted bytes round-trip without invented or truncated digits.
    """
    if len(module_id) > MODULE_ID_SIZE:
        raise ValueError(f'module_id is {len(module_id)} bytes (limit {MODULE_ID_SIZE})')

    blob = bytearray(MODULE_ID_AT + MODULE_ID_SIZE)
    blob[0:4] = NSO_MAGIC
    blob[MODULE_ID_AT : MODULE_ID_AT + len(module_id)] = module_id

    return bytes(blob)


def exefs(module_id: bytes) -> bytes:
    """
    An exefs PFS0 holding the main NSO that embeds `module_id`, built with the
    same _partition the production code parses to find the file named "main".
    """
    return pfs0([('main', nso(module_id))])


def real_nsp(title_id: int) -> Path:
    """
    The real <TitleID>-<BuildID>.nsp fixture for `title_id` in tests/, or the
    bare <TitleID>-*.nsp path (which does not exist) when no fixture is present,
    so callers that only reach here under HAVE_REAL_NSPS get a usable path.
    """
    found: Path | None = _real_nsp_path(title_id)

    return found if found is not None else TESTS_DIR / f'{title_id:016X}-.nsp'


def real_nsp_build_id(title_id: int) -> str | None:
    """
    The uppercase-hex Build ID encoded after the hyphen in the present
    <TitleID>-<BuildID>.nsp fixture for `title_id`, or None when no fixture is
    present.
    """
    found: Path | None = _real_nsp_path(title_id)

    if found is None:
        return None

    return found.stem.split('-', 1)[1].upper()


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


# ------------------------------------------------------------------------------
# PROGRAM NCA AND PACKAGE BUILDERS

# Offsets inside the decrypted 0xC00 NCA header region that the production
# exefs reader walks: the NCA3 magic and ContentType in sector 1, then the
# FsEntry array (section offset/length in 0x200 blocks from the NCA start) and
# the FsHeader array (section type, encryption, and the PFS0's offset/size
# within the section), one per 0x200 section header. These mirror real retail
# NCAs and nx_title_id's FS_ENTRY_AT/FS_HEADER_AT so a built program NCA parses
# with the same offsets the reader uses on real data.
NCA_MAGIC_AT: int = 0x200
NCA_CONTENT_TYPE_AT: int = 0x205
FS_ENTRY_AT: int = 0x240
FS_HEADER_AT: int = 0x400
HEADER_REGION: int = 0xC00
HEADER_SECTORS: int = HEADER_REGION // SECTOR  # six 0x200 sectors, numbered 0..5

# FsHeader field values the reader keys off: a PartitionFS section (FsType 1)
# whose data is stored unencrypted (EncryptionType 1 = None), so the synthetic
# exefs needs no real section crypto, only the header XTS the tests already do.
FS_TYPE_PARTITION: int = 1
ENCRYPTION_NONE: int = 1
HASH_TYPE_SHA256: int = 2  # HierarchicalSha256, what real exefs sections use
ENCRYPTION_AES_CTR: int = 3
NCA_RIGHTS_ID_AT: int = 0x230  # RightsId, non-zero for a titlekey NCA
NCA_KEY_GENERATION_AT: int = 0x220  # KeyGeneration2; stored value - 1 = gen
KEY_AREA_AT: int = 0x300  # EncryptedKeyArea, 4 x 0x10
UPPER_COUNTER_AT: int = 0x140  # FsHeader upper CTR half (u64 LE)
CTR_UPPER: int = 0x1122334455667788  # arbitrary non-zero upper counter
FS_TYPE_AT: int = 0x2  # FsType u8 within a 0x200 FsHeader
HASH_TYPE_AT: int = 0x3  # HashType u8 within a 0x200 FsHeader
ENCRYPTION_TYPE_AT: int = 0x4  # EncryptionType u8 within a 0x200 FsHeader
PFS_OFFSET_AT: int = 0x40  # PfsOffset u64 then PfsSize u64 (HierarchicalSha256)

# The synthetic meta NCA carries an update version as a u32 in its decrypted
# sector-1 header, past the title ID at 0x10, so update_version_of can read it
# back to pick the newest update. It is a test-only convention shared by this
# builder and that reader, not a real CNMT layout.
META_VERSION_AT: int = 0x18


def program_nca(
    module_id: bytes,
    magic: bytes = b'NCA3',
    *,
    section_key: bytes | None = None,
    rights_id: bytes = bytes(16),
    key_area_key: bytes | None = None,
    key_generation: int = 0,
) -> bytes:
    """
    A program NCA carrying `module_id` as its main NSO's Build ID: a 0xC00
    header region declaring ContentType PROGRAM with one PartitionFS/None
    FsEntry+FsHeader whose section starts right after the region and whose
    PfsOffset/PfsSize name the exefs at the section start, with the header
    region XTS-encrypted sector by sector (0..5) under HEADER_KEY, so the
    production reader decrypts the header, walks to the None-encrypted exefs,
    and reads `module_id` straight back out.

    With `section_key` the exefs section is instead AES-128-CTR encrypted
    (EncryptionType 3) via the cryptography package, an independent reference
    for the production counter construction. A non-zero `rights_id` makes it a
    titlekey NCA, so `section_key` is the titlekey itself; with a zero
    `rights_id` and a `key_area_key`, the section key is stored as row 2 of an
    EncryptedKeyArea encrypted under that key for `key_generation`.
    """
    body: bytes = exefs(module_id)

    # StartOffset/EndOffset are in 0x200 blocks from the NCA start; the section
    # begins right after the header region and spans however many whole 0x200
    # blocks cover the exefs, so pad the body to a block boundary.
    start: int = HEADER_SECTORS
    blocks: int = (len(body) + SECTOR - 1) // SECTOR
    exefs_body: bytes = body.ljust(blocks * SECTOR, b'\0')

    header = bytearray(HEADER_REGION)
    header[NCA_MAGIC_AT : NCA_MAGIC_AT + 4] = magic
    header[NCA_CONTENT_TYPE_AT] = PROGRAM
    struct.pack_into('<II', header, FS_ENTRY_AT, start, start + blocks)
    header[FS_HEADER_AT + FS_TYPE_AT] = FS_TYPE_PARTITION
    header[FS_HEADER_AT + HASH_TYPE_AT] = HASH_TYPE_SHA256
    header[FS_HEADER_AT + ENCRYPTION_TYPE_AT] = ENCRYPTION_NONE
    # A real section opens with its hash table; this one has none, so the PFS0
    # sits at the section start (offset 0) and spans exactly the exefs bytes.
    struct.pack_into('<QQ', header, FS_HEADER_AT + PFS_OFFSET_AT, 0, len(body))

    section: bytes = exefs_body

    if section_key is not None:
        header[FS_HEADER_AT + ENCRYPTION_TYPE_AT] = ENCRYPTION_AES_CTR
        header[NCA_RIGHTS_ID_AT : NCA_RIGHTS_ID_AT + 16] = rights_id
        struct.pack_into('<Q', header, FS_HEADER_AT + UPPER_COUNTER_AT, CTR_UPPER)

        # The key area is only consulted for a zero RightsId; a non-zero one
        # uses section_key as the titlekey directly.
        if key_area_key is not None:
            header[NCA_KEY_GENERATION_AT] = key_generation + 1
            rows: bytes = bytes(0x20) + section_key + bytes(0x10)
            header[KEY_AREA_AT : KEY_AREA_AT + 0x40] = _aes_ecb_encrypt(key_area_key, rows)

        # Big-endian counter: the upper half, then the 16-byte block index of
        # the section's start within the NCA (the exefs sits at its start).
        counter: bytes = struct.pack('>QQ', CTR_UPPER, (start * SECTOR) >> 4)
        encryptor = Cipher(algorithms.AES(section_key), modes.CTR(counter)).encryptor()
        section = encryptor.update(exefs_body) + encryptor.finalize()

    encrypted = bytearray()

    # Encrypt each 0x200 sector under its own sector number, the same per-sector
    # XTS tweak the production header decrypt reverses.
    for sector in range(HEADER_SECTORS):
        plain: bytes = bytes(header[sector * SECTOR : (sector + 1) * SECTOR])
        encrypted += nintendo_xts_encrypt(HEADER_KEY, plain, sector)

    return bytes(encrypted) + section


def meta_nca(title_id: int, version: int | None = None, magic: bytes = b'NCA3') -> bytes:
    """
    A meta (CNMT) NCA for `title_id`, like nca_header but optionally carrying an
    update `version` as a u32 at META_VERSION_AT in its decrypted sector 1, so
    update_version_of can read it to order updates. Sector 0 is zeros; sector 1
    holds the magic, META content type, title ID and version, XTS-encrypted
    under HEADER_KEY.
    """
    plain = bytearray(SECTOR)
    plain[0:4] = magic
    plain[5] = META
    struct.pack_into('<Q', plain, 0x10, title_id)

    if version is not None:
        struct.pack_into('<I', plain, META_VERSION_AT, version)

    return bytes(SECTOR) + nintendo_xts_encrypt(HEADER_KEY, bytes(plain), 1)


def _aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    """
    AES-128-ECB encryption via the cryptography package (reference side).
    """
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()

    return encryptor.update(data) + encryptor.finalize()


# Where a ticket body starts per signature type (switchbrew "Ticket"), kept as
# an independent copy of the production table so a typo there is caught.
_TICKET_BODY_AT: dict[int, int] = {0x10003: 0x240, 0x10004: 0x140, 0x10005: 0x80, 0x10006: 0x40}


def rights_id_of(title_id: int, revision: int = 0) -> bytes:
    """
    A 16-byte rights ID: the title ID, zeros, then the master key revision in
    the last byte (so revision 0 matches ticket_name).
    """
    return title_id.to_bytes(8, 'big') + bytes(7) + bytes([revision])


def ticket(
    rights_id: bytes,
    title_key: bytes,
    titlekek: bytes,
    sig_type: int = 0x10004,
    key_type: int = 0,
) -> bytes:
    """
    A ticket whose title key block holds `title_key` AES-128-ECB encrypted under
    `titlekek`, with the body placed per `sig_type` and the title key type set
    to `key_type` (0 common, 1 personalized).
    """
    body = bytearray(0x2C0)
    body[0x40:0x50] = _aes_ecb_encrypt(titlekek, title_key)
    body[0x141] = key_type
    body[0x160:0x170] = rights_id

    return struct.pack('<I', sig_type).ljust(_TICKET_BODY_AT[sig_type], b'\0') + bytes(body)


def titlekey_package(
    title_id: int,
    module_id: bytes,
    title_key: bytes,
    tik: bytes | None,
    revision: int = 0,
) -> bytes:
    """
    A package with a titlekey program NCA (rights ID from `title_id` and
    `revision`, AES-CTR keyed by `title_key`) and, unless `tik` is None, the
    ticket named for that rights ID. The ticket's name also supplies the title
    ID, so no meta NCA is needed.
    """
    rights: bytes = rights_id_of(title_id, revision)
    files: list[tuple[str, bytes]] = [('program.nca', program_nca(module_id, section_key=title_key, rights_id=rights))]

    if tik is not None:
        files.append((f'{rights.hex()}.tik', tik))

    return pfs0(files)


def write_titlekek_keys(path: Path, generation: int, titlekek: bytes) -> Path:
    """
    write_keys plus one titlekek_XX line, leaving write_keys' output as is.
    """
    write_keys(path)
    with path.open('a') as f:
        f.write(f'titlekek_{generation:02x} = {titlekek.hex()}\n')

    return path


def program_package(title_id: int, module_id: bytes, version: int | None = None) -> bytes:
    """
    A base/update/DLC package (a PFS0 of .nca files) carrying a program NCA that
    embeds `module_id` plus a meta NCA for `title_id`, so application_of folds
    it to its base game and (for an update) update_version_of reads `version`.
    The meta NCA supplies the title ID the resolver groups on; the program NCA
    supplies the Build ID it reports.
    """
    files: list[tuple[str, bytes]] = [
        ('program.nca', program_nca(module_id)),
        ('meta.cnmt.nca', meta_nca(title_id, version)),
    ]

    return pfs0(files)
