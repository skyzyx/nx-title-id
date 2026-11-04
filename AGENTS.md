# AGENTS.md

Guidance for AI agents working in the `nx-title-id` repository. Read this before making changes. It explains what the project does, how it is built and tested, the house conventions that gate every commit, and the Kiro automation (steering, hooks, agents, specs) that shapes how work happens here.

## What this project is

`nx-title-id` is a single-purpose command-line tool: given a folder of Nintendo Switch game packages (`.nsp`, `.nsz`, `.xci`, `.xcz`), it prints the base-game title ID for each distinct game it finds. A game's base, updates, and DLC are separate packages that users keep together, so the one useful fact is the base game they all belong to.

The whole implementation is one module, `src/nx_title_id/__init__.py` (under 500 lines). `src/nx_title_id/__main__.py` is a three-line `python -m` shim. Everything else in the repo is tests, documentation, and tooling configuration.

Resolution tries the cheapest, most trustworthy source first: a ticket name inside the package, then a `[title ID]` tag in the file name, then (only with `prod.keys`) the decrypted meta NCA headers. Each source yields a set, so a multi-title cartridge contributes every game it carries; `application_of()` then folds updates and DLC back to their base game.

## Technology stack

| Concern          | Choice                                  | Notes                                                              |
|------------------|-----------------------------------------|--------------------------------------------------------------------|
| Language         | Python `>=3.14`                         | Declared in `pyproject.toml` `requires-python`                     |
| Runtime dep      | `pycryptodome>=4.2,<5`                  | AES only, for the meta NCA fallback; imported lazily inside a func |
| Dev deps         | `cryptography==50.0.2`, `tryke==0.0.30` | Reference AES-XTS for tests; the test runner                       |
| Package manager  | `uv`                                    | Lockfile is `uv.lock`; run tools via `uv run`/`uvx`                |
| Build backend    | `hatchling>=1.27,<2`                    | Version read dynamically from `__init__.py` `__version__`          |
| Test runner      | Tryke                                   | Jest-style API; `uv run tryke test`                                |
| Formatter/linter | Ruff                                    | Replaces Black, isort, flake8; never run those                     |
| Type checker     | Pyrefly                                 | Not mypy or pyright; target is zero diagnostics                    |
| Task runner      | Taskfile (go-task)                      | `Taskfile.dist.yml`; `make` is a shim to `task`                    |
| Hook manager     | hk (with pkl config)                    | `hk.pkl`; installed via `task install:hooks`                       |

## Commands

Use these rather than inventing your own invocations.

| Goal                     | Command                             |
|--------------------------|-------------------------------------|
| Run the tool locally     | `uv run nx-title-id <folder>`       |
| Run the tests            | `uv run tryke test`                 |
| Type-check a file        | `uvx pyrefly check <path>`          |
| Lint Python              | `uv run ruff check`                 |
| Auto-fix lint            | `uv run ruff check --fix`           |
| Format Python            | `uv run ruff format`                |
| Check formatting only    | `uv run ruff format --check`        |
| Lint changed files (all) | `task lint`                         |
| Lint everything deeply   | `task lint:deep:full`               |
| Lint Markdown            | `rumdl check <paths>` / `rumdl fmt` |
| Build sdist and wheel    | `task build:py` (or `uv build`)     |

Notes:

* Never run bare `tryke` or `uv run tryke`: without the `test` subcommand it starts watch mode and blocks. Always `uv run tryke test`.

* On this machine Pyrefly is reached through `uvx pyrefly`; `pyrefly` and `uv run pyrefly` may not be installed.

* `task lint` delegates to `hk`, which runs the full battery (Ruff, Pyrefly, Markdown, TOML, YAML, shell, security scanners) defined in `hk.pkl`.

## Code conventions

These are enforced by hooks and are not optional. The authoritative, detailed version lives in `.kiro/steering/python-code-conventions.md`; the essentials:

* Zero Pyrefly diagnostics. Fix the root cause; suppression (`# pyrefly: ignore[kind]` with justification) is a last resort.

* Specific type annotations on every parameter, return, and notable local. No bare `dict`; avoid `Any` (prefer a typed local over reassigning a parameter; use `Sequence` for covariant read-only params).

* Docstrings put both `"""` on their own lines.

* Blank line before `return`/`sys.exit` in blocks of three or more lines, and before `if`/`for`/`try`/`except` (before the preceding comment, if any).

* File section order: imports, constants, module vars, classes, private functions, public functions, with `# ---`-style section separators (max 80 chars wide, all-caps section name).

* Imports are always global, except the one deliberate `from Crypto.Cipher import AES` inside `xts_decrypt_sector()`, which is commented as an intentional exception so ticket/tag-resolved packages never load pycryptodome.

* Comment lines wrap at 80 characters. Ruff caps code lines at 120.

* Comments explain **why**, not what (see `.kiro/steering/add-code-comments.md`).

## Testing

Tests live in `tests/` and run under Tryke. Full guidance is in `.kiro/steering/tryke-testing.md`. Key facts:

* `tests/builders.py` holds synthetic package builders (`pfs0`, `hfs0`, `xci`, `ticket_name`, `nca_header`, `write_keys`, `reader`, `run_cli`) plus `HEADER_KEY` and the `HAVE_REAL_*`/`HAVE_FF_XCI` availability flags. Reuse these; do not invent new builders.

* `test_containers.py` covers `partition`/`package_files`/`is_package`; `test_title.py` covers key parsing, AES-XTS, meta NCAs, resolution order, and `application_of`; `test_cli.py` runs the CLI end-to-end in a subprocess.

