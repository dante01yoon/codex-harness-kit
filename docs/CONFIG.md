# Configuration reference (`.codex/harness/harness.json`)

**English** | [한국어](CONFIG.ko.md)

Installing creates `harness.json` from a preset. Reinstalling never overwrites it (`--force` regenerates it). The file is itself protected, so Codex cannot change it; a human edits it. Afterwards run `python3 .codex/harness/harness.py doctor`.

| Key | Default | Meaning |
| --- | --- | --- |
| `version` | `1` | Settings format version |
| `lang` | `"ko"` | Language of the messages sent to Codex (`ko`, `en`) |
| `mode` | `"enforce"` | `observe` logs instead of blocking. Use it when adopting the harness or while you change the criteria. The environment variable `CODEX_HARNESS_MODE` overrides it |
| `on_error` | `"block"` | What the guard (PreToolUse) does when it fails, for example on a broken config: block (`block`) or let the call through (`allow`). Codex lets a call through when a hook crashes, so the default blocks. The Stop hook never blocks on its own errors; it leaves a warning |
| `protect.paths` | preset | Paths Codex must not create, edit, move or delete |
| `protect.allow_commands` | preset | Command prefixes that may take protected paths as arguments, such as test runners. `verify.commands` are added automatically |
| `protect.update_flags` | `-u`, `--update`, ... | Even an allowed test runner is blocked with one of these flags, because updating snapshots rewrites the expectations |
| `deny_read.paths` | `.env`-style files, `*.pem`, ... | Paths that must not be read or written |
| `secrets.patterns` | 9 patterns | A list of `{"id", "regex"}`, searched in commands and in lines a patch adds |
| `verify.commands` | detected | A list of `{"name", "run", "timeout", "cwd", "stop_on_failure"}`, run in order |
| `verify.skip_when_unchanged` | `true` | Skip verification when nothing changed since the last pass, so question-only turns stay fast. Needs a git repository |
| `verify.total_timeout` | `570` | Seconds for all verification commands together. Each command's `timeout` only applies within what is left. At install time the Stop hook timeout is set to this value + 30 s (at most 600 s) |
| `verify.check_protected_changes` | `true` | Remember hashes of protected files at the start of a turn (UserPromptSubmit) and report files that changed by the end. Catches changes made through paths the guard cannot see (scripts, MCP tools) |
| `verify.max_continuations_per_turn` | `2` | How many times a failed verification may send Codex back to work. After that it stops and leaves a warning |
| `verify.respect_blocked` | `true` | If Codex's last message has a line starting with `BLOCKED` (it handed off to a human), a failed verification does not send it back; a warning is left instead |
| `verify.output_tail_lines` | `40` | How many trailing lines of a failed command's output Codex sees |
| `log.enabled`, `log.path`, `log.max_bytes` | on | Blocked calls and verification results as JSON Lines. A blocked command is stored as its first 240 characters plus a hash, with only values matching `secrets.patterns` replaced by `[REDACTED:id]`. Other secrets can remain, so never commit the log folder (the installer adds it to `.gitignore`) |
| `examples.block`, `examples.allow` | preset | Examples checked by `harness.py test`. Update them when you change the rules |

## Path patterns

Rules similar to `.gitignore`, relative to the project folder.

| Pattern | Matches |
| --- | --- |
| `tests/` | the `tests` folder and everything below it |
| `vitest.config.*` | a file with this name in any folder (a pattern without `/` matches by name) |
| `**/*.test.*` | `*.test.*` files at any depth |
| `src/legacy/*.ts` | `.ts` files directly in `src/legacy` |
| `!.env.example` | excluded again; later rules win |

On macOS and Windows matching ignores case, because the file systems usually do.

## Example: protect lint and type-check settings too

An agent can also pass by loosening the checks instead of the tests. If that worries you, protect more paths. Legitimate changes, such as adding a package, are then blocked too, so a human makes them.

```json
"protect": {
  "paths": ["tests/", "**/*.test.*", "vitest.config.*", "tsconfig.json", "eslint.config.*", ".eslintrc*", ".codex/hooks.json", ".codex/config.toml", ".codex/harness/"]
}
```

## Example: verification commands

```json
"verify": {
  "commands": [
    {"name": "test", "run": "npm test --silent", "timeout": 300},
    {"name": "typecheck", "run": "npx --no-install tsc --noEmit", "timeout": 300},
    {"name": "lint", "run": "npm run lint --silent", "timeout": 300, "stop_on_failure": false}
  ]
}
```

If verification takes long, raise `verify.total_timeout` and run the installer again. The Stop hook timeout changes, so trust the hooks again. When a command cannot be found (exit code 127, `command not found`) or a Python module is missing (`No module named`), it is treated as an environment problem, not an implementation problem: Codex is not sent back, and a warning is left.
