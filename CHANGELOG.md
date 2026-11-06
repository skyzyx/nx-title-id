# CHANGELOG

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com), adheres to [Semantic Versioning](https://semver.org), and uses [Conventional Commit](https://www.conventionalcommits.org) syntax.

## Unreleased

### Added

* `-b`/`--build-id` prints `<TitleID> <BuildID>` per base game, where the Build ID is the first 8 bytes of the main NSO's ModuleId as 16 uppercase hex digits. It is opt-in, needs `prod.keys` (`header_key` up front; `key_area_key_application_XX` and/or `titlekek_XX` per game), reads the newest update of the base game (never DLC), and skips unresolvable games with a one-line message. Output without the flag is unchanged.

### <!-- 1 -->:bug: Bug fixes

* [`a8d37d3`](https://github.com/skyzyx/nx-title-id/commit/a8d37d316551acadfd796d06aa82ad765ba3128c): Correct inverted change detection logic in GitHub Actions. ([@skyzyx](https://github.com/skyzyx))

<p>Generated on 2026-11-04.</p>
