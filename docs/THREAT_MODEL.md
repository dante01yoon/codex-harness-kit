# What it blocks, and what it does not

**English** | [한국어](THREAT_MODEL.ko.md)

codex-harness-kit is designed against a **cooperative agent under pressure to finish**: Codex, asked to make the tests pass, changes an expectation, deletes a test, loosens a setting or writes a real key into the code. It is not a sandbox against an attacker.

## Blocked

- Adding, editing, deleting or moving protected paths with `apply_patch`, including `Move to:`, absolute paths, `..`, headers with quotes, backslashes or extra spaces, and `apply_patch <<EOF` called from the shell
- Changing protected paths with `git apply`, `git am` or `patch`, whether the patch comes from a file, standard input (`< fix.patch`) or a heredoc. A patch piped in from another command is refused because it cannot be inspected
- Shell commands that change protected paths: redirections (`>`, `>>`, `>|`, `&>`, `exec >`), `tee`/`sponge`, the target of `cp`/`mv`/`ln`/`rsync`/`install` (including `-t`), `rm`/`touch`/`truncate`/`chmod`, `sed -i`/`perl -i`/`awk -i inplace`, `ed`/`ex`/`vim -c`, `dd of=`, `find -delete`/`-exec` (the `-name` filter is taken into account), `git checkout`/`restore`/`rm`/`mv`
- Indirection: relative paths after `cd`/`pushd`, subshells, `bash -c`/`sh -lc`, `eval`, shell or Python code passed in a heredoc, `python -c`/`node -e` code that writes, paths built with `$(pwd)`, `$PWD` or `$(git rev-parse --show-toplevel)`, `$(...)` and backticks involving a protected path, paths passed through `xargs`
- Globs are expanded against real files (`rm tests/*.ts` is blocked, `rm lib/*.js` passes)
- Deleting or moving a folder that contains protected files (`rm -rf src` when there is `src/a.test.ts`)
- Formatters rewriting protected paths (`prettier --write .`, `black .`, `gofmt -w .`, ...)
- Expectation-update flags, whichever runner starts the tests (`vitest -u`, `pnpm exec vitest -u`, `jest --updateSnapshot`, `playwright --update-snapshots`, ...)
- Rolling back committed tests (`git checkout HEAD~3 .`, `git checkout <commit>`, `git switch --detach <commit>`, `git restore --source=…`, `git reset --hard <rev>`, `git revert`, `git read-tree -u`), and `git stash`/`reset --hard`/`checkout .`/`clean` while a human's test edits are uncommitted
- Changing test settings: `test`/`lint`/`typecheck` script lines in `package.json` or pytest settings lines in `pyproject.toml`/`setup.cfg` through `apply_patch`, and `npm pkg set scripts.*`
- Switching the harness off: changes to `.codex/hooks.json`, `.codex/config.toml`, `.codex/harness/` or `~/.codex/`
- Reading or writing secret files such as `.env`, including globs (`cat .env*`), `grep -r`, `rg --hidden` and `git show HEAD:.env`
- Strings that look like real secrets in a command or in lines a patch adds
- On macOS and Windows, paths that differ only in case (`rm TESTS/...`) are treated as the same path

## Blocked on purpose

- `cp .env.example .env`: it writes a secret file. A human creates it.
- A formatter or `--fix` run over a whole folder that holds protected files (`npx eslint --fix src` when `src` has `*.test.ts`): test files would change too, so name the files instead.

## Not blocked

The cases below are not blocked before they run. They are listed in `KNOWN_GAPS` in `tests/test_probes.py`, and the tests assert that they still pass. If the guard starts catching one, that test fails and this document is updated with it.

However, **if a protected file actually changes, it is noticed at the end of the turn.** Hashes of protected files are taken when a turn starts (UserPromptSubmit hook) and compared when it ends (Stop hook); if anything changed, Codex is told to stop and report it. The change is not undone, but it does not pass silently. Reading a secret file is not caught this way. Linked git worktrees nested in the project and untracked paths git ignores are not part of the comparison (a worktree's copy of a test is not the protected file); a script that rewrites a protected file there passes unnoticed, while the guard still blocks direct edits.

| Case | Why |
| --- | --- |
| `python3 scripts/fix.py` | The write happens inside a script file, not in the command string |
| `tar -xf /tmp/b.tar` | Archive contents are not inspected |
| `xargs rm < /tmp/list` | The paths come from another file |
| `node -e "fs.rmSync(['tes','ts'].join(''))"` | The path is assembled at run time |
| `ln -s tests t && rm t/rules/helpers.ts` | A folder alias created in the same command is not followed (files matching the name rule `**/*.test.*` are still blocked) |
| `node -r dotenv/config -e '...'` | A library reads the secret file |

Also:

- **Obfuscation.** Assembled variables (`d=te; rm ${d}sts/a`), encoded commands (`echo ... | base64 -d | sh`) and the like. A limit of string inspection.
- **Paths the hook does not see.** File writes through MCP tools (the default matcher covers `Bash` and `apply_patch` only), input sent with `write_stdin` to an already running shell, and special tools that skip the hook path.
- **Test settings in `package.json` or `pyproject.toml` changed from the shell.** Patches to those lines and `npm pkg set scripts.*` are blocked, but `sed -i` on the file is not, because protecting the whole file would also block adding dependencies. Add the file to `protect.paths` if you need that.
- **Untrusted hooks.** Codex silently skips new or changed project hooks until a person trusts them. With `codex exec` and no UI this is easy to miss.
- **A hook that does not run.** If Python is missing or a hook exceeds its timeout, Codex goes ahead with the tool call. The guard catches its own errors and blocks (`on_error`), and the tests check that each decision takes less than 3 seconds, but nothing helps if the process cannot start. Check with `harness.py doctor`.
- **Every form of secret.** Only the configured patterns are found. Use a dedicated scanner such as gitleaks before pushing.
- **Outside your machine.** Hooks run locally. On a shared repository, CI and branch protection must repeat the checks.

## Default behavior on failure

| Situation | Guard (PreToolUse) | Verification (Stop) |
| --- | --- | --- |
| Settings file missing or invalid | blocks (`on_error: block`) | finishes with a warning |
| Verification command not found (127, `No module named`) | - | reported as an environment problem, warning only |
| Codex stops with a line starting with `BLOCKED` | - | not sent back, warning only |
| Harness folder not found | answers `{}` and lets the call through | answers `{}` |
| Internal error in the hook | blocks (`on_error: block`) | finishes with a warning |
| Verification command fails | - | Codex keeps fixing (at most `max_continuations_per_turn` times) |
| Verification command times out | - | treated as a failure |
| Hook process cannot start | Codex lets the call through | Codex lets the turn end |

The Stop hook does not block on its own errors so that one bad setting cannot leave every response unable to finish.