* Property-style tests use seeded `random.Random(0)` loops, not Hypothesis (no such dependency). Tryke discovery is static, so never generate tests in a `for` loop; use `@test.cases` with string-literal labels.

* CLI tests run `python -m nx_title_id` in a subprocess with an isolated `HOME`, because Tryke workers use stdout for JSON-RPC and must not have it redirected in-process.

* Tests gated on local-only assets (real NSPs at `tests/<title ID>.nsp`, `tests/ff.xci`, real `~/.switch/prod.keys`) skip cleanly via `@test.skip_if` when the assets are absent. These are gitignored, so they are missing in CI and fresh clones.

* Exit codes are part of the contract: `0` one game, `1` setup/input error, `2` bad CLI args, `3` more than one game.

## Repository layout

| Path                          | What it is                                                       |
|-------------------------------|------------------------------------------------------------------|
| `src/nx_title_id/`            | The implementation (`__init__.py`) and the `python -m` shim      |
| `tests/`                      | Tryke tests and `builders.py`                                    |
| `docs/quickstart.md`          | Quick flow summary of the tool                                   |
| `docs/comprehensive.md`       | Deep architecture audit                                          |
| `README.md`                   | End-user usage, resolution order, exit status                    |
| `CHANGELOG.md`                | Keep a Changelog format; current work sits under `## Unreleased` |
| `pyproject.toml`              | Project metadata, deps, console script, Tryke config             |
| `Taskfile.dist.yml`, `tools/` | Task definitions (lint, build, test, clean, docs)                |
| `hk.pkl`                      | hk hook/linter configuration                                     |
| `.kiro/`                      | Kiro steering, hooks, agents, specs, settings (see below)        |
| `.kirograph/`                 | KiroGraph semantic index (do not edit by hand)                   |

## Kiro automation

This repo is configured for the Kiro IDE. Understanding this is essential before you act.

### Hard rules

* **Never commit.** `.kiro/steering/commit-rules.md` is an always-on rule: do not run `git commit` under any circumstances. Make changes in the working tree and let the human commit.

* **Core premises** (`.kiro/steering/core-premises.md`, always on): don't assume or hide confusion; write the minimum code that solves the problem; touch only what you must; define success criteria and verify; cite sources.

### Steering documents (`.kiro/steering/`)

Steering files inject rules and workflows. Some are always on, some auto-attach by file glob, some are manual. Notable ones:

* `python-code-conventions.md` — the Pyrefly/Ruff/annotation rules summarized above.

* `tryke-testing.md` — how to write and run Tryke tests.

* `markdown-style.md` — Markdown rules (use `*` for bullets, `_italics_`, `**bold**`, aligned tables, fenced blocks with a language, no line wrapping, proper-noun casing). Markdown is linted by rumdl.

* `add-code-comments.md` — comments must explain why, not what.

* `commit-rules.md`, `core-premises.md` — the hard rules above.

* `fd-instead-of-find.md` — prefer `fd` over `find` in scripts.

* `root-level-readme.md`, `comprehensive-and-quickstart.md`, `generate-agents-md.md` — document-generation guides.

* `kirograph*.md` — on-demand KiroGraph workflows (review, debug, architecture, onboard, refactor, security, patterns, git-context, memory, wiki). Activate with `/kirograph-<name>` or read the file directly.

### KiroGraph (code intelligence)

KiroGraph maintains a semantic graph in `.kirograph/`. Prefer its MCP tools over grep/glob/raw reads for code navigation: `kirograph_context` to orient, `kirograph_node` for a symbol, `kirograph_callers`/`kirograph_callees` for call flow, `kirograph_impact` before editing.

The full tool map is in `.kiro/steering/kirograph.md`. MCP server and the `kirograph` agent are configured in `.kiro/settings/mcp.json` and `.kiro/agents/kirograph.json`.

### Hooks (`.kiro/hooks/`)

These run automatically on Kiro agent events:

| Hook                      | Trigger | Effect                                             |
|---------------------------|---------|----------------------------------------------------|
| `kirograph-sync-if-dirty` | Stop    | Re-syncs the code graph if the tree changed        |
| `kirograph-mem-capture`   | Stop    | Prompts to store durable decisions/bugs/patterns   |
| `kirograph-watchmen`      | Stop    | Synthesizes stored memory into briefs/skill files  |
| `kirograph-wiki-ingest`   | (wiki)  | Builds a wiki ingest prompt from session knowledge |
| `kirograph-wiki-lint`     | (wiki)  | Health-checks wiki pages                           |

The `kirograph` agent also syncs the graph on `agentSpawn`, `userPromptSubmit`, and `stop` via `kirograph sync-if-dirty`.

Git hooks (separate from Kiro) are managed by `hk` and run on `pre-commit` (fix + commit-message validation via `gommit`) and `pre-push` (the slow profile). Install with `task install:hooks`.

### Specs (`.kiro/specs/`)

Feature and bugfix work is organized as specs. `audit-robustness-fixes/` holds the requirements, design, and tasks for the robustness hardening already shipped (set-returning resolution, per-package skip-on-error, warn-once keys, header sanity limits, exit code 3). Consult a spec's `design.md` before touching code it governs.

## Working effectively here

1. Orient with `kirograph_context` (or read `docs/comprehensive.md`) before editing.

2. Make the minimum change; match the existing style in `__init__.py`.

3. Verify: `uv run tryke test`, `uvx pyrefly check <files>`, `uv run ruff check`, `uv run ruff format --check`. All must be clean before you call the work done.

4. For Markdown changes, run `rumdl check` (and `rumdl fmt` to auto-fix table alignment).

5. Do not commit. Leave the staged/working changes for the human to review and commit.
