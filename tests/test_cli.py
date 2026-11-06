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

from nx_title_id import __version__, application_of, read_header_key, read_key_area_keys
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
    program_package,
    real_nsp,
    real_nsp_build_id,
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


with describe('--build-id'):

    @test.cases(
        test.case('-b', flag='-b'),
        test.case('--build-id', flag='--build-id'),
    )
    def prints_title_id_and_build_id(flag: str, h: Annotated[Path, Depends(home)]) -> None:
        # The synthetic package has only a meta NCA and a program NCA, so the
        # title ID comes from the meta NCA fallback via the -k keys file. The
        # ModuleId is a full 0x20 bytes with hex letters, so uppercase matters;
        # only its first 8 bytes are printed as the Build ID.
        # Validates: Requirements 1.1, 1.3, 1.4, 6.2
        module_id: bytes = bytes(range(0xA0, 0xC0))
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(program_package(BASE, module_id))
        keys: Path = write_keys(h / 'my.keys')
        result: Run = run_cli([str(games), flag, '-k', str(keys)], h)

        expected: str = module_id[:8].hex().upper()
        fields: list[str] = result.stdout.rstrip('\n').split(' ')

        # argparse rejects with exit 2, so anything else means it was accepted.
        expect(result.code).not_.to_equal(2)
        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(f'{BASE:016X} {expected}\n')
        expect(len(fields)).to_equal(2)
        expect(len(fields[1])).to_equal(16)
        expect(fields[1]).to_equal(fields[1].upper())
        expect(result.stdout).not_.to_contain(HEADER_KEY.hex())
        expect(result.stderr).not_.to_contain(HEADER_KEY.hex())

    @test(name='bytes after the first 8 of the ModuleId do not change the printed Build ID')
    def build_id_ignores_bytes_after_the_first_8(h: Annotated[Path, Depends(home)]) -> None:
        # The Build ID tools show is the ModuleId's first 8 bytes, so two
        # ModuleIds that differ only afterwards must print the same value.
        # Validates: Requirements 1.3, 2.2
        head: bytes = bytes(range(0xB0, 0xB8))
        keys: Path = write_keys(h / 'my.keys')
        printed: list[str] = []

        for i, tail in enumerate([bytes(24), bytes(range(1, 25))]):
            games: Path = h / f'games{i}'
            games.mkdir()
            (games / 'Game.nsp').write_bytes(program_package(BASE, head + tail))
            printed.append(run_cli([str(games), '-b', '-k', str(keys)], h).stdout)

        expect(printed[0]).to_equal(f'{BASE:016X} {head.hex().upper()}\n')
        expect(printed[1]).to_equal(printed[0])


