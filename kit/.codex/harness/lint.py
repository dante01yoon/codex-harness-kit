#!/usr/bin/env python3
"""Project rules the agent must follow, checked mechanically.

Each rule in `lint.rules` of harness.json names the files it applies to, a regex that must not
appear in them, and a message written for the agent: why it is a problem and how to fix it.
A violation prints `file:line:col [rule] why` followed by `fix: ...`, so Codex can read the
output and repair the code itself. Exit code 1 when anything is found.

  {"id": "ui-no-money-math",
   "files": ["ui/**/*.ts"],
   "exclude": ["ui/**/*.test.ts"],
   "forbid": "\\b(refund|fee)\\w*\\s*[-+*/]=?",
   "why": "Money is decided in src/rules, not in the UI.",
   "fix": "Call the function in src/rules and render its result."}

A line can opt out with a comment containing `harness-lint-ignore: <rule id>` and a reason.

`harness.py lint --baseline` records the violations that exist today in
`.codex/harness/lint-baseline.json` (protected like the rest of the harness), so a project can
adopt a rule without fixing everything first: recorded violations are reported as known and only
new ones fail. The baseline can only shrink when a person regenerates it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harnesslib as hl  # noqa: E402

BASELINE = "lint-baseline.json"
IGNORE = re.compile(r"harness-lint-ignore:\s*([\w.,\s-]+)")
MAX_BYTES = 1_000_000

TEXT = {
    "ko": {"fix": "고치는 법", "none": "lint 규칙이 없습니다. harness.json의 lint.rules에 규칙을 추가하세요.",
           "pass": "lint PASS: 규칙 {r}개, 파일 {f}개, 위반 0건",
           "fail": "lint FAIL: 위반 {n}건. 위 안내대로 구현 코드를 고치세요. 규칙을 지우거나 끄지 마세요."},
    "en": {"fix": "fix", "none": "No lint rules. Add rules to lint.rules in harness.json.",
           "pass": "lint PASS: {r} rules, {f} files, 0 violations",
           "fail": "lint FAIL: {n} violations. Fix the implementation as described above. Do not remove or disable rules."},
}


def compile_rules(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for r in cfg.get("lint", {}).get("rules", []):
        out.append({
            "id": r["id"],
            "files": hl.PathRules(r["files"]),
            "exclude": hl.PathRules(r.get("exclude", [])) if r.get("exclude") else None,
            "forbid": re.compile(r["forbid"], re.MULTILINE),
            "why": r.get("why", ""),
            "fix": r.get("fix", ""),
        })
    return out


def walk(root: str) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in hl.SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            path = os.path.join(dirpath, name)
            yield os.path.relpath(path, root).replace(os.sep, "/")


def line_col(text: str, pos: int) -> Tuple[int, int]:
    line = text.count("\n", 0, pos) + 1
    col = pos - (text.rfind("\n", 0, pos) + 1) + 1
    return line, col


def run_lint(root: str, cfg: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int, int]:
    rules = compile_rules(cfg)
    found: List[Dict[str, Any]] = []
    files = 0
    if not rules:
        return found, 0, 0
    for rel in walk(root):
        hits = [r for r in rules if r["files"].match(rel) and not (r["exclude"] and r["exclude"].match(rel))]
        if not hits:
            continue
        path = os.path.join(root, rel)
        try:
            if os.path.getsize(path) > MAX_BYTES:
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        files += 1
        lines = text.split("\n")
        for r in hits:
            for m in r["forbid"].finditer(text):
                ln, col = line_col(text, m.start())
                src = lines[ln - 1] if ln - 1 < len(lines) else ""
                ig = IGNORE.search(src)
                if ig and r["id"] in [s.strip() for s in ig.group(1).split(",")]:
                    continue
                found.append({"file": rel, "line": ln, "col": col, "rule": r["id"], "why": r["why"],
                              "fix": r["fix"], "src": src.strip()[:160]})
    return found, len(rules), files


def key(v: Dict[str, Any]) -> str:
    return f"{v['rule']}|{v['file']}|" + hashlib.sha256(v["src"].encode()).hexdigest()[:12]


def baseline_path() -> str:
    return os.path.join(hl.HARNESS_DIR, BASELINE)


def load_baseline() -> Dict[str, int]:
    try:
        with open(baseline_path(), encoding="utf-8") as f:
            data = json.load(f)
        return {k: int(v) for k, v in data.get("known", {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv or []
    root = hl.project_root()
    cfg = hl.load_config()
    t = TEXT.get(cfg.get("lang", "ko"), TEXT["ko"])
    found, nrules, nfiles = run_lint(root, cfg)
    if not nrules:
        print(t["none"])
        return 0
    if "--baseline" in argv:
        known: Dict[str, int] = {}
        for v in found:
            known[key(v)] = known.get(key(v), 0) + 1
        with open(baseline_path(), "w", encoding="utf-8") as f:
            json.dump({"note": "known violations; regenerate with harness.py lint --baseline", "known": known}, f, indent=1)
        print(f"baseline: {len(found)} known violations recorded in .codex/harness/{BASELINE}")
        return 0
    budget = load_baseline()
    new, old = [], 0
    for v in found:
        k = key(v)
        if budget.get(k, 0) > 0:
            budget[k] -= 1
            old += 1
        else:
            new.append(v)
    if old:
        print(f"({old} known violations from the baseline are not counted)")
    found = new
    for v in found:
        print(f"{v['file']}:{v['line']}:{v['col']} [{v['rule']}] {v['why']}")
        print(f"    {v['src']}")
        if v["fix"]:
            print(f"    {t['fix']}: {v['fix']}")
    if found:
        print(t["fail"].format(n=len(found)))
        return 1
    print(t["pass"].format(r=nrules, f=nfiles))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except hl.ConfigError as e:
        print("config error:", e, file=sys.stderr)
        sys.exit(2)
