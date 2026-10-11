# codex-harness-kit

**English** | [한국어](README.md)

A per-project harness for OpenAI Codex that **keeps the agent away from your tests and verification criteria** and **makes it verify before it finishes**. One command installs four parts:

| Role | Part | What it does |
| --- | --- | --- |
| Tell | Harness rules in `AGENTS.md` | Tests are human-owned; when blocked, stop instead of working around it |
| Block | PreToolUse hook `guard.py` | Denies edits to tests, test config and the harness itself, reads of `.env`, and real secrets, right before the tool runs |
| Check | Stop hook `verify.py` | Runs your tests and type checks before Codex ends a response; on failure Codex keeps fixing |
| Notice | Protected-file snapshot at turn start and end | If a script or MCP tool changed a protected file behind the guard's back, Codex is told to stop and report |
| Stop | Retry limit | After a set number of failed attempts, or when Codex hands off with `BLOCKED`, it stops and leaves a warning |

Standard library only (Python 3.9+). Verified with Codex CLI 0.161.0 on macOS.

## Quick start

```bash
git clone https://github.com/dante01yoon/codex-harness-kit.git
python3 codex-harness-kit/install.py ~/projects/my-app --dry-run --lang en
python3 codex-harness-kit/install.py ~/projects/my-app --lang en
cd ~/projects/my-app
python3 .codex/harness/harness.py doctor
python3 .codex/harness/harness.py test
```

Then **trust the hooks**: CLI `/hooks`, or the app's Settings > Hooks > your project > Trust all. Codex silently skips project hooks until they are trusted.

## Presets

`node`, `python`, `go` and `generic` are detected automatically (`--preset` to override, `--verify "<command>"` to set verification commands). All presets also protect the harness files, `.env`-style files and nine secret patterns.

## Commands

```bash
python3 .codex/harness/harness.py doctor | test | verify | log
python3 .codex/harness/harness.py ci --base origin/main   # fail a PR that changes protected paths
python3 .codex/harness/harness.py check --bash "rm -rf tests"
```

Start with `"mode": "observe"` in `.codex/harness/harness.json` to log instead of block while you tune the rules.

## Quality

- 53 unit tests run the guard, the Stop hook and the installer in temporary git repositories, including a red-team regression corpus of 94 commands that must be blocked, 64 that must pass and 6 documented gaps.
- `scripts/e2e_codex.py` drives a real `codex exec` against a throwaway project. Results: [docs/VERIFICATION.md](docs/VERIFICATION.md).
- CI on Ubuntu and macOS with Python 3.9 and 3.12.

## Limits

A string-level guard for a cooperative agent under pressure, not a sandbox. Writes inside script files, obfuscated commands and writes through MCP tools can get past it before they run; if they change a protected file, the end-of-turn snapshot check still catches it. See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md). For destructive commands in general use [dcg](https://github.com/Dicklesworthstone/destructive_command_guard) or [cc-safety-net](https://github.com/kenryu42/cc-safety-net); for repository-wide secret scanning use [gitleaks](https://github.com/gitleaks/gitleaks). Hooks only run on your machine, so repeat the checks in CI.

## Uninstall

```bash
python3 codex-harness-kit/install.py ~/projects/my-app --uninstall [--purge]
```

MIT License.
