# CHANGELOG

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com), adheres to [Semantic Versioning](https://semver.org), and uses [Conventional Commit](https://www.conventionalcommits.org) syntax.

## Unreleased

### Changed

* A folder that resolves to several games (including a single multi-title package) now exits with code `3` instead of `0`.
* A keys file without a `header_key` is now a warning, not a fatal error.

### Added

* A package holding several base games contributes every one of them to the result.

### Fixed

* Unreadable packages are skipped with a stderr message instead of crashing.
* An unreadable keys file produces a one-line error instead of a traceback.
* Oversized PFS0/HFS0 headers are rejected before reading.
