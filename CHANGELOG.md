# Changelog

## 0.2.0 - 2026-10-11

Let Codex see what it built and follow project rules, not only be stopped.

- `harness.py see`: starts the app when needed (`see.start`), takes screenshots per page and viewport with headless Chrome, collects console errors, uncaught exceptions, failed loads and HTTP errors, and the tail of the server log; writes `artifacts/harness/see/summary.txt`. Standard library only.
- `harness.py lint`: project rules in `lint.rules` (files, forbidden regex, why, fix). Violations print `file:line:col [rule]` and the fix for the agent. `--baseline` records today's violations so only new ones fail; the guard blocks the agent from re-baselining.
- Installer: `--lint`, `--see-url`, `--see-start`, `--see-verify` (adds the checks to the Stop hook), screenshots git-ignored, `AGENTS.md` block gains lint/see lines.
- `see.out` must be outside `.codex/` (the Codex sandbox cannot write there); doctor reports the browser and ignore status.
- Live end-to-end script for the new modules on fresh Node and Python apps: `scripts/e2e_see_lint.py`.

## Unreleased (docs)

- Documentation in two full languages: English (`README.md`, `docs/*.md`) and Korean (`README.ko.md`, `docs/*.ko.md`)
- THREAT_MODEL: test-setting lines in `package.json`/`pyproject.toml` changed through `apply_patch` are blocked; only shell edits of those files are a gap

## 0.1.0 - 2026-10-10

First release.

- PreToolUse guard for `Bash` and `apply_patch`: protected paths, secret files, secret patterns
- Stop hook that runs verification commands, skips unchanged turns, limits retries and respects a `BLOCKED` handoff
- Installer with presets (node, python, go, generic), dry run, merge with existing hooks, managed `AGENTS.md` block, uninstall
- `harness.py` with `doctor`, `test`, `check`, `verify`, `log`
- Unit tests, red-team regression corpus, live Codex end-to-end script
