#!/usr/bin/env python3
"""Install codex-harness-kit into a project.

  python3 install.py /path/to/project                  install or upgrade (keeps your harness.json)
  python3 install.py /path/to/project --dry-run        show what would change
  python3 install.py /path/to/project --preset python  pick a preset instead of auto-detecting
  python3 install.py /path/to/project --uninstall      remove hooks, scripts and the AGENTS.md block
  python3 install.py /path/to/project --uninstall --purge   also remove settings, logs and generated files

Standard library only, Python 3.9+. Never touches ~/.codex. Every input is checked before
anything is written, so a failed install leaves the project as it was.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

KIT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(KIT, "kit", ".codex", "harness")
TEMPLATES = os.path.join(KIT, "kit", "templates")
PRESETS = os.path.join(KIT, "presets")
SCRIPTS = ["harnesslib.py", "guard.py", "verify.py", "harness.py", "lint.py", "see.py"]
MARK_START = "<!-- codex-harness-kit:start -->"
MARK_END = "<!-- codex-harness-kit:end -->"
GITIGNORE_LINE = ".codex/harness/logs/"
VERSION = "0.2.1"

sys.path.insert(0, SRC)
import harnesslib as hl  # noqa: E402


class InstallError(Exception):
    pass


class Plan:
    """Collects file operations; nothing touches disk until apply()."""

    def __init__(self) -> None:
        self.ops: List[Tuple[str, str, Any, str]] = []
        self.notes: List[str] = []

    def write(self, path: str, text: str, why: str) -> None:
        if read(path) != text:
            self.ops.append(("write", path, text, why))

    def copy(self, src: str, dst: str, why: str = "") -> None:
        if read(src) != read(dst):
            self.ops.append(("copy", dst, src, why))

    def remove(self, path: str, why: str = "") -> None:
        if os.path.lexists(path):
            self.ops.append(("remove", path, None, why))

    def note(self, text: str) -> None:
        self.notes.append(text)

    def apply(self) -> None:
        for op, path, payload, _ in self.ops:
            if op == "write":
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(payload)
            elif op == "copy":
                os.makedirs(os.path.dirname(path), exist_ok=True)
                shutil.copyfile(payload, path)
            elif op == "remove":
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)

    def show(self, target: str, dry: bool) -> None:
        print("DRY RUN, nothing written:" if dry else "done:")
        for op, path, _, why in self.ops:
            rel = os.path.relpath(path, target)
            print(f"  {op:<6} {rel}" + (f"  ({why})" if why else ""))
        if not self.ops:
            print("  nothing to change")
        for n in self.notes:
            print(f"  note   {n}")


def read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError):
        return None


def load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------- detection


def detect_preset(target: str) -> str:
    if os.path.exists(os.path.join(target, "package.json")):
        return "node"
    if any(os.path.exists(os.path.join(target, f)) for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        return "python"
    if os.path.exists(os.path.join(target, "go.mod")):
        return "go"
    return "generic"


def node_runner(target: str) -> str:
    for lock, pm in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun")):
        if os.path.exists(os.path.join(target, lock)):
            return pm
    return "npm"


def detect_verify(target: str, preset: str) -> List[Dict[str, Any]]:
    cmds: List[Dict[str, Any]] = []
    if preset == "node":
        try:
            scripts = load_json(os.path.join(target, "package.json")).get("scripts", {}) or {}
        except (OSError, ValueError, AttributeError):
            scripts = {}
        pm = node_runner(target)
        run = (lambda s: f"npm run {s} --silent") if pm == "npm" else (lambda s: f"{pm} run {s}")
        test = scripts.get("test", "")
        try:
            pkg = load_json(os.path.join(target, "package.json"))
            deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        except (OSError, ValueError, AttributeError):
            deps = {}
        names = os.listdir(target)
        if test and "no test specified" not in test:
            cmds.append({"name": "test", "run": "npm test --silent" if pm == "npm" else f"{pm} test", "timeout": 300})
        elif "vitest" in deps or any(n.startswith("vitest.config.") for n in names):
            cmds.append({"name": "test", "run": "npx --no-install vitest run", "timeout": 300})
        elif "jest" in deps or any(n.startswith("jest.config.") for n in names):
            cmds.append({"name": "test", "run": "npx --no-install jest", "timeout": 300})
        for key in ("typecheck", "type-check", "check-types", "tsc"):
            if key in scripts:
                cmds.append({"name": "typecheck", "run": run(key), "timeout": 300})
                break
        else:
            if os.path.exists(os.path.join(target, "tsconfig.json")):
                # --no-install: never download an unrelated `tsc` package when typescript is missing
                cmds.append({"name": "typecheck", "run": "npx --no-install tsc --noEmit", "timeout": 300})
        if "lint" in scripts:
            cmds.append({"name": "lint", "run": run("lint"), "timeout": 300})
    elif preset == "python":
        text = (read(os.path.join(target, "pyproject.toml")) or "") + (read(os.path.join(target, "requirements.txt")) or "")
        if "pytest" in text or any(os.path.exists(os.path.join(target, f)) for f in ("pytest.ini", "conftest.py")):
            cmds.append({"name": "test", "run": "python3 -m pytest -q", "timeout": 300})
        elif os.path.isdir(os.path.join(target, "tests")):
            cmds.append({"name": "test", "run": "python3 -m unittest discover -s tests -q", "timeout": 300})
    elif preset == "go":
        cmds.append({"name": "test", "run": "go test ./...", "timeout": 300})
        cmds.append({"name": "vet", "run": "go vet ./...", "timeout": 300})
    return cmds


def merge_preset(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(base))
    for section in ("protect", "deny_read"):
        for key, val in extra.get(section, {}).items():
            if isinstance(val, list):
                out.setdefault(section, {}).setdefault(key, [])
                out[section][key] = list(dict.fromkeys(val + out[section][key]))
            else:
                out[section][key] = val
    for kind in ("block", "allow"):
        out["examples"][kind] = extra.get("examples", {}).get(kind, []) + out["examples"][kind]
    return out


def config_text(cfg: Dict[str, Any]) -> str:
    """harness.json text in which example secrets are \\u-escaped.

    The values stay the same after json.load, but the raw file no longer looks like a key,
    so GitHub push protection or gitleaks do not block the user's commits.
    """
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    for sp in cfg.get("secrets", {}).get("patterns", []):
        try:
            rx = re.compile(sp["regex"])
        except (re.error, KeyError, TypeError):
            continue
        def hide(m: "re.Match[str]") -> str:
            v = m.group(0)
            k = next((i for sep in "_ -" for i, ch in enumerate(v) if ch == sep and i > 0), 1)
            return v[:k] + "\\u%04x" % ord(v[k]) + v[k + 1:]
        text = rx.sub(hide, text)
    return text


def build_config(target: str, preset: str, lang: str, verify: Optional[List[str]]) -> Dict[str, Any]:
    cfg = merge_preset(load_json(os.path.join(PRESETS, "base.json")), load_json(os.path.join(PRESETS, f"{preset}.json")))
    cfg["lang"] = lang
    if preset == "node":
        # Vitest reads its `test` block from vite.config.* when there is no vitest.config.*.
        names = os.listdir(target)
        pkg = read(os.path.join(target, "package.json")) or ""
        if "vitest" in pkg and not any(n.startswith("vitest.config.") for n in names) and any(n.startswith("vite.config.") for n in names):
            cfg["protect"]["paths"].append("vite.config.*")
    if verify:
        cfg["verify"]["commands"] = [{"name": v.split()[0], "run": v, "timeout": 300} for v in verify]
    else:
        cfg["verify"]["commands"] = detect_verify(target, preset)
    return cfg


# ------------------------------------------------------------- hooks.json


def locate(script: str) -> str:
    """Shell snippet: walk up from the session folder to the project that holds the harness."""
    return ('d="$PWD"; while [ ! -f "$d/.codex/harness/{s}" ] && [ "$d" != / ]; do d=$(dirname "$d"); done; '
            'if [ -f "$d/.codex/harness/{s}" ]; then exec python3 "$d/.codex/harness/{s}"{args}; fi').format(s=script, args="{args}")


def hook_entries(lang: str, stop_timeout: int) -> Dict[str, Any]:
    msgs = {"ko": ("하네스: 보호 파일·비밀 값 검사", "하네스: 끝내기 전 검증"),
            "en": ("harness: protected files and secrets", "harness: verify before finishing")}[lang]
    guard = locate("guard.py").format(args="") + "; echo '{}'"
    stop = locate("verify.py").format(args="") + "; echo '{}'"
    snap = locate("verify.py").format(args=" --snapshot")
    return {
        "UserPromptSubmit": {"hooks": [{"type": "command", "command": snap,
                                        "commandWindows": "py -3 .codex\\harness\\verify.py --snapshot", "timeout": 30}]},
        "PreToolUse": {"matcher": "^(Bash|apply_patch)$",
                       "hooks": [{"type": "command", "command": guard, "commandWindows": "py -3 .codex\\harness\\guard.py",
                                  "timeout": 15, "statusMessage": msgs[0]}]},
        "Stop": {"hooks": [{"type": "command", "command": stop, "commandWindows": "py -3 .codex\\harness\\verify.py",
                            "timeout": stop_timeout, "statusMessage": msgs[1]}]},
    }


def is_ours(group: Dict[str, Any]) -> bool:
    return any(".codex/harness/" in str(h.get("command", "")) or ".codex\\harness\\" in str(h.get("commandWindows", ""))
               for h in group.get("hooks", []) if isinstance(h, dict))


def check_hooks_shape(data: Any, path: str) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise InstallError(f"{path}: expected an object with a \"hooks\" object")
    for event, groups in data.get("hooks", {}).items():
        if not isinstance(groups, list) or not all(isinstance(g, dict) and isinstance(g.get("hooks", []), list) for g in groups):
            raise InstallError(f"{path}: hooks.{event} must be a list of {{\"matcher\", \"hooks\": [...]}} objects")


def merged_hooks(existing: Optional[Dict[str, Any]], lang: str, stop_timeout: int, remove_only: bool) -> Dict[str, Any]:
    data = json.loads(json.dumps(existing)) if existing else {}
    if not remove_only:
        data.setdefault("description", "Project hooks. The codex-harness-kit entries call .codex/harness/*.py.")
    hooks = data.setdefault("hooks", {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not is_ours(g)]
        if not hooks[event]:
            del hooks[event]
    if not remove_only:
        for event, group in hook_entries(lang, stop_timeout).items():
            hooks.setdefault(event, []).append(group)
    return data


# ------------------------------------------------------------- text files


def agents_block(cfg: Dict[str, Any], lang: str) -> str:
    tpl = read(os.path.join(TEMPLATES, f"AGENTS.harness.{lang}.md")) or ""
    cmds = "\n".join(f"  - `{c['run']}`" for c in cfg["verify"]["commands"]) or "  - (none configured)"
    paths = ", ".join(f"`{p}`" for p in cfg["protect"]["paths"])
    body = tpl.format(verify_commands=cmds, protected=paths).strip()
    extra = []
    if cfg.get("lint", {}).get("rules") or any("harness.py lint" in c["run"] for c in cfg["verify"]["commands"]):
        extra.append(EXTRA[lang]["lint"])
    if cfg.get("see", {}).get("url"):
        extra.append(EXTRA[lang]["see"].format(out=cfg["see"].get("out", "artifacts/harness/see")))
    if extra:
        body += "\n" + "\n".join(extra)
    return f"{MARK_START}\n" + body + f"\n{MARK_END}\n"


EXTRA = {
    "ko": {
        "lint": "- 프로젝트 규칙은 `python3 .codex/harness/harness.py lint`로 확인한다. 위반 메시지의 `고치는 법`대로 구현을 고치고, 규칙을 지우거나 끄지 않는다. 같은 실수가 반복되면 새 규칙을 제안한다.",
        "see": "- 화면을 바꾸면 `python3 .codex/harness/harness.py see`로 앱을 띄워 스크린샷과 콘솔 오류를 모은다. `{out}/`의 스크린샷을 직접 열어 보고, 깨지거나 겹치거나 비어 보이면 고친 뒤 다시 확인한다. 샌드박스 때문에 직접 띄우지 못해도 BLOCKED가 아니다. 응답을 마치기 직전 Stop 훅이 샌드박스 밖에서 화면 확인을 대신 실행한다.",
    },
    "en": {
        "lint": "- Check project rules with `python3 .codex/harness/harness.py lint`. Fix the implementation as each violation's `fix` says; do not remove or disable rules. If the same mistake keeps happening, propose a new rule.",
        "see": "- After changing the UI, run `python3 .codex/harness/harness.py see` to start the app, take screenshots and collect console errors. Open the screenshots in `{out}/` yourself; if anything is broken, overlapping or empty, fix it and check again. If the sandbox stops you from starting the app, that is not BLOCKED: the Stop hook runs the screen check outside the sandbox right before you finish.",
    },
}


def replace_block(text: str, block: Optional[str]) -> str:
    """Insert, replace or (block=None) remove the managed AGENTS.md block."""
    pattern = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?", re.S)
    if pattern.search(text):
        out = pattern.sub(lambda _m: block or "", text, count=1)
        return re.sub(r"\n{3,}", "\n\n", out).lstrip("\n") if block is None else out
    if block is None:
        return text
    sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    return text + sep + block


# ------------------------------------------------------------- main


def git_info(target: str) -> Optional[str]:
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=target, capture_output=True, text=True)
    return os.path.realpath(p.stdout.strip()) if p.returncode == 0 else None


def plan_uninstall(target: str, purge: bool, plan: Plan) -> None:
    harness = os.path.join(target, ".codex", "harness")
    hooks_path = os.path.join(target, ".codex", "hooks.json")
    if os.path.exists(hooks_path):
        existing = load_json(hooks_path)
        check_hooks_shape(existing, hooks_path)
        data = merged_hooks(existing, "en", 0, remove_only=True)
        if not data["hooks"]:
            plan.remove(hooks_path, "only harness hooks were in it")
        else:
            plan.write(hooks_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", "harness hooks removed")
    agents = os.path.join(target, "AGENTS.md")
    text = read(agents)
    if text and MARK_START in text:
        rest = replace_block(text, None)
        if rest.strip():
            plan.write(agents, rest, "harness block removed")
        else:
            plan.remove(agents, "it only held the harness block")
    if purge:
        plan.remove(harness, "settings and logs")
        for lang in ("ko", "en"):
            if read(os.path.join(target, "docs", "harness.md")) == read(os.path.join(TEMPLATES, f"harness.{lang}.md")):
                plan.remove(os.path.join(target, "docs", "harness.md"), "unchanged template")
        gi = os.path.join(target, ".gitignore")
        gtext = read(gi)
        if gtext and GITIGNORE_LINE in gtext.splitlines():
            kept = [l for l in gtext.splitlines() if l != GITIGNORE_LINE]
            if any(l.strip() for l in kept):
                plan.write(gi, "\n".join(kept) + "\n", "harness line removed")
            else:
                plan.remove(gi, "it only held the harness line")
        baks = [n for n in os.listdir(os.path.join(target, ".codex"))] if os.path.isdir(os.path.join(target, ".codex")) else []
        baks = [n for n in baks if n.startswith("hooks.json.bak-")]
        if baks:
            plan.note(f"kept {len(baks)} hooks.json.bak-* backup(s) in .codex/; delete them when you no longer need them")
    else:
        for s in SCRIPTS:
            plan.remove(os.path.join(harness, s))
        plan.note("kept .codex/harness/harness.json and logs (add --purge to remove them)")


def plan_install(target: str, args, plan: Plan) -> None:
    harness = os.path.join(target, ".codex", "harness")
    cfg_path = os.path.join(harness, "harness.json")
    hooks_path = os.path.join(target, ".codex", "hooks.json")

    # 1. read and validate everything first
    existing_hooks = None
    if os.path.exists(hooks_path):
        try:
            existing_hooks = load_json(hooks_path)
        except ValueError as e:
            raise InstallError(f"{hooks_path} is not valid JSON ({e}). Fix or move it, then run again.")
        check_hooks_shape(existing_hooks, hooks_path)
    if os.path.exists(cfg_path) and not args.force:
        try:
            cfg = hl._merge(hl.DEFAULTS, load_json(cfg_path))
        except ValueError as e:
            raise InstallError(f"{cfg_path} is not valid JSON ({e}). Fix it, or pass --force to regenerate it.")
        errs = hl.validate(cfg)
        if errs:
            raise InstallError(f"{cfg_path}: " + "; ".join(errs) + ". Fix it, or pass --force to regenerate it.")
        plan.note("kept your .codex/harness/harness.json (use --force to regenerate it from the preset)")
        cfg_text = None
    else:
        preset = args.preset if args.preset != "auto" else detect_preset(target)
        cfg = build_config(target, preset, args.lang, args.verify)
        cfg_text = config_text(cfg)
        plan.note(f"preset: {preset}")
    lang = cfg.get("lang", args.lang)
    if apply_options(cfg, args):
        if cfg_text is None:
            plan.note("updated .codex/harness/harness.json with --lint / --see options")
        cfg_text = config_text(cfg)

    # 2. plan the writes
    for s in SCRIPTS:
        plan.copy(os.path.join(SRC, s), os.path.join(harness, s), "hook scripts")
    if cfg_text is not None:
        plan.write(cfg_path, cfg_text, "settings")
    total = int(cfg["verify"].get("total_timeout", 570))
    stop_timeout = min(600, total + 30)
    data = merged_hooks(existing_hooks, lang, stop_timeout, remove_only=False)
    new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if existing_hooks is not None and read(hooks_path) != new_text and any(not is_ours(g) for gs in existing_hooks.get("hooks", {}).values() for g in gs):
        plan.copy(hooks_path, f"{hooks_path}.bak-{time.strftime('%Y%m%d%H%M%S')}", "backup of your hooks")
    plan.write(hooks_path, new_text, "UserPromptSubmit snapshot, PreToolUse guard, Stop verify")
    if not args.no_agents:
        agents = os.path.join(target, "AGENTS.md")
        plan.write(agents, replace_block(read(agents) or "", agents_block(cfg, lang)), "harness rules block")
    doc = os.path.join(target, "docs", "harness.md")
    if not os.path.exists(doc):
        plan.write(doc, read(os.path.join(TEMPLATES, f"harness.{lang}.md")) or "", "how this harness works")
    gi = os.path.join(target, ".gitignore")
    gtext = read(gi) or ""
    want = [GITIGNORE_LINE]
    if cfg.get("see", {}).get("url"):
        want.append(cfg["see"].get("out", "artifacts/harness/see").rstrip("/") + "/")
    missing = [w for w in want if w not in gtext.splitlines()]
    if missing:
        plan.write(gi, gtext + ("" if not gtext or gtext.endswith("\n") else "\n") + "\n".join(missing) + "\n", "ignore hook logs and screenshots")

    # 3. tell the user what was decided
    for c in cfg["verify"]["commands"]:
        plan.note(f"verify: {c['run']}")
    if not cfg["verify"]["commands"]:
        plan.note("no verification command found: add one to verify.commands or pass --verify \"<command>\"")
    plan.note(f"protect: {', '.join(cfg['protect']['paths'])}")
    plan.note(f"Stop hook timeout {stop_timeout}s, verification budget {total}s")
    toml = read(os.path.join(target, ".codex", "config.toml")) or ""
    if re.search(r"^\s*\[\[?hooks", toml, re.M):
        plan.note(".codex/config.toml also defines [hooks]; Codex merges both and warns. Prefer one place.")
    if re.search(r"^\s*(codex_)?hooks\s*=\s*false", toml, re.M):
        plan.note(".codex/config.toml sets hooks = false, so no hook will run")


LINT_CMD = "python3 .codex/harness/harness.py lint"
SEE_CMD = "python3 .codex/harness/harness.py see"


def apply_options(cfg: Dict[str, Any], args) -> bool:
    """Apply --lint / --see-url / --see-start. Returns True when cfg changed."""
    changed = False
    if getattr(args, "lint", False) and not any(c["run"] == LINT_CMD for c in cfg["verify"]["commands"]):
        cfg["verify"]["commands"].append({"name": "lint", "run": LINT_CMD, "timeout": 120})
        changed = True
    see = cfg.setdefault("see", dict(hl.DEFAULTS["see"]))
    if getattr(args, "see_verify", False) and not any(c["run"] == SEE_CMD for c in cfg["verify"]["commands"]):
        cfg["verify"]["commands"].append({"name": "see", "run": SEE_CMD, "timeout": 240})
        changed = True
    if getattr(args, "see_url", None):
        see["url"] = args.see_url
        changed = True
    if getattr(args, "see_start", None):
        see["start"] = args.see_start
        changed = True
    if SEE_CMD in [c["run"] for c in cfg["verify"]["commands"]] and not see.get("url"):
        raise InstallError("--see-verify needs --see-url")
    if changed:
        errs = hl.validate(hl._merge(hl.DEFAULTS, cfg))
        if errs:
            raise InstallError("; ".join(errs))
    return changed


def run(args) -> int:
    target = os.path.realpath(args.target)
    if not os.path.isdir(target):
        raise InstallError(f"not a folder: {target}")
    if args.purge and not args.uninstall:
        raise InstallError("--purge only works together with --uninstall")
    if args.verify is not None and not all(v.strip() for v in args.verify):
        raise InstallError("--verify needs a command, for example --verify \"npm test\"")
    plan = Plan()
    root = git_info(target)
    if root is None:
        plan.note("not a git repository: run `git init` here; without git, unchanged turns are verified again every time")
    elif root != target:
        plan.note(f"git root is {root}. Codex reads project hooks from the repository root, so install there unless you always start Codex in this folder")
    if args.uninstall:
        plan_uninstall(target, args.purge, plan)
    else:
        plan_install(target, args, plan)
    if not args.dry_run:
        plan.apply()
        cleanup_empty(target)
    plan.show(target, args.dry_run)
    if not args.dry_run and not args.uninstall:
        changed_hooks = any(op[1].endswith("hooks.json") for op in plan.ops)
        print("\nnext:\n  1. cd " + target + "\n  2. python3 .codex/harness/harness.py doctor\n  3. python3 .codex/harness/harness.py test")
        if changed_hooks:
            print("  4. trust the hooks: CLI `/hooks`, or app Settings > Hooks > this project > Trust all\n"
                  "     (Codex silently skips new or changed project hooks until they are trusted)")
    return 0


def cleanup_empty(target: str) -> None:
    codex = os.path.join(target, ".codex")
    if os.path.isdir(codex) and not os.listdir(codex):
        os.rmdir(codex)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="install.py", description=f"codex-harness-kit {VERSION} installer")
    p.add_argument("target")
    p.add_argument("--preset", default="auto", choices=["auto", "node", "python", "go", "generic"])
    p.add_argument("--lang", default="ko", choices=["ko", "en"])
    p.add_argument("--verify", action="append", help="verification command (repeatable); overrides detection")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="regenerate harness.json from the preset")
    p.add_argument("--no-agents", action="store_true", help="do not touch AGENTS.md")
    p.add_argument("--lint", action="store_true", help="run project rules (lint.rules) as part of verification")
    p.add_argument("--see-url", help="base URL of the app for the screen check, e.g. http://127.0.0.1:5173")
    p.add_argument("--see-start", help="command that starts the app when the URL is not up, e.g. \"npm run dev\"")
    p.add_argument("--see-verify", action="store_true", help="also run the screen check before Codex finishes (needs --see-url)")
    p.add_argument("--uninstall", action="store_true")
    p.add_argument("--purge", action="store_true", help="with --uninstall, also delete settings, logs and generated files")
    args = p.parse_args(argv)
    try:
        return run(args)
    except (InstallError, OSError) as e:
        print(f"install.py: {e}\nnothing was changed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
