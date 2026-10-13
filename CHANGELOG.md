# Changelog

## 0.2.3 - 2026-10-13

- The installer now git-ignores `.codex/harness/__pycache__/` next to `.codex/harness/logs/`. Running `harness.py doctor` or any hook writes bytecode there, so after `install.py` a plain `git add -A` staged the `.pyc` files, and once committed, later hook runs showed them as modified tracked files.
- Re-running the installer on a project installed with 0.2.1 or earlier appends the missing line; your own `.gitignore` lines are kept as they are.
- `--uninstall --purge` removes both lines it added and nothing else. If the project already committed the `.pyc` files, run `git rm -r --cached .codex/harness/__pycache__` once.
- Tests: a temporary git repository is installed, `harness.py doctor` is run with bytecode writing forced on, and `git status` must list no `__pycache__` or `.pyc` entry. A second test covers merging into an existing `.gitignore` and purging.

## 0.2.2 - 2026-10-13

- The turn-start/turn-end protected-file snapshot no longer compares files inside linked git worktrees nested in the project (`git worktree add .worktrees/<name>`). Their copy of a protected file is another checkout of the same file, not a change to it, but the snapshot walked the whole tree: creating a worktree during a turn made the Stop hook report `.worktrees/<name>/tests/...` as "changed behind the guard's back", tell Codex to stop with BLOCKED, and end the run unfinished (seen with Codex CLI 0.162.1).
- The snapshot also skips untracked paths that git ignores (`.worktrees/`, build output). Tracked files are always compared, even when they match a `.gitignore` pattern. Both lists come from git (`git worktree list --porcelain`, `git ls-files -o -i --exclude-standard --directory`); without git, or if git does not answer, the snapshot compares everything as before.
- Unchanged: the PreToolUse guard still denies edits to protected files inside a nested worktree (`.worktrees/<name>/tests/...` matches `tests/`), and an out-of-band edit of a protected file in the main checkout is still reported. A project root that is itself a linked worktree is covered in full. `harness.json` has no new keys.
- Trade-off: a script that rewrites a protected file inside a nested worktree or an ignored path is no longer noticed at the end of the turn. The guard still stops direct edits there.
- Tests: creating a nested worktree in a turn, ignored paths, the guard denying `Bash` and `apply_patch` edits in the worktree, an out-of-band edit next to a worktree, a project that is itself a worktree, a tracked file that matches `.gitignore`, and a project without git.

## 0.2.1 - 2026-10-11

- `harness.py see` now opens pages through the Chrome DevTools protocol and sets the viewport exactly. Headless Chrome will not make a window narrower than about 500px, so in 0.2.0 a `390x844` screenshot was a 500px layout cropped to 390px and phone layouts were never really checked. Viewports under 768px are emulated as a touch phone, so a page without `<meta name="viewport">` is laid out at 980px as on a real phone.
- Console messages come from DevTools events: `console.*` calls, uncaught exceptions and failed loads, with their source. `console.error` is a problem even without an `Error:` prefix. The browser's own `/favicon.ico` request is ignored.
- If DevTools cannot be reached, `see` falls back to the plain screenshot and says so.
- Found while checking the book's demo screen with the kit: Codex opened the 390px screenshot, saw the right side cut off, and traced it to the browser, not the app.

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
