## Harness rules

This project runs the hooks in `.codex/harness/`. Settings live in `.codex/harness/harness.json`; `docs/harness.md` explains them.

- Do not change protected files: {protected}. Tests and verification config are human-owned criteria.
- If a criterion seems wrong, do not edit it. Stop and hand a human the files to change, the reason, and the new expected values.
- If a hook blocks an action, do not work around it. Stop with BLOCKED and report the reason and the options.
- These checks run automatically before you finish a response. If one fails, fix the implementation.
{verify_commands}
- If verification keeps failing and the harness stops retrying, summarize the failing command and the likely cause for a human.
- Never read or write secret files such as `.env`. Use test keys or environment variable names instead of real keys.
