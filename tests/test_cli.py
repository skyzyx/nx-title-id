"""
The nx-title-id command end to end, run as `python -m nx_title_id` in a
subprocess with its own HOME (Tryke workers use stdout to talk to the runner).
"""

import struct
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from tryke import Depends, describe, expect, fixture, test

from nx_title_id import __version__
from tests.builders import (
    BASE,
    DLC_1,
    HAVE_REAL_NSPS,
    META,
    NO_REAL_NSPS,
    OTHER_BASE,
    PFS0_ENTRY_SIZE,
    REAL_TITLE_IDS,
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

        expect(result.code).to_equal(1)
        expect(result.stderr).to_contain('has no header_key')


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

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(*REAL_TITLE_IDS))
        expect(result.stderr).to_contain('more than one game')

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
