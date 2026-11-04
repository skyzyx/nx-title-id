"""
PFS0 / HFS0 / XCI container parsing: partition(), package_files(), is_package().
"""

import random
import struct
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from tryke import Depends, describe, expect, fixture, test

from nx_title_id import MAX_ENTRIES, MAX_STRINGS_SIZE, MalformedPackageError, is_package, package_files, partition
from tests.builders import (
    HAVE_REAL_NSPS,
    HFS0_ENTRY_SIZE,
    NO_REAL_NSPS,
    PFS0_ENTRY_SIZE,
    hfs0,
    pfs0,
    reader,
    real_nsp,
    xci,
)

# ------------------------------------------------------------------------------
# FIXTURES


@fixture
def workdir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ------------------------------------------------------------------------------
# TESTS

with describe('partition'):

    @test(name='lists PFS0 names with absolute data offsets')
    def lists_pfs0() -> None:
        data: bytes = pfs0([('a.nca', b'AAAA'), ('b.tik', b'BB')])
        entries: list[tuple[str, int]] = partition(reader(data), 0, b'PFS0', PFS0_ENTRY_SIZE)

        expect([name for name, _ in entries]).to_equal(['a.nca', 'b.tik'])
        expect(data[entries[0][1] : entries[0][1] + 4]).to_equal(b'AAAA')
        expect(data[entries[1][1] : entries[1][1] + 2]).to_equal(b'BB')

    @test(name='returns [] for the wrong magic')
    def wrong_magic() -> None:
        expect(partition(reader(hfs0([('x', b'')])), 0, b'PFS0', PFS0_ENTRY_SIZE)).to_equal([])

    @test(name='returns [] for a header shorter than 16 bytes')
    def short_header() -> None:
        expect(partition(reader(b'PFS0\0\0'), 0, b'PFS0', PFS0_ENTRY_SIZE)).to_equal([])

    @test(name='an empty partition has no entries')
    def empty() -> None:
        expect(partition(reader(pfs0([])), 0, b'PFS0', PFS0_ENTRY_SIZE)).to_equal([])

    @test(name='raises MalformedPackageError for an unterminated name')
    def unterminated_name() -> None:
        entry: bytes = struct.pack('<QQI', 0, 0, 0).ljust(PFS0_ENTRY_SIZE, b'\0')
        data: bytes = b'PFS0' + struct.pack('<III', 1, 5, 0) + entry + b'x.nca'

        expect(lambda: partition(reader(data), 0, b'PFS0', PFS0_ENTRY_SIZE)).to_raise(
            MalformedPackageError, match='unterminated'
        )

    @test(name='raises MalformedPackageError when entries are truncated')
    def truncated_entries() -> None:
        data: bytes = b'PFS0' + struct.pack('<III', 0xFFFF, 0, 0)

        expect(lambda: partition(reader(data), 0, b'PFS0', PFS0_ENTRY_SIZE)).to_raise(
            MalformedPackageError, match='truncated'
        )

    @test(name='raises MalformedPackageError when the string table is truncated')
    def truncated_strings() -> None:
        entry: bytes = struct.pack('<QQI', 0, 0, 0).ljust(PFS0_ENTRY_SIZE, b'\0')
        data: bytes = b'PFS0' + struct.pack('<III', 1, 64, 0) + entry + b'a\0'

        expect(lambda: partition(reader(data), 0, b'PFS0', PFS0_ENTRY_SIZE)).to_raise(
            MalformedPackageError, match='truncated'
        )

    @test(name='within-limit headers parse to the builder entries (seeded)')
    def within_limits_round_trip() -> None:
        # Property 3, 8 (preservation): a valid PFS0 or HFS0 header within the
        # sanity limits parses to the same names, in order, each at the offset
        # where its data bytes actually are. A seeded loop stands in for a
        # property generator (no hypothesis dep).
        # Validates: Requirements 3.6
        rng = random.Random(0)

        for _ in range(200):
            count: int = rng.randint(0, 20)
            files: list[tuple[str, bytes]] = [
                (f'f{j}_{rng.randrange(0x1000):03x}.nca', bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 32))))
                for j in range(count)
            ]
            magic: bytes = rng.choice([b'PFS0', b'HFS0'])
            entry_size: int = PFS0_ENTRY_SIZE if magic == b'PFS0' else HFS0_ENTRY_SIZE
            data: bytes = pfs0(files) if magic == b'PFS0' else hfs0(files)
            entries: list[tuple[str, int]] = partition(reader(data), 0, magic, entry_size)

            expect([name for name, _ in entries]).to_equal([name for name, _ in files])

            for (_, body), (_, at) in zip(files, entries, strict=True):
                expect(data[at : at + len(body)]).to_equal(body)


with describe('package_files'):

    @test(name='an NSP lists its PFS0 files')
    def nsp() -> None:
        names: list[str] = [name for name, _ in package_files(reader(pfs0([('a.nca', b''), ('b.cnmt.nca', b'')])))]

        expect(names).to_equal(['a.nca', 'b.cnmt.nca'])

    @test(name="an XCI lists its secure partition's files, at their absolute offsets")
    def xci_secure() -> None:
        data: bytes = xci([('game.nca', b'GAME')])
        files: list[tuple[str, int]] = package_files(reader(data))

        expect([name for name, _ in files]).to_equal(['game.nca'])
        expect(data[files[0][1] : files[0][1] + 4]).to_equal(b'GAME')

    @test(name='an XCI without a secure partition has no files')
    def xci_no_secure() -> None:
        expect(package_files(reader(xci([('game.nca', b'')], partition_name='normal')))).to_equal([])

    @test(name='anything else has no files')
    def neither() -> None:
        expect(package_files(reader(b'\0' * 0x200))).to_equal([])

    @test(name='raises MalformedPackageError for a truncated card header')
    def truncated_card_header() -> None:
        data: bytes = bytes(0x100) + b'HEAD' + bytes(0x10)

        expect(lambda: package_files(reader(data))).to_raise(MalformedPackageError, match='card header')


