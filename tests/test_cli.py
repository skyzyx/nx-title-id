"""
The nx-title-id command end to end, run as `python -m nx_title_id` in a
subprocess with its own HOME (Tryke workers use stdout to talk to the runner).
"""

import os
import random
import re
import struct
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from tryke import Depends, describe, expect, fixture, test

from nx_title_id import __version__, read_header_key
from tests.builders import (
    BASE,
    DLC_1,
    HAVE_FF_XCI,
    HAVE_REAL_KEYS,
    HAVE_REAL_NSPS,
    HEADER_KEY,
    META,
    NO_FF_XCI,
    NO_REAL_NSPS,
    OTHER_BASE,
    PFS0_ENTRY_SIZE,
    REAL_KEYS,
    REAL_TITLE_IDS,
    TESTS_DIR,
    UPDATE,
    Run,
    nca_header,
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
def home() -> Iterator[Path]:
    """
    An empty HOME with a "games" folder in it.
    """
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / 'games').mkdir()
        yield Path(d)


# ------------------------------------------------------------------------------
# PRIVATE FUNCTIONS


def _games(home: Path) -> Path:
    return home / 'games'


def _ids(*title_ids: int) -> str:
    return ''.join(f'{tid:016X}\n' for tid in title_ids)


# ------------------------------------------------------------------------------
# TESTS

