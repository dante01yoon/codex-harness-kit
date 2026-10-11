# Changelog

## 0.1.0 - 2026-10-10

First release.

- PreToolUse guard for `Bash` and `apply_patch`: protected paths, secret files, secret patterns
- Stop hook that runs verification commands, skips unchanged turns, limits retries and respects a `BLOCKED` handoff
- Installer with presets (node, python, go, generic), dry run, merge with existing hooks, managed `AGENTS.md` block, uninstall
- `harness.py` with `doctor`, `test`, `check`, `verify`, `log`
- Unit tests, red-team regression corpus, live Codex end-to-end script
