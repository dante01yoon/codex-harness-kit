# Using it with other tools

**English** | [한국어](COMPARISON.ko.md)

codex-harness-kit focuses on **protecting the verification criteria**. There are already good tools for dangerous commands in general and for secrets across a repository, so combine them as needed. Codex runs every hook that matches an event, and the call is blocked if any of them blocks.

| Tool | Focus | How to combine |
| --- | --- | --- |
| codex-harness-kit | Keep the agent from changing tests, verification config and the harness itself; verify before finishing; rules for what to do when blocked (`AGENTS.md`) | Installed per project |
| [destructive_command_guard (dcg)](https://github.com/Dicklesworthstone/destructive_command_guard) | Blocks irreversible commands such as `git reset --hard`, `rm -rf`, cloud and database commands; many rule packs | User-wide hook |
| [cc-safety-net](https://github.com/kenryu42/cc-safety-net) | Blocks destructive git and file commands and access to secret files; supports many coding CLIs | User-wide hook |
| [gitleaks](https://github.com/gitleaks/gitleaks) | Scans repositories and commits for secrets; the widest set of patterns | Pre-commit hook and CI |
| CI and branch protection | Enforce the same tests and checks outside your machine | A must on shared repositories |

## The bar this kit was built to

The criteria below are what comparable tools have in common.

| Criterion | In this kit |
| --- | --- |
| Install preview and uninstall | `install.py --dry-run`, `--uninstall`, `--purge`; an existing `hooks.json` is backed up and merged |
| Self-check | `harness.py doctor`: validates settings, checks that test runners are installed, and runs the hook commands from `hooks.json` to check their answers |
| Examples per rule, checked automatically | `examples` in `harness.json`, `harness.py test` |
| Observe mode before enforcing | `mode: observe`, `CODEX_HARNESS_MODE` |
| Logs with secrets masked | `.codex/harness/logs/events.jsonl`, `[REDACTED:id]` for known patterns, a hash of the command |
| Documented failure policy | `on_error`, [THREAT_MODEL.md](THREAT_MODEL.md) |
| Wrappers and indirection understood | `bash -c`, `eval`, heredocs, `$(...)`, `cd`, `env`/`sudo`/`timeout`, `npx`, `pnpm exec`, `python -m` |
| Checked with a real agent | `scripts/e2e_codex.py`, [VERIFICATION.md](VERIFICATION.md) |
| Catching what string checks miss | Hash comparison of protected files at turn start and end (Stop hook) |
| The same check in CI | `harness.py ci --base origin/main` |
| A failed install changes nothing | Every input is validated before anything is written |
| No dependencies | Python 3.9 standard library only |
