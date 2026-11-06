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
from collections.abc import Callable, Sequence
from io import BufferedReader, BytesIO
from pathlib import Path

__version__ = '1.1.0'

EXTENSIONS = ('.nsp', '.nsz', '.xci', '.xcz')
DEFAULT_KEYS: Path = Path.home() / '.switch' / 'prod.keys'
META = 1  # the meta NCA is the only content type we read, so name just that one

# Reaching a Build ID means decrypting a program NCA down to the main NSO, so
# these name the handful of on-disk offsets and magic values that walk gets us
# there. They stay magic-number-free at the call sites, which is where a wrong
# constant would otherwise be invisible.
NSO_MAGIC = b'NSO0'  # a mismatch here means we never reached the main NSO
MODULE_ID_AT = 0x40  # the ModuleId (the Build ID) sits here in the NSO header
MODULE_ID_SIZE = 0x20  # the ModuleId is up to 32 bytes, so read this span
BUILD_ID_SIZE = 8  # tools show only the ModuleId's first 8 bytes as the Build ID
PROGRAM = 0  # only the program NCA (content type 0) carries the exefs
HEADER_REGION = 0xC00  # six XTS sectors of header; sections begin after it
FS_ENTRY_AT = 0x240  # the FsEntry array gives each section's offset and length
FS_HEADER_AT = 0x400  # the FsHeader array gives each section's encryption
KEY_AREA_AT = 0x300  # the encrypted key area holds the per-NCA section keys
UPDATE_FLAG = 0x800  # an update title ID is base + this, so classify by it
RIGHTS_ID_AT = 0x230  # non-zero RightsId marks a titlekey NCA (switchbrew NCA)

# Ticket layout (switchbrew "Ticket"): signature data, then the ticket body.
# The signature type picks the signature size and padding, which together set
# where the body starts: 4 (type) + signature + padding, 0x40-aligned. Types
# 0x10000/0x10003 are RSA-4096, 0x10001/0x10004 RSA-2048, 0x10002/0x10005
# ECDSA, 0x10006 HMAC-SHA1-160. Anything else is unrecognised and skipped.
TICKET_BODY_AT: dict[int, int] = {
    0x10000: 0x240,
    0x10001: 0x140,
    0x10002: 0x80,
    0x10003: 0x240,
    0x10004: 0x140,
    0x10005: 0x80,
    0x10006: 0x40,
}
TICKET_KEY_BLOCK_AT = 0x40  # body offset of the 0x100-byte title key block
TICKET_KEY_TYPE_AT = 0x141  # body offset of the title key type (0 = common)
TICKET_READ_SIZE = 0x500  # largest body offset (0x240) + the 0x2C0-byte body

# The update version used to pick the newest update of a base game. A real CNMT
# stores it deeper in the meta NCA's content; this is a test/synthetic
# convention shared with the builder's meta_nca, which writes the version as a
# u32 here in the decrypted sector-1 header (past the title ID at 0x10) and
# leaves it zero when there is no version. Reading it keeps the synthetic
# newest-update path testable without a full CNMT parser, so a 0 u32 means "no
# readable version", not "version 0".
META_VERSION_AT = 0x18

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


def read_key_area_keys(keys_path: Path) -> dict[int, bytes]:
    """
    Pull every key_area_key_application_XX out of a prod.keys file, keyed by
    generation (XX is two lowercase hex digits). Unlike header_key, a missing
    entry is not an error here: only the generations a given NCA needs matter,
    so the caller looks one up and treats a miss as a per-package skip.
    """
    found: dict[int, bytes] = {}

    for line in Path(keys_path).read_text(errors='replace').splitlines():
        # Same leniency as read_header_key: real files vary in spacing and case.
        m: re.Match[str] | None = re.fullmatch(
            r'\s*key_area_key_application_([0-9A-Fa-f]{2})\s*=\s*([0-9A-Fa-f]{32})\s*', line
        )

        if m:
            found[int(m.group(1), 16)] = bytes.fromhex(m.group(2))

    return found


