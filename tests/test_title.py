"""
Title ID resolution: prod.keys parsing, Nintendo AES-XTS, meta NCAs, the
ticket → tag → meta NCA order, and base-game normalization.
"""

import os
import random
import struct
import tempfile
from collections.abc import Callable, Iterator
from io import BufferedReader
from pathlib import Path
from typing import Annotated

from tryke import Depends, describe, expect, fixture, test

from nx_title_id import (
    BUILD_ID_SIZE,
    FS_ENTRY_AT,
    FS_HEADER_AT,
    HEADER_REGION,
    MODULE_ID_SIZE,
    application_of,
    build_id_of,
    decrypt_nca_header,
    exefs_section,
    meta_title_of,
    module_id_of,
    read_header_key,
    read_titlekeks,
    render_build_id,
    title_ids_of,
    title_key_of,
    update_version_of,
    xts_decrypt_sector,
)
from tests.builders import (
    BASE,
    DLC_1,
    HAVE_REAL_KEYS,
    HAVE_REAL_NSPS,
    HEADER_KEY,
    META,
    NO_REAL_NSPS,
    OTHER_BASE,
    PROGRAM,
    REAL_KEYS,
    SECTOR,
    UPDATE,
    Run,
    exefs,
    meta_nca,
    nca_header,
    nintendo_xts_encrypt,
    pfs0,
    program_nca,
    program_package,
    reader,
    real_nsp,
    rights_id_of,
    run_cli,
    ticket,
    ticket_name,
    titlekey_package,
    write_keys,
    write_titlekek_keys,
    xci,
)

# ------------------------------------------------------------------------------
# FIXTURES


@fixture
def workdir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ------------------------------------------------------------------------------
# PRIVATE FUNCTIONS


def _key() -> bytes | None:
    """
    The header_key callable title_ids_of() expects.
    """
    return HEADER_KEY


def _no_key() -> bytes | None:
    """
    A header_key callable that fails the test if title_ids_of() calls it.
    """
    raise AssertionError('header_key() was called')


def _decrypted_header(fs_type: int, encryption_type: int, blocks: int) -> bytes:
    """
    A minimal decrypted 0xC00 NCA header for exefs_section to walk: ContentType
    PROGRAM, one FsEntry at FS_ENTRY_AT spanning `blocks` 0x200 blocks from
    StartOffset 0, and one FsHeader at FS_HEADER_AT with the given FsType (at
    +0x2, as on real NCAs) and EncryptionType. Builds the plaintext directly (no
    crypto) because exefs_section takes the already-decrypted header, so the
    absent-section and unsupported-encryption variants need no round-trip
    through XTS.
    """
    header = bytearray(HEADER_REGION)
    header[0x200:0x204] = b'NCA3'
    header[0x205] = PROGRAM
    struct.pack_into('<II', header, FS_ENTRY_AT, 0, blocks)
    header[FS_HEADER_AT + 0x2] = fs_type
    header[FS_HEADER_AT + 0x4] = encryption_type

    return bytes(header)


# ------------------------------------------------------------------------------
# TESTS

with describe('read_header_key'):

    @test(name='finds header_key among other keys')
    def finds(d: Annotated[Path, Depends(workdir)]) -> None:
        expect(read_header_key(write_keys(d / 'prod.keys'))).to_equal(HEADER_KEY)

    @test(name='accepts uppercase hex and surrounding whitespace')
    def lenient(d: Annotated[Path, Depends(workdir)]) -> None:
        keys: Path = d / 'prod.keys'
        keys.write_text(f'  header_key   =   {HEADER_KEY.hex().upper()}  \n')

        expect(read_header_key(keys)).to_equal(HEADER_KEY)

    @test(name='raises ValueError without a header_key')
    def missing(d: Annotated[Path, Depends(workdir)]) -> None:
        keys: Path = d / 'prod.keys'
        keys.write_text('header_key = 0011\n')

        expect(lambda: read_header_key(keys)).to_raise(ValueError, match='no header_key')


with describe('xts_decrypt_sector'):

    @test.cases(
        test.case('sector 0', sector=0),
        test.case('sector 1', sector=1),
        test.case('sector 0x1234', sector=0x1234),
    )
    def matches_reference(sector: int) -> None:
        # 0x200 random bytes: 32 blocks, so the GF(2^128) tweak carry is hit
        plain: bytes = os.urandom(SECTOR)
        cipher: bytes = nintendo_xts_encrypt(HEADER_KEY, plain, sector)

        expect(xts_decrypt_sector(HEADER_KEY, cipher, sector)).to_equal(plain)

    @test(name='the wrong sector number does not decrypt')
    def wrong_sector() -> None:
        plain: bytes = bytes(SECTOR)
        cipher: bytes = nintendo_xts_encrypt(HEADER_KEY, plain, 1)

        expect(xts_decrypt_sector(HEADER_KEY, cipher, 2)).not_.to_equal(plain)


