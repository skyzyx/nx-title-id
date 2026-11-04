"""
Title ID resolution: prod.keys parsing, Nintendo AES-XTS, meta NCAs, the
ticket → tag → meta NCA order, and base-game normalization.
"""

import os
import random
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated

from tryke import Depends, describe, expect, fixture, test

from nx_title_id import application_of, meta_title_of, read_header_key, title_ids_of, xts_decrypt_sector
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
    nca_header,
    nintendo_xts_encrypt,
    pfs0,
    real_nsp,
    run_cli,
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