def read_titlekeks(keys_path: Path) -> dict[int, bytes]:
    """
    Pull every titlekek_XX out of a prod.keys file, keyed by generation (XX is
    two hex digits). Same leniency and miss-is-not-an-error policy as
    read_key_area_keys: only the generation a given ticket needs matters.
    """
    found: dict[int, bytes] = {}

    for line in Path(keys_path).read_text(errors='replace').splitlines():
        m: re.Match[str] | None = re.fullmatch(r'\s*titlekek_([0-9A-Fa-f]{2})\s*=\s*([0-9A-Fa-f]{32})\s*', line)

        if m:
            found[int(m.group(1), 16)] = bytes.fromhex(m.group(2))

    return found


def title_key_of(
    ticket: bytes,
    rights_id: bytes,
    titlekek: Callable[[int], bytes | None] | None,
) -> bytes | None:
    """
    Recover the plaintext titlekey from a common-type ticket, or None when it
    cannot be (unrecognised signature type, personalized ticket, truncated
    ticket, or no titlekek), so the caller skips the package. Per switchbrew
    "Ticket", the body starts after the signature data (TICKET_BODY_AT by
    signature type) and holds the 0x100-byte title key block at +0x40; for a
    common ticket (title key type 0 at +0x141) the first 16 bytes are the
    titlekey AES-128-ECB-encrypted with titlekek for the key generation. A
    personalized ticket (type 1) holds an RSA-2048 block only the owning console
    can open, so it is unsupported. The generation comes from the rights ID's
    last byte (the master key revision), where 0 and 1 both mean generation 0,
    as with the NCA key generation (hactool / switchbrew "NCA").
    """

    # Not enough bytes to read the signature type means no ticket to use.
    if len(ticket) < 4:
        return None

    body_at: int | None = TICKET_BODY_AT.get(struct.unpack_from('<I', ticket, 0)[0])

    # An unknown signature type means we cannot find the body; skip.
    if body_at is None or len(ticket) < body_at + TICKET_KEY_TYPE_AT + 1:
        return None

    # Only common tickets carry an AES-encrypted titlekey we can open.
    if ticket[body_at + TICKET_KEY_TYPE_AT] != 0 or titlekek is None:
        return None

    revision: int = rights_id[15]
    kek: bytes | None = titlekek(revision - 1 if revision > 0 else 0)

    # The keys file lacks the titlekek for this generation; skip.
    if kek is None:
        return None

    # Deferred like the other AES uses: default runs must not load pycryptodome.
    from Crypto.Cipher import AES

    block_at: int = body_at + TICKET_KEY_BLOCK_AT

    return AES.new(kek, AES.MODE_ECB).decrypt(ticket[block_at : block_at + 0x10])


def ticket_of(f: BufferedReader, rights_id: bytes) -> bytes | None:
    """
    Read the `<rights ID>.tik` file for a rights ID from the open package, or
    None when the package has none. The name is the 32 hex digits of the rights
    ID, matched case-insensitively. The ticket is read in a fixed window large
    enough for any signature variant; title_key_of bounds-checks what it uses.
    """
    want: str = f'{rights_id.hex()}.tik'

    for name, at in package_files(f):
        if name.lower() == want:
            f.seek(at)

            return f.read(TICKET_READ_SIZE)

    return None


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


