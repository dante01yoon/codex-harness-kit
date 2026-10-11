#!/usr/bin/env python3
"""Command-line helper for the Codex harness.

  python3 .codex/harness/harness.py doctor        check the installation
  python3 .codex/harness/harness.py test          run the examples in harness.json
  python3 .codex/harness/harness.py check --bash "rm tests/a.test.ts"
  python3 .codex/harness/harness.py check --patch-file change.patch
  python3 .codex/harness/harness.py verify        run the verification commands now
  python3 .codex/harness/harness.py log [-n 20]   show recent hook decisions
  python3 .codex/harness/harness.py ci --base origin/main   fail if a branch changed protected paths
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harnesslib as hl  # noqa: E402
import guard  # noqa: E402
import verify  # noqa: E402

OK, WARN, FAIL = "ok", "warn", "fail"


def evaluate(cfg: Dict[str, Any], root: str, kind: str, text: str, cwd: str = "") -> List[Dict[str, Any]]:
    tool = "apply_patch" if kind == "patch" else "Bash"
    payload = {"tool_name": tool, "tool_input": {"command": text}, "cwd": cwd or root}
    return guard.decide(payload, cfg, root)


def cmd_check(args) -> int:
    root = hl.project_root()
    cfg = hl.load_config()
    if args.patch_file:
        with open(args.patch_file, encoding="utf-8") as f:
            kind, text = "patch", f.read()
    else:
        kind, text = "bash", args.bash
    findings = evaluate(cfg, root, kind, text)
    if not findings:
        print("ALLOW")
        return 0
    print("DENY")
    for f in findings:
        print(" -", guard.render(f, cfg))
    return 1


def cmd_test(args) -> int:
    root = hl.project_root()
    cfg = hl.load_config()
    ex = cfg.get("examples", {})
    failures = 0
    total = 0
    for expect in ("block", "allow"):
        for item in ex.get(expect, []):
            kind = "patch" if "patch" in item else "bash"
            text = item.get("patch") or item.get("bash") or ""
            findings = evaluate(cfg, root, kind, text)
            got = "block" if findings else "allow"
            total += 1
            mark = "PASS" if got == expect else "FAIL"
            if got != expect:
                failures += 1
            label = item.get("why") or text.splitlines()[0][:70]
            print(f"{mark}  expect {expect:<5}  {label}")
            if got != expect and findings:
                print("      ", guard.render(findings[0], cfg))
    print(f"\n{total - failures}/{total} examples behave as expected")
    return 1 if failures else 0


def cmd_verify(args) -> int:
    root = hl.project_root()
    cfg = hl.load_config()
    if not cfg["verify"]["commands"]:
        print("verify.commands is empty")
        return 0
    ok, results = verify.run_commands(cfg, root)
    print(verify.details(results, cfg, hl.compile_secrets(cfg)))
    return 0 if ok else 1


def cmd_log(args) -> int:
    root = hl.project_root()
    cfg = hl.load_config()
    path = os.path.join(root, cfg["log"]["path"])
    if not os.path.exists(path):
        print("no events yet:", path)
        return 0
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()[-args.n:]
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        what = e.get("preview") or ", ".join(f"{r['name']}={r['code']}" for r in e.get("results", [])) or e.get("error", "")
        print(f"{e.get('ts', '')}  {e.get('hook', ''):<6} {e.get('decision', ''):<11} {what}")
    return 0


def check_hooks_json(root: str) -> List[tuple]:
    out = []
    path = os.path.join(root, ".codex", "hooks.json")
    if not os.path.exists(path):
        return [(FAIL, ".codex/hooks.json is missing (run install.py again)")]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        return [(FAIL, f".codex/hooks.json is not valid JSON: {e}")]
    hooks = data.get("hooks", {})
    want = {"PreToolUse": "guard.py", "Stop": "verify.py", "UserPromptSubmit": "verify.py --snapshot"}
    for event, script in want.items():
        found = any(all(part in h.get("command", "") for part in script.split()) for g in hooks.get(event, []) for h in g.get("hooks", []))
        out.append((OK if found else FAIL, f"hooks.json wires {event} -> .codex/harness/{script}" if found
                    else f"hooks.json has no {event} hook for .codex/harness/{script}"))
    for g in hooks.get("PreToolUse", []):
        if any("guard.py" in h.get("command", "") for h in g.get("hooks", [])):
            m = g.get("matcher", "")
            good = all(t in m for t in ("Bash", "apply_patch"))
            out.append((OK if good else WARN, f"PreToolUse matcher is {m!r}" + ("" if good else " (should cover Bash and apply_patch)")))
    stop_timeout = max([h.get("timeout", 600) for g in hooks.get("Stop", []) for h in g.get("hooks", []) if "verify.py" in h.get("command", "")] or [600])
    return out + [(OK, f"Stop hook timeout {stop_timeout}s")]


def check_runner(root: str, run: str) -> tuple:
    """Is the program a verification command needs actually installed?"""
    try:
        words = shlex.split(run)
    except ValueError:
        return (FAIL, f"verify command `{run}` cannot be parsed")
    if not words:
        return (FAIL, "empty verify command")
    exe = words[0]
    if not (shutil.which(exe) or os.path.exists(os.path.join(root, exe))):
        return (FAIL, f"verify command `{run}`: {exe} not found on PATH")
    if exe in ("npx", "pnpx", "bunx"):
        prog = next((w for w in words[1:] if not w.startswith("-")), "")
        if prog and not os.path.exists(os.path.join(root, "node_modules", ".bin", prog)):
            return (WARN, f"verify command `{run}`: {prog} is not installed in node_modules (run your package install)")
    if re.match(r"^python3?(\.\d+)?$", os.path.basename(exe)) and words[1:2] == ["-m"] and len(words) > 2:
        mod = words[2]
        ok = subprocess.run([exe, "-c", f"import importlib.util,sys; sys.exit(0 if importlib.util.find_spec({mod!r}) else 1)"],
                            cwd=root, capture_output=True).returncode == 0
        if not ok:
            return (FAIL, f"verify command `{run}`: python module {mod} is not installed")
    return (OK, f"verify command `{run}`")


def run_hooks(root: str, cfg: Any) -> List[tuple]:
    """Run the hook commands exactly as hooks.json has them, the way Codex does."""
    path = os.path.join(root, ".codex", "hooks.json")
    try:
        with open(path, encoding="utf-8") as f:
            hooks = json.load(f).get("hooks", {})
    except (OSError, ValueError):
        return []
    rows = []

    def call(command: str, payload: Dict[str, Any]) -> tuple:
        p = subprocess.run(["/bin/sh", "-c", command], input=json.dumps(payload), cwd=root,
                           capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout.strip(), p.stderr.strip()

    for g in hooks.get("PreToolUse", []):
        for h in g.get("hooks", []):
            if "guard.py" not in h.get("command", ""):
                continue
            code, out, err = call(h["command"], {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": root,
                                                 "hook_event_name": "PreToolUse"})
            good = code == 0 and out.startswith("{") and "deny" not in out
            rows.append((OK if good else FAIL, "guard hook runs and allows `ls`" if good
                         else f"guard hook did not answer correctly (exit {code}): {(out or err)[:160]}"))
            sample = next((i for i in (cfg or {}).get("examples", {}).get("block", []) if "bash" in i), None)
            if sample and good:
                code, out, err = call(h["command"], {"tool_name": "Bash", "tool_input": {"command": sample["bash"]}, "cwd": root,
                                                     "hook_event_name": "PreToolUse"})
                denied = '"deny"' in out
                rows.append((OK if denied else FAIL, f"guard hook denies `{sample['bash'][:50]}`" if denied
                             else f"guard hook did not deny `{sample['bash'][:50]}`"))
    for g in hooks.get("UserPromptSubmit", []):
        for h in g.get("hooks", []):
            if "verify.py" in h.get("command", ""):
                code, out, err = call(h["command"], {"hook_event_name": "UserPromptSubmit", "prompt": "doctor", "cwd": root})
                rows.append((OK if code == 0 else FAIL, "snapshot hook runs" if code == 0 else f"snapshot hook failed: {err[:160]}"))
    return rows


def cmd_ci(args) -> int:
    """For CI: fail when a branch changes protected paths or test settings compared with a base."""
    root = hl.project_root()
    cfg = hl.load_config()
    p = subprocess.run(["git", "diff", "--name-only", f"{args.base}...HEAD"], cwd=root, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stderr.strip() or "git diff failed", file=sys.stderr)
        return 2
    rules = hl.PathRules(cfg["protect"]["paths"])
    hits = [f for f in p.stdout.splitlines() if rules.match(f)]
    diff = subprocess.run(["git", "diff", "-U0", f"{args.base}...HEAD", "--", "package.json", "pyproject.toml", "setup.cfg"],
                          cwd=root, capture_output=True, text=True).stdout
    current, settings = "", []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = os.path.basename(line[6:])
        rule = guard.TEST_SETTINGS.get(current)
        if rule and line[:1] in "+-" and not line.startswith(("+++", "---")) and rule.search(line[1:]):
            settings.append(f"{current}: {line[1:].strip()}")
    for f in hits:
        print(f"protected file changed: {f}")
    for s_ in settings:
        print(f"test setting changed: {s_}")
    if hits or settings:
        print("\nThese are human-owned verification criteria. Review them before merging.")
        return 1
    print(f"no protected paths changed since {args.base}")
    return 0


def cmd_lint(args) -> int:
    import lint
    return lint.main(["--baseline"] if args.baseline else [])


def cmd_see(args) -> int:
    import see
    return see.main([])


def cmd_doctor(args) -> int:
    root = hl.project_root()
    rows: List[tuple] = []
    rows.append((OK if sys.version_info >= (3, 9) else FAIL, f"python {sys.version.split()[0]} (needs 3.9+)"))
    groot = hl.git_root(root)
    if not groot:
        rows.append((WARN, "not a git repository: run `git init` (unchanged turns are re-verified every time without git)"))
    elif not os.path.samefile(groot, root):
        rows.append((WARN, f"git root is {groot}: Codex reads project hooks from the repository root, so these hooks may not load"))
    else:
        rows.append((OK, f"git root is {groot}"))
    cfg = None
    try:
        cfg = hl.load_config()
        rows.append((OK, f"harness.json is valid (mode={cfg['mode']}, on_error={cfg['on_error']})"))
    except hl.ConfigError as e:
        rows.append((FAIL, str(e)))
    rows += check_hooks_json(root)
    if cfg:
        for c in cfg["verify"]["commands"]:
            try:
                exe = shlex.split(c["run"])[0]
            except (ValueError, IndexError):
                exe = ""
            rows.append(check_runner(root, c["run"]))
        if not cfg["verify"]["commands"]:
            rows.append((WARN, "verify.commands is empty, so the Stop hook does nothing"))
        if not cfg["protect"]["paths"]:
            rows.append((WARN, "protect.paths is empty, so nothing is protected"))
        protects_self = any(cfg["protect"]["paths"]) and hl.PathRules(cfg["protect"]["paths"]).match(".codex/harness/harness.json")
        rows.append((OK if protects_self else WARN, "the harness protects its own config" if protects_self
                     else "add `.codex/` to protect.paths so the agent cannot switch the harness off"))
        ex = cfg.get("examples", {})
        n = len(ex.get("block", [])) + len(ex.get("allow", []))
        if n:
            bad = 0
            for expect in ("block", "allow"):
                for item in ex.get(expect, []):
                    kind = "patch" if "patch" in item else "bash"
                    got = "block" if evaluate(cfg, root, kind, item.get("patch") or item.get("bash") or "") else "allow"
                    bad += got != expect
            rows.append((OK if not bad else FAIL, f"{n - bad}/{n} examples behave as expected (details: harness.py test)"))
    if cfg:
        nrules = len(cfg.get("lint", {}).get("rules", []))
        if nrules:
            rows.append((OK, f"lint: {nrules} project rules (run: harness.py lint)"))
        if cfg.get("see", {}).get("url"):
            import see as see_mod
            chrome = see_mod.find_chrome(cfg)
            rows.append((OK if chrome else WARN, f"see: browser {chrome}" if chrome
                         else "see: Chrome/Chromium not found; set see.chrome"))
            out = cfg["see"].get("out", "")
            ign = subprocess.run(["git", "check-ignore", "-q", os.path.join(out, "x.png")], cwd=root,
                                 capture_output=True).returncode == 0 if groot else True
            rows.append((OK if ign else WARN, f"see: {out}/ is git-ignored" if ign else f"see: add {out}/ to .gitignore"))
    rows += run_hooks(root, cfg)
    ign = subprocess.run(["git", "check-ignore", "-q", ".codex/harness/logs/events.jsonl"], cwd=root,
                         capture_output=True).returncode == 0 if groot else True
    rows.append((OK if ign else WARN, ".codex/harness/logs/ is git-ignored" if ign else "add .codex/harness/logs/ to .gitignore"))
    rows.append((WARN, "Codex skips new or changed project hooks until you trust them: open /hooks in the CLI or Settings > Hooks in the app"))
    width = max(len(r[0]) for r in rows)
    for status, text in rows:
        print(f"[{status.upper():<{width}}] {text}")
    return 1 if any(r[0] == FAIL for r in rows) else 0


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="harness.py", description="Codex harness helper")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("test").set_defaults(fn=cmd_test)
    c = sub.add_parser("check")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--bash")
    g.add_argument("--patch-file")
    c.set_defaults(fn=cmd_check)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    ci = sub.add_parser("ci", help="fail if protected paths changed since --base")
    ci.add_argument("--base", default="origin/main")
    ci.set_defaults(fn=cmd_ci)
    li = sub.add_parser("lint", help="check project rules (lint.rules) and print fixes for the agent")
    li.add_argument("--baseline", action="store_true", help="record today's violations as known (person only)")
    li.set_defaults(fn=cmd_lint)
    sub.add_parser("see", help="start the app if needed, take screenshots, collect console errors").set_defaults(fn=cmd_see)
    lg = sub.add_parser("log")
    lg.add_argument("-n", type=int, default=20)
    lg.set_defaults(fn=cmd_log)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except hl.ConfigError as e:
        print("config error:", e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
