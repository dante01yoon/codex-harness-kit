# The harness in this project

Installed with codex-harness-kit. Four parts shape how Codex works here.

| Role | Part | When it runs | Enforced? |
| --- | --- | --- | --- |
| Tell | Harness rules in `AGENTS.md` | Start of every chat | No. Guidance the agent follows |
| Block | `.codex/harness/guard.py` (PreToolUse) | Before shell commands and file edits | Yes. Denies edits to protected files, reads of secret files, and real secrets |
| Check | `.codex/harness/verify.py` (Stop) | Before the agent finishes a response | Yes. On failure the agent keeps working |
| Notice | `.codex/harness/verify.py` (UserPromptSubmit + Stop) | Start and end of each turn | Yes. If a protected file changed through a path the guard cannot see, the agent is told to stop and report |
| Rules | `harness.py lint` (`lint.rules`) | before Codex finishes, when it is a verification command | Yes. Sends back the file, line and how to fix it |
| See | `harness.py see` (`see`) | after UI changes, or before finishing when it is a verification command | Yes when verifying. Leaves screenshots, console errors and the server log for Codex to look at |
| Stop | `verify.max_continuations_per_turn` | When verification keeps failing or the agent hands off with BLOCKED | Yes. No more retries; a warning instead |

## Commands

```sh
python3 .codex/harness/harness.py doctor
python3 .codex/harness/harness.py test
python3 .codex/harness/harness.py check --bash "rm tests/a.test.ts"
python3 .codex/harness/harness.py verify
python3 .codex/harness/harness.py log
python3 .codex/harness/harness.py lint              # project rules: location and how to fix
python3 .codex/harness/harness.py lint --baseline   # person only: record today's violations as known
python3 .codex/harness/harness.py see               # start the app, take screenshots, collect console errors
python3 .codex/harness/harness.py ci --base origin/main
```

## Changing the criteria

A human edits protected files. To let the agent do it, set `mode` in `harness.json` to `observe` for a while (it logs instead of blocking; the file itself is protected, so a human makes that change) and set it back to `enforce` afterwards.

## Growing the rules and the screen check

When Codex makes the same mistake twice, add a rule to `lint.rules` in `harness.json`. Write `why` and `fix` so Codex can read them and repair the code on its own. When you add a screen, add its path to `see.pages`. This file is protected too, so a person edits it.

## Limits

- `see` starts a local server. Codex cannot open a port inside its default sandbox (EPERM), but the Stop hook runs outside the sandbox, so it works as a verification command. Results go outside `.codex/` (`artifacts/`), because the sandbox cannot write to `.codex/`.

- Hooks run only after you trust them, and changing `hooks.json` or a hook command needs trust again. Untrusted project hooks are skipped without a warning.
- The guard parses command strings and patches. Obfuscated commands, scripts that build paths at run time, and writes through MCP tools may get past it.
- Hooks run on your machine only. On a shared repository, run the same checks in CI and branch protection.