with describe('one game'):

    @test(name='base, update and DLC print the base game once')
    def one_game(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(pfs0([(ticket_name(BASE), b'')]))
        (games / f'Game [{UPDATE:016X}].nsp').write_bytes(pfs0([('a.nca', b'')]))
        (games / 'Game DLC.NSZ').write_bytes(pfs0([(ticket_name(DLC_1), b'')]))
        result: Run = run_cli([str(games)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(BASE))
        expect(result.stderr).to_equal('')

    @test.skip_if(not HAVE_REAL_NSPS, reason=NO_REAL_NSPS)
    @test.cases(
        test.case('01004B800AF5A000', tid=0x01004B800AF5A000),
        test.case('01009C100390E000', tid=0x01009C100390E000),
        test.case('0100D9B00B666000', tid=0x0100D9B00B666000),
    )
    def real_nsp_alone(tid: int, h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Game.nsp').symlink_to(real_nsp(tid))
        result: Run = run_cli([str(games)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(tid))
        expect(result.stderr).to_equal('')

    @test(name='an XCI with a tag')
    def xci_tag(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / f'Game [{BASE:016X}].xci').write_bytes(xci([('a.nca', b'')]))
        result: Run = run_cli([str(games)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(BASE))

    @test(name='one package with a base, its update and its DLC prints one base game')
    def base_update_dlc_in_one_package(h: Annotated[Path, Depends(home)]) -> None:
        # 3.12: a package holding a base game, its update, and its DLC resolves
        # to exactly one base game ID and, alone in the folder, exits 0. All
        # three tickets fold through application_of() to BASE.
        # Validates: Requirements 3.12
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(
            pfs0([(ticket_name(BASE), b''), (ticket_name(UPDATE), b''), (ticket_name(DLC_1), b'')])
        )
        result: Run = run_cli([str(games)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(BASE))
        expect(result.stderr).to_equal('')


with describe('keys'):

    @test(name='-k reads the meta NCA of a package with no ticket or tag')
    def explicit_keys(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(pfs0([('meta.cnmt.nca', nca_header(UPDATE, META))]))
        keys: Path = write_keys(h / 'my.keys')
        result: Run = run_cli([str(games), '-k', str(keys)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(BASE))

    @test(name='~/.switch/prod.keys is used by default')
    def default_keys(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Game.xcz').write_bytes(xci([('meta.cnmt.ncz', nca_header(BASE, META))]))
        (h / '.switch').mkdir()
        write_keys(h / '.switch' / 'prod.keys')
        result: Run = run_cli([str(games)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(BASE))

    @test(name='without keys, a package needing them is skipped with a hint')
    def no_keys(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(pfs0([('meta.cnmt.nca', nca_header(BASE, META))]))
        result: Run = run_cli([str(games)], h)

        expect(result.code).to_equal(1)
        expect(result.stdout).to_equal('')
        expect(result.stderr).to_contain('give -k prod.keys')

    @test(name='a missing -k file is an error, even if no package needs it')
    def missing_keys_file(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(pfs0([(ticket_name(BASE), b'')]))
        result: Run = run_cli([str(games), '-k', str(h / 'nope.keys')], h)

        expect(result.code).to_equal(1)
        expect(result.stderr).to_contain('no such keys file')

    @test(name='a keys file without header_key is an error once a package needs it')
    def keys_without_header_key(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(pfs0([('meta.cnmt.nca', nca_header(BASE, META))]))
        keys: Path = h / 'bad.keys'
        keys.write_text('titlekek_00 = 00\n')
        result: Run = run_cli([str(games), '-k', str(keys)], h)

        # its only package needs the key, so after the warn-once the package is
        # skipped, no title ID is resolved, and the run exits 1. The warning
        # line is printed exactly once and there is no traceback.
        expect(result.code).to_equal(1)
        expect(result.stderr.count('has no header_key')).to_equal(1)
        expect(result.stderr).not_.to_contain('Traceback')


with describe('skipping'):

    @test(name='AppleDouble, empty, and wrong-extension files are ignored silently')
    def ignored(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(pfs0([(ticket_name(BASE), b'')]))
        (games / '._Game.nsp').write_bytes(pfs0([(ticket_name(OTHER_BASE), b'')]))
        (games / f'Empty [{OTHER_BASE:016X}].nsp').write_bytes(b'')
        (games / f'Other [{OTHER_BASE:016X}].zip').write_bytes(pfs0([]))
        (games / 'sub.nsp').mkdir()
        result: Run = run_cli([str(games)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(BASE))
        expect(result.stderr).to_equal('')

    @test(name='a file that is not a package is skipped with a message')
    def not_a_package(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(pfs0([(ticket_name(BASE), b'')]))
        (games / f'Fake [{OTHER_BASE:016X}].nsp').write_bytes(b'not a package')
        result: Run = run_cli([str(games)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(BASE))
        expect(result.stderr).to_contain('skipping Fake')

    @test(name='a malformed package is skipped and the others still print')
    def malformed(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'A.nsp').write_bytes(pfs0([(ticket_name(BASE), b'')]))
        # B: a name with no NUL in the string table; C: 0xFFFF entries, none present
        entry: bytes = struct.pack('<QQI', 0, 0, 0).ljust(PFS0_ENTRY_SIZE, b'\0')
        (games / f'B [{OTHER_BASE:016X}].nsp').write_bytes(b'PFS0' + struct.pack('<III', 1, 5, 0) + entry + b'x.nca')
        (games / f'C [{OTHER_BASE:016X}].nsp').write_bytes(b'PFS0' + struct.pack('<III', 0xFFFF, 0, 0))
        result: Run = run_cli([str(games)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(BASE))
        expect(result.stderr).to_contain('skipping B')
        expect(result.stderr).to_contain('skipping C')
        expect(result.stderr).not_.to_contain('Traceback')


with describe('folder results'):

    @test.skip_if(not HAVE_REAL_NSPS, reason=NO_REAL_NSPS)
    # skip_if takes no name= and Tryke reads a docstring's literal first line
    # (blank in this project's style), so the function name is the label
    def more_than_one_game_prints_each_sorted_with_a_warning(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)

        # reversed names, so the sorted output can't just follow file order
        for name, tid in zip(['C', 'B', 'A'], REAL_TITLE_IDS, strict=True):
            (games / f'{name}.nsp').symlink_to(real_nsp(tid))

        result: Run = run_cli([str(games)], h)

        expect(result.code).to_equal(3)
        expect(result.stdout).to_equal(_ids(*REAL_TITLE_IDS))
        expect(result.stderr).to_contain('more than one game')

    @test(name='stdout holds only sorted, unique title IDs and no key value (seeded)')
    def stdout_only_ids(h: Annotated[Path, Depends(home)]) -> None:
        # Property 7 (preservation): across many mixed folders, every stdout
        # line is 16 uppercase hex digits, the lines are sorted and unique, and
        # no key value ever reaches stdout or stderr. A seeded loop stands in
        # for a property generator (no hypothesis dep).
        # Validates: Requirements 3.1, 3.10, 3.14
        rng = random.Random(0)
        keys: Path = write_keys(h / 'my.keys')
        id_line = re.compile(r'^[0-9A-F]{16}$')

        for n in range(20):
            folder: Path = h / f'folder{n}'
            folder.mkdir()

            # a seeded mix of ticket, tag, meta-NCA, malformed, and
            # non-package files, so stdout must still hold only clean IDs
            for kind in rng.choices(['ticket', 'tag', 'meta', 'malformed', 'junk'], k=rng.randint(1, 6)):
                tid: int = rng.choice([BASE, UPDATE, DLC_1, OTHER_BASE])
                name: str = f'{kind}_{rng.randrange(0x10000):04x}'

                if kind == 'ticket':
                    (folder / f'{name}.nsp').write_bytes(pfs0([(ticket_name(tid), b'')]))

                elif kind == 'tag':
                    (folder / f'{name} [{tid:016X}].nsp').write_bytes(pfs0([('a.nca', b'')]))

                elif kind == 'meta':
                    (folder / f'{name}.nsp').write_bytes(pfs0([('meta.cnmt.nca', nca_header(tid, META))]))

                elif kind == 'malformed':
                    (folder / f'{name} [{tid:016X}].nsp').write_bytes(b'PFS0' + struct.pack('<III', 0xFFFF, 0, 0))

                else:  # a file with a package extension that is not a package
                    (folder / f'{name} [{tid:016X}].nsp').write_bytes(b'not a package')

            result: Run = run_cli([str(folder), '-k', str(keys)], h)
            lines: list[str] = result.stdout.splitlines()

            for line in lines:
                expect(id_line.match(line)).not_.to_be_none()

            expect(lines).to_equal(sorted(set(lines)))
            expect(result.stdout).not_.to_contain(HEADER_KEY.hex())
            expect(result.stderr).not_.to_contain(HEADER_KEY.hex())
            expect(result.stderr).not_.to_contain('Traceback')

    @test(name='an empty folder is an error')
    def empty_folder(h: Annotated[Path, Depends(home)]) -> None:
        result: Run = run_cli([str(_games(h))], h)

        expect(result.code).to_equal(1)
        expect(result.stderr).to_contain('no package with a readable title ID')

    @test(name='a path that is not a folder is an error')
    def not_a_folder(h: Annotated[Path, Depends(home)]) -> None:
        result: Run = run_cli([str(h / 'nowhere')], h)

        expect(result.code).to_equal(1)
        expect(result.stderr).to_contain('not a folder')


@test(name='--version prints the package version')
def version(h: Annotated[Path, Depends(home)]) -> None:
    result: Run = run_cli(['--version'], h)

    expect(result.code).to_equal(0)
    expect(result.stdout).to_equal(f'nx-title-id {__version__}\n')


# ------------------------------------------------------------------------------
# REAL MULTI-TITLE CARTRIDGE (tests/ff.xci), gated on the fixture and keys

_FF_ID_LINE: re.Pattern[str] = re.compile(r'^[0-9A-F]{16}$')


with describe('real multi-title cartridge'):

    @test.skip_if(not (HAVE_FF_XCI and HAVE_REAL_KEYS), reason=f'{NO_FF_XCI} or no ~/.switch/prod.keys')
    def ff_xci_with_keys_exits_3(h: Annotated[Path, Depends(home)]) -> None:
        # G, real meta NCAs: ff.xci carries several base games. With real keys
        # and no tickets, the meta NCA step collects every base game, so more
        # than one ID prints and the run exits 3. No key value leaks.
        # Validates: Requirements 2.14, 3.14, 3.15
        games: Path = _games(h)
        (games / 'ff.xci').symlink_to(TESTS_DIR / 'ff.xci')

        result: Run = run_cli([str(games), '-k', str(REAL_KEYS)], h)
        lines: list[str] = result.stdout.splitlines()

        expect(result.code).to_equal(3)
        expect(all(_FF_ID_LINE.match(line) for line in lines)).to_be_truthy()
        expect(len(lines) > 1).to_be_truthy()
        expect(result.stderr).to_contain('more than one game')

        key_hex: str = read_header_key(REAL_KEYS).hex()
        expect(result.stdout).not_.to_contain(key_hex)
        expect(result.stderr).not_.to_contain(key_hex)

    @test.skip_if(not HAVE_FF_XCI, reason=NO_FF_XCI)
    def ff_xci_without_keys_is_the_detection_limit(h: Annotated[Path, Depends(home)]) -> None:
        # 2.15: without keys the empty HOME has no ~/.switch/prod.keys, so
        # detection relies on tickets alone. Depending on what tickets the
        # cartridge carries this resolves to zero IDs (exit 1 with the hint),
        # one (exit 0), or several (exit 3) - all acceptable, none a defect.
        # The invariant is that running without keys never crashes, every
        # stdout line is a clean ID, and no package reads prod.keys.
        # Validates: Requirements 2.15
        games: Path = _games(h)
        (games / 'ff.xci').symlink_to(TESTS_DIR / 'ff.xci')

        result: Run = run_cli([str(games)], h)
        lines: list[str] = result.stdout.splitlines()

        expect(result.code in (0, 1, 3)).to_be_truthy()
        expect(all(_FF_ID_LINE.match(line) for line in lines)).to_be_truthy()

        if result.code == 1:
            expect(result.stderr).to_contain('give -k prod.keys')
            expect(lines).to_equal([])

        elif result.code == 0:
            expect(len(lines)).to_equal(1)

        else:  # several base games resolved from tickets alone
            expect(len(lines) > 1).to_be_truthy()

        expect(result.stderr).not_.to_contain('Traceback')


# ------------------------------------------------------------------------------
# BUG CONDITION EXPLORATION TESTS (A, D, E, G)
#
# Each test encodes the fixed behavior for one audited defect. They were written
# first, against the unfixed code, where they failed - that failure is what
# confirmed each defect was real. They now pass and guard against a regression,
# so the FIXED / UNFIXED notes below are kept as a record of what each one caught.

_IS_ROOT: bool = hasattr(os, 'geteuid') and os.geteuid() == 0
_ROOT_SKIP: str = 'running as root, which ignores file modes'

with describe('bug condition A (unreadable files)'):

    @test.skip_if(_IS_ROOT, reason=_ROOT_SKIP)
    def unreadable_package_is_skipped(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'A.nsp').write_bytes(pfs0([(ticket_name(BASE), b'')]))
        b: Path = games / 'B.nsp'
        b.write_bytes(pfs0([(ticket_name(OTHER_BASE), b'')]))
        b.chmod(0o000)

        result: Run = run_cli([str(games)], h)

        # FIXED: B is skipped with a stderr message, A still prints, exit 0,
        # no traceback. UNFIXED: an uncaught PermissionError traceback, no
        # stdout, nonzero exit.
        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(BASE))
        expect(result.stderr).to_contain('skipping B.nsp: Permission denied')
        expect(result.stderr).not_.to_contain('Traceback')

    @test.skip_if(_IS_ROOT, reason=_ROOT_SKIP)
    def unreadable_keys_is_one_line_fatal(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(pfs0([('meta.cnmt.nca', nca_header(BASE, META))]))
        keys: Path = h / 'my.keys'
        keys.write_text('header_key = ' + '00' * 32 + '\n')
        keys.chmod(0o000)

        result: Run = run_cli([str(games), '-k', str(keys)], h)

        # FIXED: a one-line fatal error naming the keys path, exit 1, no
        # traceback. UNFIXED: an uncaught PermissionError traceback.
        expect(result.code).to_equal(1)
        expect(result.stderr).to_contain('cannot read keys file')
        expect(result.stderr).not_.to_contain('Traceback')


with describe('bug condition D (missing header_key)'):

    @test(name='a missing header_key keeps the other packages printing')
    def missing_header_key_keeps_others(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'A.nsp').write_bytes(pfs0([('meta.cnmt.nca', nca_header(BASE, META))]))
        (games / 'B.nsp').write_bytes(pfs0([(ticket_name(OTHER_BASE), b'')]))
        keys: Path = h / 'bad.keys'
        keys.write_text('titlekek_00 = 00\n')

        result: Run = run_cli([str(games), '-k', str(keys)], h)

        # FIXED: warn once about the missing header_key, skip A with a
        # header_key message, still print B's ticket-resolved ID, exit 0.
        # UNFIXED: the first package needing the fallback calls sys.exit(),
        # discarding B's ID.
        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(OTHER_BASE))
        expect(result.stderr.count('has no header_key')).to_equal(1)
        expect(result.stderr).to_contain('skipping A.nsp')
        expect(result.stderr).to_contain('header_key')
        expect(result.stderr).not_.to_contain('Traceback')


with describe('bug condition E (several games)'):

    @test(name='several single-game packages print every ID and exit 3')
    def several_games_exit_3(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'A.nsp').write_bytes(pfs0([(ticket_name(BASE), b'')]))
        (games / 'B.nsp').write_bytes(pfs0([(ticket_name(OTHER_BASE), b'')]))

        result: Run = run_cli([str(games)], h)

        # FIXED: both IDs on stdout (sorted), the multi-game warning on stderr,
        # exit 3. UNFIXED: the warning prints but the run falls through to the
        # implicit exit 0.
        expect(result.code).to_equal(3)
        expect(result.stdout).to_equal(_ids(BASE, OTHER_BASE))
        expect(result.stderr).to_contain('more than one game')


with describe('bug condition G (multi-title package)'):

    @test(name='one package with two base games prints both and exits 3')
    def multi_title_package_exit_3(h: Annotated[Path, Depends(home)]) -> None:
        games: Path = _games(h)
        (games / 'Collection.xci').write_bytes(xci([(ticket_name(BASE), b''), (ticket_name(OTHER_BASE), b'')]))

        result: Run = run_cli([str(games)], h)

        # FIXED: title_ids_of() collects both tickets, each folds to a distinct
        # base game, so both IDs print (sorted), the warning shows, exit 3.
        # UNFIXED: title_of() returns the first ticket only, so one ID, exit 0.
        expect(result.code).to_equal(3)
        expect(result.stdout).to_equal(_ids(BASE, OTHER_BASE))
        expect(result.stderr).to_contain('more than one game')
