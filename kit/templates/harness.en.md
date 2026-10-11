# The harness in this project

Installed with codex-harness-kit. Four parts shape how Codex works here.

| Role | Part | When it runs | Enforced? |
| --- | --- | --- | --- |
| Tell | Harness rules in `AGENTS.md` | Start of every chat | No. Guidance the agent follows |
| Block | `.codex/harness/guard.py` (PreToolUse) | Before shell commands and file edits | Yes. Denies edits to protected files, reads of secret files, and real secrets |
| Check | `.codex/harness/verify.py` (Stop) | Before the agent finishes a response | Yes. On failure the agent keeps working |
| Notice | `.codex/harness/verify.py` (UserPromptSubmit + Stop) | Start and end of each turn | Yes. If a protected file changed through a path the guard cannot see, the agent is told to stop and report |
| Stop | `verify.max_continuations_per_turn` | When verification keeps failing or the agent hands off with BLOCKED | Yes. No more retries; a warning instead |

## Commands

```sh
python3 .codex/harness/harness.py doctor
python3 .codex/harness/harness.py test
python3 .codex/harness/harness.py check --bash "rm tests/a.test.ts"
python3 .codex/harness/harness.py verify
python3 .codex/harness/harness.py log
python3 .codex/harness/harness.py ci --base origin/main
```

## Changing the criteria

A human edits protected files. To let the agent do it, set `mode` in `harness.json` to `observe` for a while (it logs instead of blocking; the file itself is protected, so a human makes that change) and set it back to `enforce` afterwards.

## Limits

- Hooks run only after you trust them, and changing `hooks.json` or a hook command needs trust again. Untrusted project hooks are skipped without a warning.
- The guard parses command strings and patches. Obfuscated commands, scripts that build paths at run time, and writes through MCP tools may get past it.
- Hooks run on your machine only. On a shared repository, run the same checks in CI and branch protection.
