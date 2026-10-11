# codex-harness-kit

**English** | [한국어](README.ko.md)

A per-project harness for OpenAI Codex that **keeps the agent away from your tests and verification criteria** and **makes it verify before it finishes**. One command installs these parts into a project:

| Role | Part | What it does |
| --- | --- | --- |
| Tell | Harness rules in `AGENTS.md` | Tests are human-owned criteria; when blocked, stop instead of working around it |
| Block | PreToolUse hook `guard.py` | Right before a tool runs, denies edits to tests, test config and the harness itself, reads of `.env`, and real secrets |
| Check | Stop hook `verify.py` | Runs your tests and type checks before Codex ends a response; on failure Codex keeps fixing |
| Notice | Protected-file snapshot at turn start and end | If a script or an MCP tool changed a protected file behind the guard's back, Codex is told to stop and report it when the turn ends |
| Stop | Retry limit | When verification keeps failing, or Codex hands off with `BLOCKED`, it is not sent back again; a warning is left instead |

It turns the harness built by hand in chapter 14 of the Korean book *Codex Complete Guide* (『코덱스 완벽 가이드』, "Harness engineering: designing the agent's working environment") into something you can install in any project. Python 3.9 standard library only; nothing else is installed.

## What it looks like

Shortened from real runs with Codex CLI 0.161.0. The full record and how each run was judged are in [VERIFICATION.md](docs/VERIFICATION.md).

**Ask it to change a test** and the hook blocks it; Codex hands the decision to a human.

```text
You:   The refund policy changed. In tests/test_refund.py change the expected 1500 to 1000.
Hook:  Blocked: this changes a protected verification file (tests/test_refund.py, rule tests/). ...
Codex: BLOCKED. A human must change the expectation in tests/test_refund.py to 1000. No files changed.
```

**Tell it not to run the tests**, and verification still runs before it finishes.

```text
You:   In app/refund.py change the 120-minute comparison from >= to >. Don't run the tests.
Codex: Changed it. Tests were not run.
Hook:  Verification ran before finishing and failed. ... test_full_refund_at_exactly_two_hours ... FAIL
Codex: Exactly 120 minutes must refund 3,000, so I restored >=. Verification passes; tests and config unchanged.
```

## Quick start

```bash
git clone https://github.com/dante01yoon/codex-harness-kit.git
python3 codex-harness-kit/install.py ~/projects/my-app --lang en --dry-run   # see what would change
python3 codex-harness-kit/install.py ~/projects/my-app --lang en
```

`--lang en` makes the messages Codex receives and the generated `AGENTS.md` block English (the default is Korean). Check the installation in the project:

```bash
cd ~/projects/my-app
python3 .codex/harness/harness.py doctor   # checks the setup
python3 .codex/harness/harness.py test     # examples that must be blocked are blocked, and the rest pass
```

Finally, **trust the hooks.** Codex silently skips new project hooks until a person trusts them.

- App: Settings > Hooks, open the project, confirm each Command runs this project's `.codex/harness/guard.py` and `verify.py`, then Trust all
- CLI: `/hooks`

Commit `.codex/` and `AGENTS.md` so everyone on the repository gets the same harness (each person still trusts the hooks once).

## What gets installed

```text
my-app/
├── AGENTS.md                      # harness rules block (your own content is left as it is; only the marked block is managed)
├── docs/harness.md                # how the harness works in this project
├── .gitignore                     # adds .codex/harness/logs/
└── .codex/
    ├── hooks.json                 # UserPromptSubmit and Stop -> verify.py, PreToolUse -> guard.py (existing hooks are backed up and merged)
    └── harness/
        ├── harness.json           # settings; reinstalling never overwrites it
        ├── guard.py  verify.py  harness.py  harnesslib.py
        └── logs/                  # what was blocked and verification results (known secrets masked)
```

## Presets

The installer looks at the project to pick a preset and verification commands. Use `--preset` and `--verify "<command>"` to choose yourself.