with describe('decrypt_nca_header'):

    @test(name='round-trips a 0xC00 region encrypted sector by sector')
    def round_trip() -> None:
        # Six 0x200 sectors numbered 0..5, each encrypted under its own sector
        # tweak, so the whole-region decrypt must reverse all of them in place.
        sectors: int = HEADER_REGION // SECTOR
        plains: list[bytes] = [os.urandom(SECTOR) for _ in range(sectors)]
        plain: bytes = b''.join(plains)
        cipher: bytes = b''.join(nintendo_xts_encrypt(HEADER_KEY, p, i) for i, p in enumerate(plains))

        expect(decrypt_nca_header(HEADER_KEY, cipher)).to_equal(plain)

    @test(name='a truncated region decrypts only the sectors present')
    def truncated() -> None:
        # The reader slices raw[at:at+0x200] per sector, so an input shorter
        # than 0xC00 yields only the bytes the present sectors decrypt to: here
        # two whole sectors plus a half third, the rest simply absent.
        present: int = 2 * SECTOR + SECTOR // 2
        plain: bytes = os.urandom(present)
        cipher = bytearray()

        for sector in range(HEADER_REGION // SECTOR):
            at: int = sector * SECTOR
            chunk: bytes = plain[at : at + SECTOR]

            # XTS needs whole 16-byte blocks; the builder's reference and the
            # production reader agree on a 0x200-aligned half sector, so slice
            # the same way and only encrypt what is actually there.
            if chunk:
                cipher += nintendo_xts_encrypt(HEADER_KEY, chunk, sector)

        expect(decrypt_nca_header(HEADER_KEY, bytes(cipher))).to_equal(plain)


with describe('meta_title_of'):

    @test(name='every meta NCA title ID, ignoring other content types')
    def lowest_meta(d: Annotated[Path, Depends(workdir)]) -> None:
        pkg: Path = d / 'game.nsp'
        pkg.write_bytes(
            pfs0(
                [
                    ('program.nca', nca_header(BASE - 0x10000, PROGRAM)),
                    ('update.cnmt.nca', nca_header(UPDATE, META)),
                    ('base.cnmt.ncz', nca_header(BASE, META, magic=b'NCA2')),
                    ('readme.txt', b'not an nca'),
                ]
            )
        )

        # program.nca is PROGRAM (not META) and readme.txt is not an NCA, so
        # only the UPDATE and BASE meta NCAs qualify.
        expect(meta_title_of(pkg, HEADER_KEY)).to_equal({UPDATE, BASE})

    @test(name="reads an XCI's secure partition")
    def from_xci(d: Annotated[Path, Depends(workdir)]) -> None:
        pkg: Path = d / 'game.xci'
        pkg.write_bytes(xci([('meta.cnmt.nca', nca_header(BASE, META))]))

        expect(meta_title_of(pkg, HEADER_KEY)).to_equal({BASE})

    @test(name='an empty set for unknown magic, a short NCA, or the wrong key')
    def none_found(d: Annotated[Path, Depends(workdir)]) -> None:
        pkg: Path = d / 'game.nsp'
        pkg.write_bytes(pfs0([('nca0.nca', nca_header(BASE, META, magic=b'NCA0')), ('short.nca', bytes(0x300))]))
        good: Path = d / 'good.nsp'
        good.write_bytes(pfs0([('meta.cnmt.nca', nca_header(BASE, META))]))

        expect(meta_title_of(pkg, HEADER_KEY)).to_equal(set())
        expect(meta_title_of(good, bytes(reversed(HEADER_KEY)))).to_equal(set())

    @test(name='two META NCAs for two base games return both')
    def two_base_metas(d: Annotated[Path, Depends(workdir)]) -> None:
        # Validates: Requirements 2.13
        pkg: Path = d / 'collection.xci'
        pkg.write_bytes(xci([('a.cnmt.nca', nca_header(BASE, META)), ('b.cnmt.nca', nca_header(OTHER_BASE, META))]))

        expect(meta_title_of(pkg, HEADER_KEY)).to_equal({BASE, OTHER_BASE})

    @test(name='a non-META NCA is excluded')
    def non_meta_excluded(d: Annotated[Path, Depends(workdir)]) -> None:
        # Validates: Requirements 2.13
        pkg: Path = d / 'game.nsp'
        pkg.write_bytes(pfs0([('program.nca', nca_header(BASE, PROGRAM))]))

        expect(meta_title_of(pkg, HEADER_KEY)).to_equal(set())

    @test(name='no meta NCA returns an empty set')
    def no_meta(d: Annotated[Path, Depends(workdir)]) -> None:
        # Validates: Requirements 2.13
        pkg: Path = d / 'game.nsp'
        pkg.write_bytes(pfs0([('readme.txt', b'not an nca')]))

        expect(meta_title_of(pkg, HEADER_KEY)).to_equal(set())


with describe('title_ids_of'):

    @test(name="the ticket's name wins over a [title ID] tag")
    def ticket_first(d: Annotated[Path, Depends(workdir)]) -> None:
        pkg: Path = d / f'Game [{OTHER_BASE:016X}].nsp'
        pkg.write_bytes(pfs0([(ticket_name(BASE), b''), ('a.nca', b'')]))

        expect(title_ids_of(pkg, _no_key)).to_equal({BASE})

    @test(name='a [title ID] tag, in either case, when there is no ticket')
    def tag(d: Annotated[Path, Depends(workdir)]) -> None:
        upper: Path = d / f'Game [{UPDATE:016X}][v65536].nsp'
        lower: Path = d / f'Game [{UPDATE:016x}].nsz'
        upper.write_bytes(pfs0([('a.nca', b'')]))
        lower.write_bytes(pfs0([('a.nca', b'')]))

        expect(title_ids_of(upper, _no_key)).to_equal({UPDATE})
        expect(title_ids_of(lower, _no_key)).to_equal({UPDATE})

    @test(name='the meta NCA when there is neither, only when a key is available')
    def meta_fallback(d: Annotated[Path, Depends(workdir)]) -> None:
        pkg: Path = d / 'Game.nsp'
        pkg.write_bytes(pfs0([('meta.cnmt.nca', nca_header(DLC_1, META))]))
        no_key: Callable[[], bytes | None] | None = None

        expect(title_ids_of(pkg, _key)).to_equal({DLC_1})
        expect(title_ids_of(pkg, no_key)).to_equal(set())

    @test(name='a None/no-key callable gives an empty set for a meta-only package')
    def meta_only_no_key(d: Annotated[Path, Depends(workdir)]) -> None:
        # Validates: Requirements 2.15
        pkg: Path = d / 'Game.nsp'
        pkg.write_bytes(pfs0([('meta.cnmt.nca', nca_header(BASE, META))]))
        no_key: Callable[[], bytes | None] | None = None

        expect(title_ids_of(pkg, no_key)).to_equal(set())

    @test(name='the key callable is not invoked for a ticket or tag package')
    def key_not_called(d: Annotated[Path, Depends(workdir)]) -> None:
        # _no_key raises if title_ids_of() ever calls it, so a passing test
        # proves the ticket-first / tag short-circuit before the meta NCA step.
        # Validates: Requirements 2.13
        ticket: Path = d / 'Ticket.nsp'
        ticket.write_bytes(pfs0([(ticket_name(BASE), b'')]))
        tagged: Path = d / f'Tagged [{OTHER_BASE:016X}].nsp'
        tagged.write_bytes(pfs0([('a.nca', b'')]))

        expect(title_ids_of(ticket, _no_key)).to_equal({BASE})
        expect(title_ids_of(tagged, _no_key)).to_equal({OTHER_BASE})

    @test(name='two tickets give a two-element set')
    def two_tickets(d: Annotated[Path, Depends(workdir)]) -> None:
        # Validates: Requirements 2.13
        pkg: Path = d / 'Collection.nsp'
        pkg.write_bytes(pfs0([(ticket_name(BASE), b''), (ticket_name(OTHER_BASE), b'')]))

        expect(title_ids_of(pkg, _no_key)).to_equal({BASE, OTHER_BASE})

    @test(name='one ticket gives a one-element set')
    def one_ticket(d: Annotated[Path, Depends(workdir)]) -> None:
        # Validates: Requirements 2.13
        pkg: Path = d / 'Game.nsp'
        pkg.write_bytes(pfs0([(ticket_name(BASE), b'')]))

        expect(title_ids_of(pkg, _no_key)).to_equal({BASE})


with describe('application_of'):

    @test.cases(
        test.case('base game', tid=BASE, expected=BASE),
        test.case('update', tid=UPDATE, expected=BASE),
        test.case('first DLC', tid=DLC_1, expected=BASE),
        test.case('last DLC', tid=BASE + 0x1000 + 0x7FF, expected=BASE),
    )
    def normalizes(tid: int, expected: int) -> None:
        expect(application_of(tid)).to_equal(expected)

    @test(name='base, update, and DLC indexes 1..2000 all fold to the base (seeded)')
    def folds_base_update_dlc() -> None:
        # Property 4, 8 (preservation): application_of() returns the base game
        # for a base ID, its update (base + 0x800), and every DLC index 1..2000.
        # A seeded loop stands in for a property generator (no hypothesis dep).
        # Validates: Requirements 3.5
        rng = random.Random(0)

        for _ in range(1000):
            # an application ID with low 13 bits clear, so base + 0x800
            # (update) and base + 0x1000 + i (DLC) stay inside one base game
            b: int = rng.randrange(0x0100000000010000, 0x01FFFFFFFFFFE000 + 1, 0x2000)
            i: int = rng.randint(1, 2000)

            expect(application_of(b)).to_equal(b)
            expect(application_of(b + 0x800)).to_equal(b)
            expect(application_of(b + 0x1000 + i)).to_equal(b)


with describe('multi-title fold'):

    @test(name='every base game in one package folds back to its set, exit 3 vs 0 (seeded)')
    def multi_title_folds(d: Annotated[Path, Depends(workdir)]) -> None:
        # Property 9: a single package whose tickets fold through application_of()
        # to a set of distinct base games B contributes every one, so the result
        # set equals B, and the CLI on a folder with just that package exits 3
        # when |B| > 1 and 0 when |B| == 1. A seeded loop stands in for a
        # property generator (no hypothesis dep).
        # Validates: Requirements 2.13, 2.14, 3.12
        rng = random.Random(0)

        for n in range(30):
            # a set of distinct base games, each with low 13 bits clear so its
            # update (base + 0x800) and DLC (base + 0x1000 + i) stay in the base
            size: int = rng.randint(1, 4)
            bases: set[int] = set()

            while len(bases) < size:
                bases.add(rng.randrange(0x0100000000010000, 0x01FFFFFFFFFFE000 + 1, 0x2000))

            tickets: list[tuple[str, bytes]] = []

            # one ticket per base, plus some update/DLC-shaped tickets off a base
            for base in bases:
                tickets.append((ticket_name(base), b''))

                if rng.random() < 0.5:
                    tickets.append((ticket_name(base + 0x800), b''))

                if rng.random() < 0.5:
                    tickets.append((ticket_name(base + 0x1000 + rng.randint(1, 2000)), b''))

            rng.shuffle(tickets)

            games: Path = d / f'games{n}'
            games.mkdir()
            (games / 'Collection.nsp').write_bytes(pfs0(tickets))

            no_key: Callable[[], bytes | None] | None = None
            found: set[int] = {application_of(t) for t in title_ids_of(games / 'Collection.nsp', no_key)}

            expect(found).to_equal(bases)

            # the CLI on a folder with only this package: HOME is the workdir
            result: Run = run_cli([str(games)], d)

            expect(result.code).to_equal(3 if len(bases) > 1 else 0)


with describe('real NSPs'):

    @test.skip_if(not HAVE_REAL_NSPS, reason=NO_REAL_NSPS)
    @test.cases(
        test.case('01004B800AF5A000', tid=0x01004B800AF5A000),
        test.case('01009C100390E000', tid=0x01009C100390E000),
        test.case('0100D9B00B666000', tid=0x0100D9B00B666000),
    )
    def real_ticket(tid: int) -> None:
        # the ticket answers, so the keys are never asked for
        found: set[int] = title_ids_of(real_nsp(tid), _no_key)

        expect(found).to_equal({tid})
        expect(application_of(tid)).to_equal(tid)

    @test.skip_if(not HAVE_REAL_NSPS or not HAVE_REAL_KEYS, reason='needs the real .nsp files and ~/.switch/prod.keys')
    @test.cases(
        test.case('01004B800AF5A000', tid=0x01004B800AF5A000),
        test.case('01009C100390E000', tid=0x01009C100390E000),
        test.case('0100D9B00B666000', tid=0x0100D9B00B666000),
    )
    def real_meta(tid: int) -> None:
        # decrypts the real NCA headers; the key itself never reaches the output
        expect(meta_title_of(real_nsp(tid), read_header_key(REAL_KEYS))).to_equal({tid})


with describe('exefs_section'):

    @test(name='returns the exact exefs bytes for a None-encrypted section')
    def none_exact() -> None:
        # Validates: Requirements 2.1
        # The builder lays the exefs right after the 0xC00 header region with a
        # PartitionFS/None FsEntry+FsHeader whose PfsOffset/PfsSize name it;
        # exefs_section must hand back exactly the PFS0 bytes.
        module_id: bytes = bytes(range(8))
        blob: bytes = program_nca(module_id)
        f: BufferedReader = reader(blob)

        header: bytes = decrypt_nca_header(HEADER_KEY, blob[:HEADER_REGION])
        section: bytes | None = exefs_section(f, 0, header, None)

        expect(section).to_equal(exefs(module_id))

    @test(name='honors a non-zero nca_at when locating the section')
    def none_offset() -> None:
        # Validates: Requirements 2.1
        # The PFS0 offset is nca_at + StartOffset*0x200 + PfsOffset, so a
        # program NCA placed partway through the reader must still resolve to the
        # same exefs bytes when nca_at names where it starts.
        module_id: bytes = bytes(range(4))
        blob: bytes = program_nca(module_id)
        pad: int = 0x400
        f: BufferedReader = reader(bytes(pad) + blob)

        header: bytes = decrypt_nca_header(HEADER_KEY, blob[:HEADER_REGION])
        section: bytes | None = exefs_section(f, pad, header, None)

        expect(section).to_equal(exefs(module_id))

    @test(name='returns None when no PartitionFS section is present')
    def absent_section() -> None:
        # Validates: Requirements 4.3
        # Every FsHeader slot defaults to FsType 0, so a header whose only
        # populated slot is a non-PartitionFS type (here FsType 2) offers no
        # exefs for exefs_section to find.
        header: bytes = _decrypted_header(fs_type=2, encryption_type=1, blocks=1)
        f: BufferedReader = reader(header + bytes(SECTOR))

        expect(exefs_section(f, 0, header, None)).to_equal(None)

    @test(name='returns None for an unsupported EncryptionType')
    def unsupported_encryption() -> None:
        # Validates: Requirements 4.3
        # A PartitionFS section declaring AesCtr (3) is the retail path the None
        # reader does not implement yet, so the whole package is skipped rather
        # than a half-decrypted section returned.
        header: bytes = _decrypted_header(fs_type=1, encryption_type=3, blocks=1)
        f: BufferedReader = reader(header + bytes(SECTOR))

        expect(exefs_section(f, 0, header, None)).to_equal(None)


with describe('module_id_of'):

    @test(name='round-trips any 0..32-byte ModuleId through a synthetic program NCA (seeded)')
    def module_id_round_trip(d: Annotated[Path, Depends(workdir)]) -> None:
        # Feature: build-id, Property 1: ModuleId round-trip
        # For any ModuleId up to 32 bytes, embedding it in a synthetic program
        # NCA's main NSO and extracting via module_id_of returns the exact bytes
        # present, padded to MODULE_ID_SIZE. module_id_of returns the whole
        # fixed 0x20 field bytes[0x40:0x60], and the builder lays the ModuleId
        # at 0x40 zero-padded to 0x20, so the honest round-trip is the exact
        # bytes followed by zero padding to MODULE_ID_SIZE. A seeded loop stands
        # in for a property generator (no hypothesis dep).
        # Validates: Requirements 2.1, 6.2
        rng = random.Random(0)

        for i in range(100):
            module_id: bytes = rng.randbytes(rng.randint(0, MODULE_ID_SIZE))
            pkg: Path = d / f'game{i}.nsp'
            pkg.write_bytes(program_package(BASE, module_id))

            extracted: bytes | None = module_id_of(pkg, HEADER_KEY, None)

            expect(extracted).to_equal(module_id.ljust(MODULE_ID_SIZE, b'\0'))

    @test(name='renders the first 8 bytes of any 8..32-byte ModuleId as 16 uppercase hex digits (seeded)')
    def hex_rendering() -> None:
        # Feature: build-id, Property 2: Build-ID rendering is the first 8 bytes as uppercase hex
        # For any ModuleId field of 8..32 bytes (module_id_of always yields the
        # full 32-byte field), render_build_id, the helper the CLI prints,
        # returns exactly 16 uppercase hex digits that decode back to the first
        # 8 bytes, ignoring everything after them. A seeded loop stands in for
        # a property generator (no hypothesis dep).
        # Validates: Requirements 2.2
        rng = random.Random(0)

        for _ in range(100):
            raw: bytes = rng.randbytes(rng.randint(BUILD_ID_SIZE, MODULE_ID_SIZE))
            rendered: str = render_build_id(raw)

            expect(rendered).to_equal(rendered.upper())
            expect(len(rendered)).to_equal(2 * BUILD_ID_SIZE)
            expect(bytes.fromhex(rendered.lower())).to_equal(raw[:BUILD_ID_SIZE])


with describe('update_version_of'):

    @test(name='reads the same version from an XCI as from an NSP')
    def same_version_nsp_and_xci(d: Annotated[Path, Depends(workdir)]) -> None:
        # The version lives inside the NCA header, so it must not depend on
        # which container (PFS0 vs XCI secure partition) wraps the meta NCA.
        meta: bytes = meta_nca(UPDATE, version=65536)
        nsp: Path = d / 'game.nsp'
        cart: Path = d / 'game.xci'
        nsp.write_bytes(pfs0([('meta.cnmt.nca', meta)]))
        cart.write_bytes(xci([('meta.cnmt.nca', meta)]))

        expect(update_version_of(nsp, HEADER_KEY)).to_equal(65536)
        expect(update_version_of(cart, HEADER_KEY)).to_equal(65536)


with describe('build_id_of'):

    @test(name='picks the newest update, any update over the base, base when alone (seeded)')
    def newest_update_selection(d: Annotated[Path, Depends(workdir)]) -> None:
        # Feature: build-id, Property 3: Newest-update selection is monotonic in version
        # For a base game plus a random set of update packages with distinct
        # embedded ModuleIds and distinct versions, build_id_of reports the
        # greatest-readable-version update's ModuleId; an update always beats
        # the base (even when every update version is unreadable, where the
        # least sorted path wins); and a base-only input resolves to the base's
        # ModuleId. Each ModuleId is padded to MODULE_ID_SIZE because
        # module_id_of returns the whole fixed 0x20 field. A seeded loop stands
        # in for a property generator (no hypothesis dep).
        # Validates: Requirements 3.1, 3.2, 3.3
        rng = random.Random(0)

        for i in range(100):
            scenario: Path = d / f'iter{i}'
            scenario.mkdir()

            # The base package: version=None (it is the base). Its ModuleId is
            # distinct from every update's, drawn without replacement below.
            update_count: int = rng.randint(0, 5)

            # Draw update_count + 1 distinct ModuleIds (one base, the rest
            # updates) without replacement, each a random 8..32-byte sequence
            # tagged with its index so collisions are impossible.
            module_ids: list[bytes] = []

            while len(module_ids) < update_count + 1:
                candidate: bytes = bytes([len(module_ids)]) + rng.randbytes(rng.randint(7, 31))

                if candidate not in module_ids:
                    module_ids.append(candidate)

            base_module_id: bytes = module_ids[0]
            update_module_ids: list[bytes] = module_ids[1:]

            base_path: Path = scenario / 'base.nsp'
            base_path.write_bytes(program_package(BASE, base_module_id))
            packages: list[tuple[Path, set[int]]] = [(base_path, {BASE})]

            # Some iterations make every update version unreadable (None -> the
            # builder writes 0 -> update_version_of returns None), exercising
            # the all-unreadable fallback; otherwise draw distinct positive
            # versions so the greatest is unambiguous.
            unreadable: bool = update_count > 0 and rng.random() < 0.3
            versions: list[int | None] = []

            if unreadable:
                versions = [None] * update_count
            else:
                drawn: set[int] = set()

                while len(drawn) < update_count:
                    drawn.add(rng.randint(1, 1_000_000))

                versions = list(drawn)
                rng.shuffle(versions)

            # Write each update to a distinct filename so the paths have a known
            # sort order (update0.nsp < update1.nsp < ...); this lets the
            # all-unreadable case be asserted precisely as the least path.
            update_paths: list[Path] = []

            for j, (update_module_id, version) in enumerate(zip(update_module_ids, versions, strict=True)):
                update_path: Path = scenario / f'update{j}.nsp'
                update_path.write_bytes(program_package(UPDATE, update_module_id, version=version))
                update_paths.append(update_path)
                packages.append((update_path, {UPDATE}))

            result: bytes | None = build_id_of(packages, BASE, HEADER_KEY, None)

            if update_count == 0:
                # Base-only input resolves to the base package's ModuleId.
                expect(result).to_equal(base_module_id.ljust(MODULE_ID_SIZE, b'\0'))

                continue

            if unreadable:
                # Every update version is unreadable, so build_id_of falls back
                # to the least sorted update path; update0.nsp sorts least.
                least_path: Path = min(update_paths)
                least_index: int = update_paths.index(least_path)
                expected: bytes = update_module_ids[least_index].ljust(MODULE_ID_SIZE, b'\0')
            else:
                # The greatest readable version wins; versions are distinct, so
                # the max is unambiguous and beats the base.
                best_version: int = max(v for v in versions if v is not None)
                best_index: int = versions.index(best_version)
                expected = update_module_ids[best_index].ljust(MODULE_ID_SIZE, b'\0')

            expect(result).to_equal(expected)
            expect(result).not_.to_equal(base_module_id.ljust(MODULE_ID_SIZE, b'\0'))

    @test(name='never resolves a DLC package, even when it is the only package (seeded)')
    def dlc_exclusion(d: Annotated[Path, Depends(workdir)]) -> None:
        # Feature: build-id, Property 4: DLC packages never supply the Build ID
        # For DLC packages (title ID BASE + 0x1000 + i) mixed with an optional
        # base and optional updates, each carrying a distinct embedded
        # ModuleId, build_id_of never reports any DLC's ModuleId. DLC-only
        # input resolves to None; otherwise it resolves to the max-version
        # update's ModuleId, or the base's when there are no updates. Each
        # ModuleId is padded to MODULE_ID_SIZE because module_id_of returns the
        # whole fixed 0x20 field. A seeded loop stands in for a property
        # generator (no hypothesis dep).
        # Validates: Requirements 3.4
        rng = random.Random(0)

        for i in range(100):
            scenario: Path = d / f'iter{i}'
            scenario.mkdir()

            # Cycle the shapes so every one is exercised many times: DLC only,
            # DLC + base, and DLC + base + updates.
            shape: int = i % 3
            dlc_count: int = rng.randint(1, 4)
            update_count: int = rng.randint(1, 4) if shape == 2 else 0
            has_base: bool = shape != 0

            # Distinct ModuleIds across base, updates, and DLCs, tagged with
            # their index so collisions are impossible.
            total: int = dlc_count + update_count + (1 if has_base else 0)
            module_ids: list[bytes] = []

            while len(module_ids) < total:
                candidate: bytes = bytes([len(module_ids)]) + rng.randbytes(rng.randint(7, 31))

                if candidate not in module_ids:
                    module_ids.append(candidate)

            dlc_module_ids: list[bytes] = module_ids[:dlc_count]
            update_module_ids: list[bytes] = module_ids[dlc_count : dlc_count + update_count]
            base_module_ids: list[bytes] = module_ids[dlc_count + update_count :]

            packages: list[tuple[Path, set[int]]] = []

            # Distinct DLC title IDs: BASE + 0x1000 + n, n in 1..2000.
            dlc_offsets: list[int] = rng.sample(range(1, 2001), dlc_count)

            for j, (dlc_module_id, offset) in enumerate(zip(dlc_module_ids, dlc_offsets, strict=True)):
                dlc_id: int = BASE + 0x1000 + offset
                dlc_path: Path = scenario / f'dlc{j}.nsp'
                dlc_path.write_bytes(program_package(dlc_id, dlc_module_id))
                packages.append((dlc_path, {dlc_id}))

            expected: bytes | None = None

            if has_base:
                base_path: Path = scenario / 'base.nsp'
                base_path.write_bytes(program_package(BASE, base_module_ids[0]))
                packages.append((base_path, {BASE}))
                expected = base_module_ids[0].ljust(MODULE_ID_SIZE, b'\0')

            if update_count:
                # Distinct readable versions make the newest update unambiguous.
                versions: list[int] = rng.sample(range(1, 1_000_001), update_count)

                for j, (update_module_id, version) in enumerate(zip(update_module_ids, versions, strict=True)):
                    update_path: Path = scenario / f'update{j}.nsp'
                    update_path.write_bytes(program_package(UPDATE, update_module_id, version=version))
                    packages.append((update_path, {UPDATE}))

                expected = update_module_ids[versions.index(max(versions))].ljust(MODULE_ID_SIZE, b'\0')

            rng.shuffle(packages)

            result: bytes | None = build_id_of(packages, BASE, HEADER_KEY, None)

            expect(result).to_equal(expected)

            for dlc_module_id in dlc_module_ids:
                expect(result).not_.to_equal(dlc_module_id.ljust(MODULE_ID_SIZE, b'\0'))


# ------------------------------------------------------------------------------
# TITLEKEY (RIGHTS-ID) PATH

TITLE_KEY: bytes = bytes(range(0x40, 0x50))
TITLEKEK: bytes = bytes(range(0x90, 0xA0))
MODULE_ID: bytes = bytes(range(1, 0x21))


def _kek(generation: int) -> bytes | None:
    """
    A titlekek lookup holding only generation 0 and 2.
    """
    return {0: TITLEKEK, 2: bytes(16)}.get(generation)


with describe('read_titlekeks'):

    @test(name='finds titlekek_XX lines leniently and ignores other keys')
    def finds_titlekeks(d: Annotated[Path, Depends(workdir)]) -> None:
        keys: Path = write_titlekek_keys(d / 'prod.keys', 2, TITLEKEK)
        keys.write_text(keys.read_text() + f'  TITLEKEK_0A = {TITLEKEK.hex().upper()}  \n')

        expect(read_titlekeks(keys)).to_equal({2: TITLEKEK})


with describe('title_key_of'):

    @test(name='decrypts the titlekey for every supported signature type')
    def decrypts_all_sig_types() -> None:
        rid: bytes = rights_id_of(BASE)

        for sig_type in (0x10003, 0x10004, 0x10005, 0x10006):
            tik: bytes = ticket(rid, TITLE_KEY, TITLEKEK, sig_type=sig_type)

            expect(title_key_of(tik, rid, _kek)).to_equal(TITLE_KEY)

    @test(name='maps rights ID revisions 0 and 1 to generation 0, else value - 1')
    def revision_to_generation() -> None:
        for revision, kek in ((0, TITLEKEK), (1, TITLEKEK), (3, bytes(16))):
            rid: bytes = rights_id_of(BASE, revision)
            tik: bytes = ticket(rid, TITLE_KEY, kek)

            expect(title_key_of(tik, rid, _kek)).to_equal(TITLE_KEY)

    @test(name='returns None for personalized, unknown-type, truncated, and keyless')
    def unsupported_is_none() -> None:
        rid: bytes = rights_id_of(BASE)
        good: bytes = ticket(rid, TITLE_KEY, TITLEKEK)

        expect(title_key_of(ticket(rid, TITLE_KEY, TITLEKEK, key_type=1), rid, _kek)).to_equal(None)
        expect(title_key_of(b'\x01\x00\x01\x7f' + good[4:], rid, _kek)).to_equal(None)
        expect(title_key_of(good[:0x200], rid, _kek)).to_equal(None)
        expect(title_key_of(b'', rid, _kek)).to_equal(None)
        expect(title_key_of(good, rid, None)).to_equal(None)
        expect(title_key_of(good, rights_id_of(BASE, 9), _kek)).to_equal(None)


with describe('titlekey program NCA'):

    @test(name='round-trips a ModuleId through the ticket titlekey path')
    def titlekey_round_trip(d: Annotated[Path, Depends(workdir)]) -> None:
        rid: bytes = rights_id_of(BASE)
        pkg: Path = d / 'game.nsp'
        pkg.write_bytes(titlekey_package(BASE, MODULE_ID, TITLE_KEY, ticket(rid, TITLE_KEY, TITLEKEK)))

        expect(module_id_of(pkg, HEADER_KEY, None, _kek)).to_equal(MODULE_ID)

    @test(name='returns None without a ticket, a titlekek, or a common ticket')
    def titlekey_skips(d: Annotated[Path, Depends(workdir)]) -> None:
        rid: bytes = rights_id_of(BASE)
        cases: dict[str, bytes] = {
            'none': titlekey_package(BASE, MODULE_ID, TITLE_KEY, None),
            'personalized': titlekey_package(BASE, MODULE_ID, TITLE_KEY, ticket(rid, TITLE_KEY, TITLEKEK, key_type=1)),
            'bad-sig': titlekey_package(
                BASE, MODULE_ID, TITLE_KEY, b'\x01\x00\x01\x7f' + ticket(rid, TITLE_KEY, TITLEKEK)[4:]
            ),
        }

        for label, data in cases.items():
            pkg: Path = d / f'{label}.nsp'
            pkg.write_bytes(data)

            expect(module_id_of(pkg, HEADER_KEY, None, _kek)).to_equal(None)

        good: Path = d / 'good.nsp'
        good.write_bytes(titlekey_package(BASE, MODULE_ID, TITLE_KEY, ticket(rid, TITLE_KEY, TITLEKEK)))

        expect(module_id_of(good, HEADER_KEY, None, None)).to_equal(None)
        expect(module_id_of(good, HEADER_KEY, None, lambda _g: None)).to_equal(None)

    @test(name='AES-CTR with a zero RightsId decrypts via the key area (counter check)')
    def key_area_ctr_round_trip(d: Annotated[Path, Depends(workdir)]) -> None:
        kak: bytes = bytes(range(0x10, 0x20))
        pkg: Path = d / 'game.nsp'
        pkg.write_bytes(
            pfs0([('program.nca', program_nca(MODULE_ID, section_key=TITLE_KEY, key_area_key=kak, key_generation=2))])
        )

        expect(module_id_of(pkg, HEADER_KEY, lambda g: kak if g == 2 else None)).to_equal(MODULE_ID)
        expect(module_id_of(pkg, HEADER_KEY, lambda g: None)).to_equal(None)
