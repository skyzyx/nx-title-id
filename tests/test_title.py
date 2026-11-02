"""
Title ID resolution: prod.keys parsing, Nintendo AES-XTS, meta NCAs, the
ticket → tag → meta NCA order, and base-game normalization.
"""

import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated

from tryke import Depends, describe, expect, fixture, test

from nx_title_id import application_of, meta_title_of, read_header_key, title_of, xts_decrypt_sector
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
    nca_header,
    nintendo_xts_encrypt,
    pfs0,
    real_nsp,
    ticket_name,
    write_keys,
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


def _key() -> bytes:
    """
    The header_key callable title_of() expects.
    """
    return HEADER_KEY


def _no_key() -> bytes:
    """
    A header_key callable that fails the test if title_of() calls it.
    """
    raise AssertionError('header_key() was called')


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


with describe('meta_title_of'):

    @test(name='the lowest title ID among the meta NCAs, ignoring other content types')
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

        expect(meta_title_of(pkg, HEADER_KEY)).to_equal(BASE)

    @test(name="reads an XCI's secure partition")
    def from_xci(d: Annotated[Path, Depends(workdir)]) -> None:
        pkg: Path = d / 'game.xci'
        pkg.write_bytes(xci([('meta.cnmt.nca', nca_header(BASE, META))]))

        expect(meta_title_of(pkg, HEADER_KEY)).to_equal(BASE)

    @test(name='None for unknown magic, a short NCA, or the wrong key')
    def none_found(d: Annotated[Path, Depends(workdir)]) -> None:
        pkg: Path = d / 'game.nsp'
        pkg.write_bytes(pfs0([('nca0.nca', nca_header(BASE, META, magic=b'NCA0')), ('short.nca', bytes(0x300))]))
        good: Path = d / 'good.nsp'
        good.write_bytes(pfs0([('meta.cnmt.nca', nca_header(BASE, META))]))

        expect(meta_title_of(pkg, HEADER_KEY)).to_be_none()
        expect(meta_title_of(good, bytes(reversed(HEADER_KEY)))).to_be_none()


with describe('title_of'):

    @test(name="the ticket's name wins over a [title ID] tag")
    def ticket_first(d: Annotated[Path, Depends(workdir)]) -> None:
        pkg: Path = d / f'Game [{OTHER_BASE:016X}].nsp'
        pkg.write_bytes(pfs0([(ticket_name(BASE), b''), ('a.nca', b'')]))

        expect(title_of(pkg, _no_key)).to_equal(BASE)

    @test(name='a [title ID] tag, in either case, when there is no ticket')
    def tag(d: Annotated[Path, Depends(workdir)]) -> None:
        upper: Path = d / f'Game [{UPDATE:016X}][v65536].nsp'
        lower: Path = d / f'Game [{UPDATE:016x}].nsz'
        upper.write_bytes(pfs0([('a.nca', b'')]))
        lower.write_bytes(pfs0([('a.nca', b'')]))

        expect(title_of(upper, _no_key)).to_equal(UPDATE)
        expect(title_of(lower, _no_key)).to_equal(UPDATE)

    @test(name='the meta NCA when there is neither, only when a key is available')
    def meta_fallback(d: Annotated[Path, Depends(workdir)]) -> None:
        pkg: Path = d / 'Game.nsp'
        pkg.write_bytes(pfs0([('meta.cnmt.nca', nca_header(DLC_1, META))]))
        no_key: Callable[[], bytes] | None = None

        expect(title_of(pkg, _key)).to_equal(DLC_1)
        expect(title_of(pkg, no_key)).to_be_none()


with describe('application_of'):

    @test.cases(
        test.case('base game', tid=BASE, expected=BASE),
        test.case('update', tid=UPDATE, expected=BASE),
        test.case('first DLC', tid=DLC_1, expected=BASE),
        test.case('last DLC', tid=BASE + 0x1000 + 0x7FF, expected=BASE),
    )
    def normalizes(tid: int, expected: int) -> None:
        expect(application_of(tid)).to_equal(expected)


with describe('real NSPs'):

    @test.skip_if(not HAVE_REAL_NSPS, reason=NO_REAL_NSPS)
    @test.cases(
        test.case('01004B800AF5A000', tid=0x01004B800AF5A000),
        test.case('01009C100390E000', tid=0x01009C100390E000),
        test.case('0100D9B00B666000', tid=0x0100D9B00B666000),
    )
    def real_ticket(tid: int) -> None:
        # the ticket answers, so the keys are never asked for
        found: int | None = title_of(real_nsp(tid), _no_key)

        expect(found).to_equal(tid)
        expect(application_of(tid)).to_equal(tid)

    @test.skip_if(not HAVE_REAL_NSPS or not HAVE_REAL_KEYS, reason='needs the real .nsp files and ~/.switch/prod.keys')
    @test.cases(
        test.case('01004B800AF5A000', tid=0x01004B800AF5A000),
        test.case('01009C100390E000', tid=0x01009C100390E000),
        test.case('0100D9B00B666000', tid=0x0100D9B00B666000),
    )
    def real_meta(tid: int) -> None:
        # decrypts the real NCA headers; the key itself never reaches the output
        expect(meta_title_of(real_nsp(tid), read_header_key(REAL_KEYS))).to_equal(tid)