with describe('is_package'):

    @test(name='accepts a PFS0 and a card header, rejects anything else')
    def magic(d: Annotated[Path, Depends(workdir)]) -> None:
        nsp: Path = d / 'a.nsp'
        card: Path = d / 'b.xci'
        junk: Path = d / 'c.nsp'
        tiny: Path = d / 'd.nsp'
        nsp.write_bytes(pfs0([]))
        card.write_bytes(xci([]))
        junk.write_bytes(b'not a package' * 100)
        tiny.write_bytes(b'PF')

        expect(is_package(nsp)).to_be_truthy()
        expect(is_package(card)).to_be_truthy()
        expect(is_package(junk)).to_be_falsy()
        expect(is_package(tiny)).to_be_falsy()


with describe('real NSPs'):

    @test.skip_if(not HAVE_REAL_NSPS, reason=NO_REAL_NSPS)
    @test.cases(
        test.case('01004B800AF5A000', tid=0x01004B800AF5A000),
        test.case('01009C100390E000', tid=0x01009C100390E000),
        test.case('0100D9B00B666000', tid=0x0100D9B00B666000),
    )
    def real_layout(tid: int) -> None:
        path: Path = real_nsp(tid)
        size: int = path.stat().st_size

        with open(path, 'rb') as f:
            files: list[tuple[str, int]] = package_files(f)

        names: list[str] = [name for name, _ in files]
        tickets: list[str] = [n for n in names if n.endswith('.tik')]

        expect(is_package(path)).to_be_truthy()
        expect(tickets).to_have_length(1)
        expect(tickets[0][:16]).to_equal(f'{tid:016x}')
        expect([n for n in names if n.endswith('.cnmt.nca')]).to_have_length(1)
        expect(all(0 < at < size for _, at in files)).to_be_truthy()


with describe('partition oversized headers (bug condition B)'):
    # The sanity limits come from the module: MAX_ENTRIES, MAX_STRINGS_SIZE.

    @test.cases(
        test.case('PFS0', magic=b'PFS0', entry_size=PFS0_ENTRY_SIZE),
        test.case('HFS0', magic=b'HFS0', entry_size=HFS0_ENTRY_SIZE),
    )
    def count_at_limit_passes_the_guard(magic: bytes, entry_size: int) -> None:
        # A header declaring exactly MAX_ENTRIES entries passes the limit guard;
        # the entry bytes are absent, so it fails as 'truncated', not 'limit'.
        # Validates: Requirements 2.3, 2.4
        data: bytes = magic + struct.pack('<III', MAX_ENTRIES, 0, 0)

        expect(lambda: partition(reader(data), 0, magic, entry_size)).to_raise(MalformedPackageError, match='truncated')

    @test.cases(
        test.case('PFS0', magic=b'PFS0', entry_size=PFS0_ENTRY_SIZE),
        test.case('HFS0', magic=b'HFS0', entry_size=HFS0_ENTRY_SIZE),
    )
    def oversized_entries(magic: bytes, entry_size: int) -> None:
        # A header declaring MAX_ENTRIES + 1 entries, with no entry bytes present:
        # a 'limit' error raised after reading only the 16-byte header.
        # Validates: Requirements 2.3, 2.4
        data: bytes = magic + struct.pack('<III', MAX_ENTRIES + 1, 0, 0)
        r = reader(data)

        expect(lambda: partition(r, 0, magic, entry_size)).to_raise(MalformedPackageError, match='limit')
        expect(r.tell()).to_equal(16)

    @test.cases(
        test.case('PFS0', magic=b'PFS0', entry_size=PFS0_ENTRY_SIZE),
        test.case('HFS0', magic=b'HFS0', entry_size=HFS0_ENTRY_SIZE),
    )
    def strings_at_limit_passes_the_guard(magic: bytes, entry_size: int) -> None:
        # A header declaring 0 entries and exactly MAX_STRINGS_SIZE string bytes
        # passes the limit guard; the string bytes are absent, so it fails as
        # 'truncated', not 'limit'.
        # Validates: Requirements 2.3, 2.4
        data: bytes = magic + struct.pack('<III', 0, MAX_STRINGS_SIZE, 0)

        expect(lambda: partition(reader(data), 0, magic, entry_size)).to_raise(MalformedPackageError, match='truncated')

    @test.cases(
        test.case('PFS0', magic=b'PFS0', entry_size=PFS0_ENTRY_SIZE),
        test.case('HFS0', magic=b'HFS0', entry_size=HFS0_ENTRY_SIZE),
    )
    def oversized_strings(magic: bytes, entry_size: int) -> None:
        # A header declaring 0 entries and a MAX_STRINGS_SIZE + 1 byte string
        # table, with no string bytes present: a 'limit' error raised after
        # reading only the 16-byte header.
        # Validates: Requirements 2.3, 2.4
        data: bytes = magic + struct.pack('<III', 0, MAX_STRINGS_SIZE + 1, 0)
        r = reader(data)

        expect(lambda: partition(r, 0, magic, entry_size)).to_raise(MalformedPackageError, match='limit')
        expect(r.tell()).to_equal(16)