with describe('--build-id keys and skips'):

    @test(name='-b with no keys source is a one-line setup error before any output')
    def build_id_without_keys(h: Annotated[Path, Depends(home)]) -> None:
        # A ticket-resolved package would print fine without -b, so an empty
        # stdout proves the precondition fires before any package work.
        # Validates: Requirements 2.3, 4.1, 6.3
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(pfs0([(ticket_name(BASE), b'')]))
        result: Run = run_cli([str(games), '-b'], h)

        expect(result.code).to_equal(1)
        expect(result.stdout).to_equal('')
        expect(result.stderr).to_contain('needs prod.keys')
        expect(len(result.stderr.strip().splitlines())).to_equal(1)
        expect(result.stderr).not_.to_contain('Traceback')

    @test(name='-b with a keys file lacking header_key exits 1 up front')
    def build_id_keys_without_header_key(h: Annotated[Path, Depends(home)]) -> None:
        # Validates: Requirements 4.1
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(pfs0([(ticket_name(BASE), b'')]))
        keys: Path = h / 'bad.keys'
        keys.write_text('titlekek_00 = 00\n')
        result: Run = run_cli([str(games), '-b', '-k', str(keys)], h)

        expect(result.code).to_equal(1)
        expect(result.stdout).to_equal('')
        expect(result.stderr).to_contain('header_key')
        expect(result.stderr).not_.to_contain('Traceback')

    @test(name='a ticket-only game is skipped while a program-NCA game still prints')
    def build_id_skips_ticket_only_game(h: Annotated[Path, Depends(home)]) -> None:
        # Validates: Requirements 4.2, 6.3
        module_id: bytes = bytes(range(0x10, 0x30))
        games: Path = _games(h)
        (games / 'A.nsp').write_bytes(pfs0([(ticket_name(OTHER_BASE), b'')]))
        (games / 'B.nsp').write_bytes(program_package(BASE, module_id))
        keys: Path = write_keys(h / 'my.keys')
        result: Run = run_cli([str(games), '-b', '-k', str(keys)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(f'{BASE:016X} {module_id[:8].hex().upper()}\n')
        expect(result.stderr).to_contain(f'skipping game {OTHER_BASE:016X}')
        expect(result.stderr).not_.to_contain('Traceback')
        expect(result.stdout).not_.to_contain(HEADER_KEY.hex())
        expect(result.stderr).not_.to_contain(HEADER_KEY.hex())

    @test(name='a package with no program NCA is skipped while others print')
    def build_id_skips_package_without_program_nca(h: Annotated[Path, Depends(home)]) -> None:
        # C resolves its title ID through the meta NCA fallback but carries no
        # program NCA, so it has no main NSO to read.
        # Validates: Requirements 4.3
        module_id: bytes = bytes(range(0x40, 0x60))
        games: Path = _games(h)
        (games / 'B.nsp').write_bytes(program_package(BASE, module_id))
        (games / 'C.nsp').write_bytes(pfs0([('meta.cnmt.nca', nca_header(OTHER_BASE, META))]))
        keys: Path = write_keys(h / 'my.keys')
        result: Run = run_cli([str(games), '-b', '-k', str(keys)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(f'{BASE:016X} {module_id[:8].hex().upper()}\n')
        expect(result.stderr).to_contain(f'skipping game {OTHER_BASE:016X}')
        expect(result.stderr).not_.to_contain('Traceback')
        expect(result.stdout).not_.to_contain(HEADER_KEY.hex())
        expect(result.stderr).not_.to_contain(HEADER_KEY.hex())


with describe('--build-id exit codes'):

    @test(name='one game under -b exits 0 and prints one line')
    def build_id_exit_0_one_game(h: Annotated[Path, Depends(home)]) -> None:
        # Validates: Requirements 4.4
        module_id: bytes = bytes(range(0x10, 0x30))
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(program_package(BASE, module_id))
        keys: Path = write_keys(h / 'my.keys')
        result: Run = run_cli([str(games), '-b', '-k', str(keys)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(f'{BASE:016X} {module_id[:8].hex().upper()}\n')
        expect(result.stderr).not_.to_contain('more than one game')
        expect(result.stdout).not_.to_contain(HEADER_KEY.hex())
        expect(result.stderr).not_.to_contain(HEADER_KEY.hex())

    @test(name='several games under -b print each sorted by Title ID and exit 3')
    def build_id_exit_3_several_games(h: Annotated[Path, Depends(home)]) -> None:
        # Validates: Requirements 4.4
        module_a: bytes = bytes(range(0x10, 0x30))
        module_b: bytes = bytes(range(0x50, 0x70))
        games: Path = _games(h)
        # written in reverse order so sorted output can't follow file order
        (games / 'A.nsp').write_bytes(program_package(OTHER_BASE, module_b))
        (games / 'B.nsp').write_bytes(program_package(BASE, module_a))
        keys: Path = write_keys(h / 'my.keys')
        result: Run = run_cli([str(games), '-b', '-k', str(keys)], h)

        expect(result.code).to_equal(3)
        expect(result.stdout).to_equal(
            ''.join(
                f'{tid:016X} {mid[:8].hex().upper()}\n'
                for tid, mid in sorted([(BASE, module_a), (OTHER_BASE, module_b)])
            )
        )
        expect(result.stderr).to_contain('more than one game')
        expect(result.stderr).not_.to_contain('Traceback')
        expect(result.stdout).not_.to_contain(HEADER_KEY.hex())
        expect(result.stderr).not_.to_contain(HEADER_KEY.hex())

    @test(name='all games skipped under -b exits 1 with no output')
    def build_id_exit_1_all_skipped(h: Annotated[Path, Depends(home)]) -> None:
        # Validates: Requirements 4.5
        games: Path = _games(h)
        (games / 'A.nsp').write_bytes(pfs0([(ticket_name(BASE), b'')]))
        (games / 'B.nsp').write_bytes(pfs0([('meta.cnmt.nca', nca_header(OTHER_BASE, META))]))
        keys: Path = write_keys(h / 'my.keys')
        result: Run = run_cli([str(games), '-b', '-k', str(keys)], h)

        expect(result.code).to_equal(1)
        expect(result.stdout).to_equal('')
        expect(result.stderr).to_contain('no game with a readable Build ID')
        expect(result.stderr).not_.to_contain('Traceback')
        expect(result.stderr).not_.to_contain(HEADER_KEY.hex())

    @test(name='skips do not turn two printable games into anything but exit 3')
    def build_id_exit_3_ignores_skipped(h: Annotated[Path, Depends(home)]) -> None:
        # Validates: Requirements 4.4
        module_a: bytes = bytes(range(0x10, 0x30))
        module_b: bytes = bytes(range(0x50, 0x70))
        games: Path = _games(h)
        (games / 'A.nsp').write_bytes(program_package(BASE, module_a))
        (games / 'B.nsp').write_bytes(program_package(OTHER_BASE, module_b))
        # a third, distinct game (DLC_1 would fold into BASE) with only a
        # ticket, so it has no program NCA and is skipped
        third: int = OTHER_BASE + 0x10000
        (games / 'C.nsp').write_bytes(pfs0([(ticket_name(third), b'')]))
        keys: Path = write_keys(h / 'my.keys')
        result: Run = run_cli([str(games), '-b', '-k', str(keys)], h)

        expect(result.code).to_equal(3)
        expect(len(result.stdout.splitlines())).to_equal(2)
        expect(result.stderr).to_contain('more than one game')
        expect(result.stderr).to_contain(f'skipping game {third:016X}')
        expect(result.stderr).not_.to_contain('Traceback')

    @test(name='an unknown flag is an argparse error with exit 2')
    def bad_flag_exits_2(h: Annotated[Path, Depends(home)]) -> None:
        # Validates: Requirements 4.4
        result: Run = run_cli(['--no-such-flag'], h)

        expect(result.code).to_equal(2)
        expect(result.stdout).to_equal('')
        expect(result.stderr).to_contain('usage')

    @test(name='a missing folder argument is an argparse error with exit 2')
    def missing_folder_exits_2(h: Annotated[Path, Depends(home)]) -> None:
        result: Run = run_cli([], h)

        expect(result.code).to_equal(2)
        expect(result.stdout).to_equal('')
        expect(result.stderr).to_contain('usage')


with describe('--build-id titlekey package'):

    @test(name='a synthetic titlekey package prints its Build ID; no ticket is a skip')
    def titlekey_cli(h: Annotated[Path, Depends(home)]) -> None:
        title_key: bytes = bytes(range(0x40, 0x50))
        titlekek: bytes = bytes(range(0x90, 0xA0))
        module_id: bytes = bytes(range(1, 0x21))
        rid: bytes = rights_id_of(BASE)
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(titlekey_package(BASE, module_id, title_key, ticket(rid, title_key, titlekek)))
        keys: Path = write_titlekek_keys(h / 'my.keys', 0, titlekek)
        result: Run = run_cli([str(games), '-b', '-k', str(keys)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(f'{BASE:016X} {module_id[:8].hex().upper()}\n')

        for text in (result.stdout, result.stderr):
            expect(text).not_.to_contain(title_key.hex())
            expect(text).not_.to_contain(titlekek.hex())

        # the same package without the titlekek in the keys file is a skip
        plain: Path = write_keys(h / 'plain.keys')
        skipped: Run = run_cli([str(games), '-b', '-k', str(plain)], h)

        expect(skipped.code).to_equal(1)
        expect(skipped.stdout).to_equal('')
        expect(skipped.stderr).to_contain(f'skipping game {BASE:016X}')
        expect(skipped.stderr).not_.to_contain('Traceback')


with describe('--build-id absent (default output)'):

    @test(name='a default run prints only Title IDs, one per line, and no Build ID')
    def default_run_emits_no_build_id(h: Annotated[Path, Depends(home)]) -> None:
        # Validates: Requirements 1.2, 5.1, 5.3
        module_id: bytes = bytes(range(0xA0, 0xC0))
        games: Path = _games(h)
        (games / 'Game.nsp').write_bytes(program_package(BASE, module_id))
        keys: Path = write_keys(h / 'my.keys')
        result: Run = run_cli([str(games), '-k', str(keys)], h)

        expect(result.code).to_equal(0)
        expect(result.stdout).to_equal(_ids(BASE))
        expect(result.stdout).not_.to_contain(' ')
        expect(result.stdout).not_.to_contain(module_id[:8].hex().upper())
        expect(result.stderr).to_equal('')

    @test(name='default output is unchanged without -b and does no program NCA work (seeded)')
    def default_output_unchanged_without_flag(h: Annotated[Path, Depends(home)]) -> None:
        # Feature: build-id, Property 5: Default output is unchanged when the flag is absent
        # Validates: Requirements 1.2, 5.1, 5.2, 5.3, 6.4
        #
        # The expected output is computed from the generated contents alone:
        # sorted unique application_of() IDs, exit 0/3/1 for one/several/none.
        # A sentinel ModuleId in every program package must never surface, and
        # a corrupt program NCA must not produce a skip message, because a
        # default run never opens program NCAs.
        rng = random.Random(0)
        keys: Path = write_keys(h / 'my.keys')
        sentinel: bytes = bytes([0xDE, 0xAD, 0xBE, 0xEF] * 8)
        titles: list[int] = [BASE, UPDATE, DLC_1, OTHER_BASE, OTHER_BASE + 0x800, OTHER_BASE + 0x10000]
        id_line = re.compile(r'^[0-9A-F]{16}$')

        for n in range(100):
            folder: Path = h / f'folder{n}'
            folder.mkdir()
            expected: set[int] = set()

            for i, kind in enumerate(rng.choices(['ticket', 'tag', 'meta', 'program', 'corrupt'], k=rng.randint(0, 4))):
                tid: int = rng.choice(titles)
                expected.add(application_of(tid))

                if kind == 'ticket':
                    (folder / f'p{i}.nsp').write_bytes(pfs0([(ticket_name(tid), b'')]))

                elif kind == 'tag':
                    (folder / f'p{i} [{tid:016X}].nsp').write_bytes(pfs0([('a.nca', b'')]))

                elif kind == 'meta':
                    (folder / f'p{i}.nsp').write_bytes(pfs0([('meta.cnmt.nca', nca_header(tid, META))]))

                elif kind == 'program':
                    (folder / f'p{i}.nsp').write_bytes(program_package(tid, sentinel))

                else:  # an unreadable program NCA beside a valid meta NCA
                    junk: bytes = bytes(rng.randrange(256) for _ in range(0xC00 + 0x200))
                    (folder / f'p{i}.nsp').write_bytes(
                        pfs0([('program.nca', junk), ('meta.cnmt.nca', nca_header(tid, META))])
                    )

            result: Run = run_cli([str(folder), '-k', str(keys)], h)
            want: list[int] = sorted(expected)
            want_code: int = 1 if not want else (0 if len(want) == 1 else 3)

            expect(result.code).to_equal(want_code)
            expect(result.stdout).to_equal(_ids(*want))

            for line in result.stdout.splitlines():
                expect(id_line.match(line)).not_.to_be_none()

            for text in (result.stdout, result.stderr):
                expect(text).not_.to_contain(sentinel.hex())
                expect(text).not_.to_contain(sentinel.hex().upper())
                expect(text).not_.to_contain(HEADER_KEY.hex())
                expect(text).not_.to_contain('Traceback')

            if want:
                expect(result.stderr).not_.to_contain('Build ID')
                expect(result.stderr).not_.to_contain('skipping')


# ------------------------------------------------------------------------------
# --BUILD-ID REAL FIXTURES, gated on the fixtures and real keys

# Every real fixture's program NCA carries a non-zero RightsId, so its exefs is
# keyed from the NSP's ticket titlekey (titlekek_XX), not the key area.
_NO_REAL_BUILD_IDS: str = f'{NO_REAL_NSPS} or no ~/.switch/prod.keys'
_REAL_ONE_LINE: re.Pattern[str] = re.compile(r'^([0-9A-F]{16}) ([0-9A-F]{16})\n$')


def _secret_hexes() -> list[str]:
    """
    Every real key value as hex in both cases, so a test can assert none of them
    leaks into the output.
    """
    keys: list[bytes] = [read_header_key(REAL_KEYS), *read_key_area_keys(REAL_KEYS).values()]

    return [form for key in keys for form in (key.hex(), key.hex().upper())]


with describe('--build-id real fixtures'):

    @test.skip_if(not (HAVE_REAL_NSPS and HAVE_REAL_KEYS), reason=_NO_REAL_BUILD_IDS)
    @test.cases(
        test.case('01004B800AF5A000', tid=0x01004B800AF5A000),
        test.case('01009C100390E000', tid=0x01009C100390E000),
        test.case('0100D9B00B666000', tid=0x0100D9B00B666000),
    )
    def real_build_id_matches_filename(tid: int, h: Annotated[Path, Depends(home)]) -> None:
        # Validates: Requirements 6.5, 6.6
        # The filename's 16 hex digits are the first 8 bytes of the 32-byte
        # ModuleId (what nxdumptool/Atmosphere call the Build ID), which is
        # exactly what the tool prints.
        games: Path = _games(h)
        (games / 'Game.nsp').symlink_to(real_nsp(tid))

        result: Run = run_cli([str(games), '-b', '-k', str(REAL_KEYS)], h)
        m: re.Match[str] | None = _REAL_ONE_LINE.match(result.stdout)
        want: str | None = real_nsp_build_id(tid)

        expect(result.code).to_equal(0)
        expect(m is not None and want is not None).to_be_truthy()

        if m is not None and want is not None:
            expect(m.group(1)).to_equal(f'{tid:016X}')
            expect(m.group(2)).to_equal(want)

        for secret in _secret_hexes():
            expect(result.stdout).not_.to_contain(secret)
            expect(result.stderr).not_.to_contain(secret)

    @test.skip_if(not (HAVE_REAL_NSPS and HAVE_REAL_KEYS), reason=_NO_REAL_BUILD_IDS)
    # skip_if takes no name= and Tryke reads a docstring's literal first line
    # (blank in this project's style), so the function name is the label
    def real_build_ids_for_every_fixture_sorted(h: Annotated[Path, Depends(home)]) -> None:
        # Validates: Requirements 4.4, 6.5
        games: Path = _games(h)

        # reversed names, so the sorted output can't just follow file order
        for name, tid in zip(['C', 'B', 'A'], REAL_TITLE_IDS, strict=True):
            (games / f'{name}.nsp').symlink_to(real_nsp(tid))

        result: Run = run_cli([str(games), '-b', '-k', str(REAL_KEYS)], h)
        lines: list[str] = result.stdout.splitlines()

        expect(result.code).to_equal(3)
        expect(len(lines)).to_equal(len(REAL_TITLE_IDS))

        # sorted by Title ID; each Build ID equals the filename's digits
        for line, tid in zip(lines, sorted(REAL_TITLE_IDS), strict=True):
            title, _, build = line.partition(' ')

            expect(title).to_equal(f'{tid:016X}')
            expect(build).to_equal(real_nsp_build_id(tid))
        expect(result.stderr).to_contain('more than one game')


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
