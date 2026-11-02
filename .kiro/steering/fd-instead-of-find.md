---
# @config-manager:start fd-instead-of-find-frontmatter
inclusion: auto
name: fd-instead-of-find
description: Use fd instead of find in shell scripts and Taskfiles for cross-platform parity. Use when writing or editing shell scripts, Taskfile.yml tasks, or any command that searches the filesystem.
# @config-manager:end fd-instead-of-find-frontmatter
---

<!-- @config-manager:start fd-instead-of-find -->
# Use fd Instead of find

macOS ships BSD `find`, while Linux and Windows WSL ship GNU `find`. Their flags and behavior differ (for example `-regex` dialects, `-maxdepth` placement, `-printf`, and `-iname` support). Use [`fd`](https://github.com/sharkdp/fd) instead so commands behave the same on every platform.

## Rules

* Use `fd`, never `find`, in every shell script and `Taskfile` that we write.
* When editing an existing script or `Taskfile` that uses `find`, convert those calls to `fd` only if you're already changing that code, or if asked.
* Run `fd --help` for the full option reference.

## Baseline flags

`fd`'s defaults differ from `find`'s. Out of the box, `fd` skips hidden and `.gitignore`d files, uses smart-case matching, treats the pattern as a regex, and doesn't follow symlinks. To get predictable, `find`-like results, always start from this baseline and pick exactly one matching mode:

```bash
fd --unrestricted --case-sensitive --follow --glob          '<glob>'
fd --unrestricted --case-sensitive --follow --regex         '<regex>'
fd --unrestricted --case-sensitive --follow --fixed-strings '<substring>'
fd --unrestricted --case-sensitive --follow --exact         '<exact-name>'
```

| Flag               | Why                                                                                 |
|--------------------|-------------------------------------------------------------------------------------|
| `--case-sensitive` | Disables smart case, so matching never depends on whether the pattern has capitals. |
| `--exact`          | Literal match against the **entire** name. Regex characters aren't special.         |
| `--fixed-strings`  | Literal **substring** match. Regex characters aren't special.                       |
| `--follow`         | Descends into symlinked directories (like `find -L`).                               |
| `--glob`           | Shell-style glob, such as `'*.sh'`. Prefer this for simple name matches.            |
| `--regex`          | Regular expression (the `fd` default, stated explicitly for clarity).               |
| `--unrestricted`   | Includes hidden and ignored files (alias for `--no-ignore --hidden`), like `find`.  |

Use the long flag names in scripts and `Taskfile`s so they're self-documenting.

## Matching the full path

By default the pattern matches only the file or directory name. Add `--full-path` to match against the full **absolute** path instead, so anchor patterns accordingly (for example, start globs with `**/`):

```bash
fd --unrestricted --case-sensitive --full-path --glob '**/tests/*.py'
```

## Filtering

* Depth: `--min-depth <n>`, `--max-depth <n>`, or `--exact-depth <n>`.
* Type: `--type file|dir|symlink|executable|empty` (short forms `f|d|l|x|e`). Repeat `--type` to allow several types.
* `--type empty` matches both empty files and empty directories unless you also pass `--type file` or `--type dir`.
* `--type executable` implies `--type file`.
* Extension: `--extension <ext>` (repeatable). Pair with `--type file`, because it otherwise also matches directories.
* Exclusions: `--exclude '<glob>'` (repeatable).

## Search location

* Pass the search root as the positional path after the pattern: `fd [options] '<pattern>' <path>`.
* Use `--base-directory <path>` to run `fd` as if from that directory. Results are shown relative to it, and positional paths are resolved relative to it too.
* Pass `.` as the pattern to match everything: `fd --unrestricted --type file . src`.

## Acting on results

* Prefer `--exec <cmd>` (once per result, in parallel) or `--exec-batch <cmd>` (once with all results) over piping into `xargs`. Place them last on the command line.
* If you must pipe, use `--print0` with `xargs -0` so paths with spaces or newlines are safe.
* Results are prefixed with `./` when using `--exec`, `--exec-batch`, or `--print0`. Use `--strip-cwd-prefix` if you need bare relative paths.

## Gotchas

* Output order isn't deterministic, and `--exec`/`--exec-batch` run in an unspecified order. Pipe through `sort` when order matters, or use `--threads=1` for sequential `--exec`.
* `fd` exits `0` even when nothing matches. Use `--quiet` (exit `1` when there are no matches) to test for existence, for example `if fd --unrestricted --case-sensitive --glob --quiet '*.lock'; then ...`.
* Quote every pattern so the shell doesn't expand globs before `fd` sees them.
* If a pattern starts with `-`, pass `--` first: `fd -- '-foo'`.

## Common translations

| `find`                                        | `fd`                                                                                |
|-----------------------------------------------|-------------------------------------------------------------------------------------|
| `find . -name '*.sh'`                         | `fd --unrestricted --case-sensitive --follow --glob '*.sh'`                         |
| `find . -iname '*.md'`                        | `fd --unrestricted --ignore-case --follow --glob '*.md'`                            |
| `find src -type f -name '*.py'`               | `fd --unrestricted --case-sensitive --follow --type file --glob '*.py' src`         |
| `find . -maxdepth 1 -type d`                  | `fd --unrestricted --case-sensitive --max-depth 1 --type dir`                       |
| `find . -type f -empty`                       | `fd --unrestricted --type empty --type file`                                        |
| `find . -path '*/tests/*.py'`                 | `fd --unrestricted --case-sensitive --full-path --glob '**/tests/*.py'`             |
| `find . -name '*.sh' -exec shellcheck {} +`   | `fd --unrestricted --case-sensitive --follow --glob '*.sh' --exec-batch shellcheck` |
| `find . -name '*.tmp' -print0 \| xargs -0 rm` | `fd --unrestricted --case-sensitive --glob '*.tmp' --exec-batch rm`                 |

## Taskfile usage

Use the same flags inside `cmds`. Quote the whole command when it contains characters that are special in YAML, such as `*`, `{`, or `:`:

```yaml
tasks:
  lint:shell:
    cmds:
      - "fd --unrestricted --case-sensitive --follow --type file --glob '*.sh' --exclude .git --exec-batch shellcheck"
```
<!-- @config-manager:end fd-instead-of-find -->