def decrypt_nca_header(header_key: bytes, raw: bytes) -> bytes:
    """
    Decrypt the 0xC00-byte NCA header region and return the plaintext. The
    region is AES-XTS over six 0x200-byte sectors numbered from 0, so this
    reuses xts_decrypt_sector per sector and concatenates the results. Sector 1
    is the NCA header the meta path already reads; sectors 2..5 hold the four
    FsHeaders a Build-ID walk needs, so decrypting the whole region once hands
    callers every field they will look at.
    """
    out = bytearray()

    # Each 0x200 slice is one XTS sector; its own sector number feeds the tweak,
    # so decrypt them independently and join in order.
    for sector in range(HEADER_REGION // 0x200):
        at = sector * 0x200
        out += xts_decrypt_sector(header_key, raw[at : at + 0x200], sector)

    return bytes(out)


def _key_generation(header: bytes) -> int:
    """
    Decode an NCA's key generation from its decrypted header. switchbrew keeps
    the value in two bytes for backward compatibility: the old KeyGeneration at
    0x206 and a newer KeyGeneration2 at 0x220. The effective generation is the
    greater of the two, and a stored value of 1 historically means generation 0
    (the 1.0.0 keys), so collapse 1 down to 0 here. The result indexes
    key_area_key_application_XX in prod.keys.
    """
    old: int = header[0x206]  # KeyGeneration (pre-3.0.1 NCAs leave this set)
    new: int = header[0x220]  # KeyGeneration2 (3.0.1+ NCAs set this instead)
    generation: int = max(old, new)

    # switchbrew: a raw value of 0 or 1 both map to the first key generation,
    # so normalise 1 -> 0 to index key_area_key_application_00 for both.
    return generation - 1 if generation > 0 else 0


def _ctr_decrypt_section(
    raw: bytes,
    header: bytes,
    fs_header_at: int,
    nca_offset: int,
    key_area_key: Callable[[int], bytes | None] | None,
    title_key: bytes | None = None,
) -> bytes | None:
    """
    AES-CTR-decrypt bytes read from an NCA section, or return None when it
    cannot be keyed (per-package skip, never a crash). A zero RightsId means
    standard crypto: the section key is recovered by AES-128-ECB-decrypting the
    EncryptedKeyArea (4 x 0x10 at KEY_AREA_AT) with key_area_key_application
    for the header's key generation, then taking decrypted row 2 (bytes
    [0x20:0x30]), switchbrew's standard content key for a section. A non-zero
    RightsId means titlekey crypto: the already-decrypted `title_key` from the
    ticket is the section key directly, with no key-area step (switchbrew
    "NCA": rights-ID NCAs use the titlekey in place of the key area's
    AES-CTR key). `nca_offset` is where `raw` starts relative to the start of
    the NCA (not the package) and must be 0x10-aligned.
    """

    # Deferred on purpose, matching xts_decrypt_sector's documented exception to
    # the imports-are-global rule: a default (no -b) run resolves by ticket or
    # tag and never reaches this branch, so it should not load pycryptodome.
    from Crypto.Cipher import AES

    section_key: bytes

    # A non-zero RightsId at 0x230 marks a titlekey (ticket) NCA, which is keyed
    # from the titlekey rather than the key area. Without one (no ticket, no
    # titlekek, unsupported ticket) the section is un-keyable: skip the package.
    if header[RIGHTS_ID_AT : RIGHTS_ID_AT + 0x10] != b'\x00' * 0x10:
        if title_key is None:
            return None

        section_key = title_key

    else:
        # Without a key-area-key source there is no way to recover the section
        # key, so this section is un-keyable and the package is skipped.
        if key_area_key is None:
            return None

        kak: bytes | None = key_area_key(_key_generation(header))

        # The keys file lacks key_area_key_application for this generation, so
        # the section cannot be decrypted; skip the package.
        if kak is None:
            return None

        # The 4 x 0x10 EncryptedKeyArea is AES-128-ECB under the key-area key;
        # row 2 (bytes [0x20:0x30]) is the standard content key for a section.
        encrypted_key_area: bytes = header[KEY_AREA_AT : KEY_AREA_AT + 0x40]
        key_area: bytes = AES.new(kak, AES.MODE_ECB).decrypt(encrypted_key_area)
        section_key = key_area[0x20:0x30]

    # hactool/libhac build the CTR from the 8-byte upper counter at
    # FsHeader+0x140, stored little-endian but placed big-endian (so the bytes
    # are reversed), then the 16-byte block index within the NCA, big-endian.
    # Counting from the NCA start, not the package, is what made real meta NCA
    # sections inside an NSP decrypt to PFS0; the package offset did not.
    upper_counter: int = struct.unpack_from('<Q', header, fs_header_at + 0x140)[0]
    counter: bytes = struct.pack('>QQ', upper_counter, nca_offset >> 4)

    cipher = AES.new(section_key, AES.MODE_CTR, initial_value=counter, nonce=b'')

    return cipher.decrypt(raw)


def exefs_section(
    f: BufferedReader,
    nca_at: int,
    header: bytes,
    key_area_key: Callable[[int], bytes | None] | None,
    title_key: bytes | None = None,
) -> bytes | None:
    """
    Return the decrypted bytes of the PFS0 held in the program NCA's exefs
    PartitionFS section, or None when no exefs section is present or it cannot
    be decrypted. Walks the FsEntry/FsHeader arrays in the decrypted header to
    find the first PartitionFS (FsType 1 at FsHeader+0x2) section, then reads
    the PFS0 at the section's HierarchicalSha256 PfsOffset/PfsSize (u64s at
    FsHeader+0x40/0x48), and decrypts per EncryptionType: 1 (None) returns the
    bytes as-is (the synthetic CI path); 3 (AesCtr) recovers the section key
    from the decrypted key area, or for a titlekey (non-zero RightsId) NCA uses
    the caller-supplied decrypted `title_key`, then AES-CTR-decrypts (the real
    retail path). A missing key, and any other encryption type, return None so
    the package is skipped per-package.
    """

    # The header holds four section slots; the first PartitionFS one is the
    # exefs we want. FsHeader[i] carries the type and encryption; FsEntry[i]
    # carries the offset and length.
    for i in range(4):
        fs_header_at = FS_HEADER_AT + i * 0x200
        # switchbrew puts FsType at +0x2; +0x3 is HashType, which real NCAs set
        # to 2/3 and would never match PartitionFS (1).
        fs_type = header[fs_header_at + 0x2]

        # Not a PartitionFS section, so it is not the exefs; keep looking.
        if fs_type != 1:
            continue

        encryption_type = header[fs_header_at + 0x4]
        start_offset = struct.unpack_from('<I', header, FS_ENTRY_AT + i * 0x10)[0]
        # The section's hash table comes first; the PFS0 itself sits at
        # PfsOffset within the section. Checked against real NCAs, where only
        # this offset (with FsEntry blocks counted from the NCA start, not the
        # end of the header region) lands on the PFS0 magic.
        pfs_offset, pfs_size = struct.unpack_from('<QQ', header, fs_header_at + 0x40)
        data_at = start_offset * 0x200 + pfs_offset  # relative to the NCA start

        # EncryptionType 1 (None) is the synthetic CI path: the section bytes
        # are already plaintext, so hand them back untouched.
        if encryption_type == 1:
            f.seek(nca_at + data_at)

            return f.read(pfs_size)

        # EncryptionType 3 (AesCtr) is the real retail path: recover the
        # section key from the key area and AES-CTR-decrypt. Everything else
        # (including 2/AesXts, which retail exefs never uses) is unsupported.
        if encryption_type == 3:
            # CTR runs in 0x10 blocks, so start the read on a block boundary
            # and drop the lead-in after decrypting.
            aligned_at = data_at & ~0xF
            f.seek(nca_at + aligned_at)
            raw = f.read(data_at - aligned_at + pfs_size)
            plain: bytes | None = _ctr_decrypt_section(raw, header, fs_header_at, aligned_at, key_area_key, title_key)

            return None if plain is None else plain[data_at - aligned_at :]

        # Any other encryption type is one the reader cannot handle, so skip
        # the whole package rather than returning a half-decrypted section.
        return None

    return None


def module_id_of(
    path: Path,
    header_key: bytes,
    key_area_key: Callable[[int], bytes | None] | None,
    titlekek: Callable[[int], bytes | None] | None = None,
) -> bytes | None:
    """
    Decrypt the package's program NCA far enough to read the main NSO's
    ModuleId, returning the exact MODULE_ID_SIZE bytes present or None when the
    package carries no program NCA or no readable main NSO. Finds the first
    program NCA (content type PROGRAM), decrypts its header, reads its exefs,
    locates the file named "main" in that PFS0, checks the NSO0 magic, and
    returns NSO header bytes [MODULE_ID_AT : MODULE_ID_AT + MODULE_ID_SIZE]. A
    genuinely corrupt header still raises MalformedPackageError (the existing
    handler skips it); None is reserved for the expected "cannot reach the main
    NSO" cases so the caller skips that one package and keeps going.
    """

    with open(path, 'rb') as f:
        for name, at in package_files(f):
            # Only NCA/NCZ entries carry the header we can walk; skip the rest
            # without paying for a decrypt.
            if not name.lower().endswith(('.nca', '.ncz')):
                continue

            f.seek(at)
            raw: bytes = f.read(HEADER_REGION)

            # A region shorter than the full header cannot be decrypted into a
            # usable NCA header, so this NCA cannot be the program one.
            if len(raw) < HEADER_REGION:
                continue

            header: bytes = decrypt_nca_header(header_key, raw)

            # Wrong magic means the wrong key or an unsupported NCA version;
            # either way this is not a readable program NCA.
            if header[0x200:0x204] not in (b'NCA3', b'NCA2'):
                continue

            # Only the program NCA (content type PROGRAM) carries the exefs, so
            # anything else is not the NCA we need.
            if header[0x205] != PROGRAM:
                continue

            title_key: bytes | None = None
            rights_id: bytes = header[RIGHTS_ID_AT : RIGHTS_ID_AT + 0x10]

            # A rights ID means the section is keyed by a ticket's titlekey.
            # The ticket lives in the same package; a missing ticket, missing
            # titlekek, or unsupported ticket leaves title_key None, which
            # exefs_section turns into a per-package skip.
            if rights_id != b'\x00' * 0x10:
                ticket: bytes | None = ticket_of(f, rights_id)

                if ticket is not None:
                    title_key = title_key_of(ticket, rights_id, titlekek)

            exefs: bytes | None = exefs_section(f, at, header, key_area_key, title_key)

            # No readable exefs (absent section or an encryption the reader
            # cannot handle yet) means we cannot reach the main NSO here.
            if exefs is None:
                return None

            # Parse the exefs PFS0 the same way package_files parses a PFS0 at
            # offset 0, but over an in-memory reader of the decrypted bytes.
            reader = BufferedReader(BytesIO(exefs))

            for entry_name, entry_at in partition(reader, 0, b'PFS0', 0x18):
                # The main NSO is the only exefs entry that carries a ModuleId,
                # so skip every other file in the partition.
                if entry_name != 'main':
                    continue

                reader.seek(entry_at)
                nso: bytes = reader.read(MODULE_ID_AT + MODULE_ID_SIZE)

                # A short read or wrong magic means this is not a readable main
                # NSO, so there is no Build ID to return for this package.
                if len(nso) < MODULE_ID_AT + MODULE_ID_SIZE or nso[:4] != NSO_MAGIC:
                    return None

                return nso[MODULE_ID_AT : MODULE_ID_AT + MODULE_ID_SIZE]

            # A program NCA whose exefs has no "main" entry cannot yield a Build
            # ID, so skip this package.
            return None

    # No program NCA at all in the package: nothing to read a Build ID from.
    return None


def update_version_of(path: Path, header_key: bytes) -> int | None:
    """
    Read the package's update version from its meta NCA so the resolver can pick
    the newest update of a base game, or None when no readable version is
    available. A None result means "readable update version unavailable", which
    the resolver treats as merely newer than the base. The version is read as a
    little-endian u32 at META_VERSION_AT of each meta NCA's decrypted sector-1
    header (the same sector meta_title_of reads), so the crypto is not
    duplicated. A zero u32 is treated as "no readable version" (and so returns
    None here), not "version 0", because the builder writes zero when there is
    no version; among several meta NCAs the greatest non-zero version wins, so
    the choice is deterministic and matches the newest-update rule.
    """
    best: int | None = None

    with open(path, 'rb') as f:
        for name, at in package_files(f):
            # Only NCA/NCZ entries carry the header we can read; skip the rest
            # without paying for a decrypt.
            if not name.lower().endswith(('.nca', '.ncz')):
                continue

            f.seek(at + 0x200)
            sector: bytes = f.read(0x200)

            # A header shorter than a full sector cannot be decrypted; move on.
            if len(sector) < 0x200:
                continue

            h: bytes = xts_decrypt_sector(header_key, sector, 1)

            # Wrong magic means the wrong key or an unsupported NCA version; a
            # non-meta content type carries no version we read. Both are skipped
            # so a bad key yields no version instead of garbage.
            if h[:4] not in (b'NCA3', b'NCA2') or h[5] != META:
                continue

            version = struct.unpack_from('<I', h, META_VERSION_AT)[0]

            # A zero u32 is the builder's "no version" sentinel, so only a
            # non-zero value counts; keep the greatest so several meta NCAs
            # resolve deterministically to the newest.
            if version and (best is None or version > best):
                best = version

    return best


def build_id_of(
    packages: Sequence[tuple[Path, set[int]]],
    base: int,
    header_key: bytes,
    key_area_key: Callable[[int], bytes | None] | None,
    titlekek: Callable[[int], bytes | None] | None = None,
) -> bytes | None:
    """
    Resolve the Build ID to report for one base game, honoring the
    newest-update rule, or None when no base/update package yields a readable
    main NSO. Classifies each scanned package by the low 12 bits of the title
    IDs it carries: the base title (base itself) marks a base package, base +
    UPDATE_FLAG marks an update package, and a DLC is never a Build-ID source.
    Among update packages the greatest readable update_version_of wins; an
    update with no readable version still beats the base (it is merely newer),
    so when every update is unreadable an update is still chosen. With no update
    package the base package is used. Ties and the no-version case are broken
    deterministically by the least sorted path, so the same folder always
    resolves the same Build ID.
    """
    base_paths: list[Path] = []
    update_paths: list[Path] = []

    # Classify by the title IDs already resolved for each package, reusing the
    # low-12-bits role logic so the Build-ID source matches application_of's
    # folding: base title -> base package, base + UPDATE_FLAG -> update package,
    # everything else (DLC) is ignored as a source.
    for path, title_ids in packages:
        if base in title_ids:
            base_paths.append(path)

        if (base + UPDATE_FLAG) in title_ids:
            update_paths.append(path)

    # An update always outranks the base, so prefer the update packages; among
    # them the greatest readable version is newest. Read each version once.
    if update_paths:
        best_path: Path | None = None
        best_version: int | None = None

        # Sort so ties and the all-unreadable case resolve to the least path,
        # making the choice deterministic regardless of scan order.
        for path in sorted(update_paths):
            version: int | None = update_version_of(path, header_key)

            # A readable version outranks an unreadable one; among readable
            # versions the greater wins. Sorted iteration means the first
            # package seen at a given rank is the least path, so a strict ">"
            # keeps it and preserves the deterministic tie-break.
            if version is not None and (best_version is None or version > best_version):
                best_path, best_version = path, version

        # Every update had an unreadable version, so none outranks another;
        # fall back to the least sorted path, which still beats the base.
        if best_path is None:
            best_path = sorted(update_paths)[0]

        return module_id_of(best_path, header_key, key_area_key, titlekek)

    # No update package for this base game: the base package is the source.
    if base_paths:
        return module_id_of(sorted(base_paths)[0], header_key, key_area_key, titlekek)

    # Neither a base nor an update package carries a program NCA for this game
    # (for example a ticket/tag-only game), so there is no Build ID to report.
    return None


def render_build_id(module_id: bytes) -> str:
    """
    Render the Build ID tools show: the ModuleId's first BUILD_ID_SIZE bytes as
    uppercase hex. The rest of the field is not part of the Build ID, so it is
    dropped here, at the one print site, while module_id_of keeps the full field.
    """
    return module_id[:BUILD_ID_SIZE].hex().upper()


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
    parser.add_argument(
        '-b',
        '--build-id',
        action='store_true',
        dest='build_id',
        help="also print each game's Build ID (the main NSO ModuleId); requires prod.keys",
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

    # Under -b a usable header_key is a precondition, not a fallback: checking
    # it up front means a keyless or header_key-less run fails before any
    # package output, so stdout never carries a partial list. This is distinct
    # from the default run, where a missing header_key is only warn-once-skip.
    if args.build_id:
        # No keys source at all (no -k and no default prod.keys): -b cannot
        # decrypt a single program NCA, so fail the run with a setup error.
        if keys is None:
            sys.exit(f'-b/--build-id needs prod.keys; pass -k or install one at {DEFAULT_KEYS}')

        # The file exists but yields no header_key (header_key() warns once and
        # caches None): under -b that is fatal, because no Build ID could ever
        # resolve without it. Report it as a setup error and stop before the
        # loop rather than letting every package be skipped.
        if header_key() is None:
            sys.exit(f'-b/--build-id needs a header_key in {keys}')

    kak_cache: list[dict[int, bytes]] = []  # empty until read, then holds [keys]

    def key_area_key(generation: int) -> bytes | None:
        """
        Look up key_area_key_application for a generation, reading prod.keys at
        most once and only when a program NCA first needs it. A missing
        generation is None (a per-package skip), never fatal. Only called when
        `keys` is set.
        """

        if not kak_cache:
            # Unreachable at runtime (-b guarantees `keys`), but it narrows the
            # type for the read below.
            if keys is None:
                return None

            try:
                kak_cache.append(read_key_area_keys(keys))

            except OSError as e:
                sys.exit(f'cannot read keys file {keys}: {e.strerror or e}')

        return kak_cache[0].get(generation)

    tk_cache: list[dict[int, bytes]] = []  # empty until read, then holds [keys]

    def titlekek(generation: int) -> bytes | None:
        """
        Look up titlekek_XX for a generation, reading prod.keys at most once and
        only when a titlekey NCA first needs it. A missing generation is None
        (a per-package skip), never fatal.
        """

        if not tk_cache:
            # Unreachable at runtime (-b guarantees `keys`); narrows the type.
            if keys is None:
                return None

            try:
                tk_cache.append(read_titlekeks(keys))

            except OSError as e:
                sys.exit(f'cannot read keys file {keys}: {e.strerror or e}')

        return tk_cache[0].get(generation)

    apps: set[int] = set()
    scanned: list[tuple[Path, set[int]]] = []  # kept for -b's Build ID lookup

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
        scanned.append((pkg, tids))

    # Nothing resolved anywhere is a failed run, not an empty-but-fine one.
    if not apps:
        sys.exit(f'no package with a readable title ID in {args.folder}')

    lines: list[str] = []

    for app in sorted(apps):
        # Default mode prints the bare title ID, with no extra reads at all.
        if not args.build_id:
            lines.append(f'{app:016X}')

            continue

        module_id: bytes | None

        # A per-package failure skips just this game, matching the scan's
        # policy. header_key() is known to be bytes here: the -b precondition
        # exited earlier otherwise.
        try:
            hk: bytes | None = header_key()
            module_id = build_id_of(scanned, app, hk, key_area_key, titlekek) if hk else None

        except MalformedPackageError as e:
            print(f'skipping game {app:016X}: {e}', file=sys.stderr)

            continue

        except OSError as e:
            print(f'skipping game {app:016X}: {e.strerror or e}', file=sys.stderr)

            continue

        # No readable main NSO (ticket/tag-only game, missing key-area key, or
        # an unsupported encryption) means no Build ID to report for this game.
        if module_id is None:
            print(
                f'skipping game {app:016X}: no Build ID (no readable program NCA, ticket, or key in {keys})',
                file=sys.stderr,
            )

            continue

        lines.append(f'{app:016X} {render_build_id(module_id)}')

    # Every game skipped under -b: nothing was printed, so it is a failed run.
    if not lines:
        sys.exit(f'no game with a readable Build ID in {args.folder}')

    # Warn before the IDs so a human sees the heads-up first, while stdout still
    # carries only the clean list for a consuming script. The count is of games
    # actually printed, so a skipped game does not turn one line into exit 3.
    if len(lines) > 1:
        print('more than one game in the folder:', file=sys.stderr)

    for line in lines:
        print(line)

    # Several games is a distinct outcome, not a failure: print them all, then
    # signal it with exit 3 so a caller can tell it from the single-game case.
    if len(lines) > 1:
        sys.exit(3)