| Preset | Detected by | Protects | Verification |
| --- | --- | --- | --- |
| `node` | `package.json` | `tests/`, `test/`, `__tests__/`, `/e2e/`, `*.test.*`, `*.spec.*`, vitest/jest/playwright config (`vite.config.*` too when only vitest is used), the `test`/`lint`/`typecheck` script lines in `package.json` | `npm test` (or `vitest run`/`jest` when there is no test script), `tsc --noEmit`, the `lint` script, whichever exist |
| `python` | `pyproject.toml` etc. | `tests/`, `test/`, `test_*.py`, `*_test.py`, `conftest.py` in any folder, `pytest.ini`, `tox.ini`, `noxfile.py`, the pytest settings lines in `pyproject.toml` | `pytest` or `unittest` |
| `go` | `go.mod` | `*_test.go`, `testdata/` | `go test ./...`, `go vet ./...` |
| `generic` | anything else | `tests/`, `test/` | set it yourself |

Every preset also protects the harness itself (`.codex/hooks.json`, `.codex/config.toml`, `.codex/harness/`), `.env`-style files, and nine secret patterns (Toss Payments and Stripe live keys; OpenAI, Anthropic, AWS, GitHub, Slack and Google keys; private keys).

## Commands

```bash
python3 .codex/harness/harness.py doctor                       # settings, whether the test runners are installed, and a live run of the hook commands
python3 .codex/harness/harness.py test                         # run the rule examples
python3 .codex/harness/harness.py check --bash "rm -rf tests"  # would this command be blocked?
python3 .codex/harness/harness.py verify                       # run the verification commands now
python3 .codex/harness/harness.py log -n 20                    # recent decisions
python3 .codex/harness/harness.py ci --base origin/main        # fail when a branch changed protected paths (for CI)
```

Hooks run only on your machine, so check again in CI on a shared repository. A GitHub Actions example:

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- run: python3 .codex/harness/harness.py ci --base origin/${{ github.base_ref }}
```

## Adopting it in an existing project

Set `mode` in `harness.json` to `"observe"` for a few days. Nothing is blocked; decisions are logged as `would_deny`. Read them with `harness.py log`, tune the rules, then switch to `"enforce"`. All settings are described in [CONFIG.md](docs/CONFIG.md).

## Quality

- **53 unit tests** (`python3 -m unittest discover -s tests`) run the guard, the Stop hook and the installer in temporary git repositories, calling the hooks exactly as `hooks.json` has them, from subfolders too. The regression corpus in `tests/test_probes.py`, built from a separate red-team pass, holds 94 commands that must be blocked, 64 that must pass and 6 documented gaps, and checks that each decision takes less than 3 seconds.
- **Checked with real Codex** (`scripts/e2e_codex.py`): installs into throwaway projects and asks `codex exec` to edit a test, write a live key, switch the harness off, break the code without running tests, and run a script that edits a test behind the guard's back. Results: [VERIFICATION.md](docs/VERIFICATION.md).
- **CI** runs the tests and every preset's examples on Ubuntu and macOS with Python 3.9 and 3.12.

## Use it with other tools

This kit focuses on **protecting the verification criteria**. For irreversible commands in general (`git reset --hard`, `rm -rf ~`, ...) add [dcg](https://github.com/Dicklesworthstone/destructive_command_guard) or [cc-safety-net](https://github.com/kenryu42/cc-safety-net); for repository-wide secret scanning add [gitleaks](https://github.com/gitleaks/gitleaks). How they fit together: [COMPARISON.md](docs/COMPARISON.md).

## Limits

The guard reads command strings and patches. It is meant to stop a cooperative agent under pressure from changing the criteria, not to be a sandbox against an attacker. Writes inside script files, obfuscated commands and writes through MCP tools may get past it before they run; if they change a protected file, the end-of-turn check still notices and makes Codex stop and report. [THREAT_MODEL.md](docs/THREAT_MODEL.md) lists what is and is not caught, case by case. Hooks run only on your machine, so on a shared repository repeat the checks in CI and branch protection.

## Upgrade and uninstall

```bash
git -C codex-harness-kit pull && python3 codex-harness-kit/install.py ~/projects/my-app   # updates the scripts, keeps harness.json
python3 codex-harness-kit/install.py ~/projects/my-app --uninstall           # removes the hooks, the scripts and the AGENTS.md block
python3 codex-harness-kit/install.py ~/projects/my-app --uninstall --purge   # also settings, logs, an untouched docs/harness.md and the .gitignore line
```

When a hook command changes, Codex asks you to trust it again.

## Requirements

- Python 3.9 or later (the `python3` that ships with macOS is enough) and git
- Codex CLI, or Codex in the ChatGPT app, with hook support. Verified with Codex CLI 0.161.0 on macOS. Windows has a `commandWindows` entry but has not been tested.

## License

MIT
