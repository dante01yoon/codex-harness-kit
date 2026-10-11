"""Shared helpers for the Codex harness hooks.

Standard library only, Python 3.9+. Hooks import this module from the same
directory, so it must not depend on anything outside `.codex/harness/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_NAME = "harness.json"
SCHEMA_VERSION = 1


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- paths


def git_root(start: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def project_root() -> str:
    """The project root is the folder that contains `.codex/harness/`."""
    return os.path.dirname(os.path.dirname(HARNESS_DIR))


CASE_INSENSITIVE_FS = sys.platform == "darwin" or os.name == "nt"


def resolve(path: str, base: str) -> str:
    """Absolute path with `..` folded and the parent folder's symlinks resolved.

    The last component is kept as written so a symlink named like a protected file
    is still judged by its own name.
    """
    path = os.path.expanduser(path.replace("\\", "/") if os.sep == "/" else path)
    full = os.path.normpath(path if os.path.isabs(path) else os.path.join(base, path))
    parent, name = os.path.split(full)
    return os.path.join(os.path.realpath(parent), name) if name else os.path.realpath(full)


def to_rel(path: str, base: str, root: str) -> Optional[str]:
    """Resolve `path` (relative to `base`) and return it relative to `root`.

    Returns None when the path points outside the project.
    """
    full = resolve(path, base)
    root = os.path.realpath(root)
    if full == root:
        return "."
    if not full.startswith(root + os.sep):
        return None
    return os.path.relpath(full, root).replace(os.sep, "/")


# ---------------------------------------------------------------- globs


def glob_to_regex(pattern: str, ignore_case: bool = False) -> "re.Pattern[str]":
    """Translate a gitignore-like glob into a regex over POSIX relative paths.

    - `dir/`      matches the directory and everything under it
    - `**/x`      matches x at any depth
    - `*`, `?`    never cross `/`
    - a pattern without `/` matches the basename at any depth
    """
    p = pattern.strip()
    anchored = "/" in p.rstrip("/")
    if p.startswith("/"):
        p = p[1:]
    p = p.rstrip("/")
    i, out = 0, []
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = p.find("]", i)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                out.append("[" + p[i + 1:j].replace("\\", "\\\\") + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    body = "".join(out)
    prefix = "" if anchored else "(?:.*/)?"
    # A match also covers everything below it, so `tests` protects `tests/a.py`.
    return re.compile("^" + prefix + body + "(?:/.*)?$", re.I if ignore_case else 0)


class PathRules:
    """An ordered glob list. Later rules win; `!pattern` re-includes a path."""

    def __init__(self, patterns: Iterable[str], ignore_case: bool = CASE_INSENSITIVE_FS):
        self.patterns = [p for p in patterns if p and p.strip()]
        self._compiled: List[Tuple[bool, "re.Pattern[str]", str]] = []
        for p in self.patterns:
            neg = p.startswith("!")
            raw = p[1:] if neg else p
            self._compiled.append((neg, glob_to_regex(raw, ignore_case), raw))

    def match(self, rel: str) -> Optional[str]:
        """Return the matching pattern, or None if the path is not covered."""
        while rel.startswith("./"):
            rel = rel[2:]
        hit: Optional[str] = None
        for neg, rx, raw in self._compiled:
            if rx.match(rel):
                hit = None if neg else raw
        return hit

    def literal_bases(self) -> List[str]:
        """Directory prefixes before the first wildcard, for ancestor checks."""
        bases = []
        for neg, _, raw in self._compiled:
            if neg:
                continue
            head = re.split(r"[*?\[]", raw, 1)[0]
            if "/" not in raw.rstrip("/"):
                bases.append("")  # basename pattern: may live anywhere
                continue
            bases.append(head.rsplit("/", 1)[0] if "/" in head else "")
        return bases

    def covers_inside(self, rel_dir: str, root: str, limit: int = 20000) -> Optional[str]:
        """Does deleting/moving `rel_dir` touch a protected path?"""
        rel_dir = "" if rel_dir in (".", "") else rel_dir.rstrip("/")
        for base in self.literal_bases():
            if base and (base == rel_dir or base.startswith(rel_dir + "/") or not rel_dir):
                return base + "/"
        full = os.path.join(root, rel_dir)
        if not os.path.isdir(full):
            return None
        seen = 0
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".venv", "venv", "__pycache__")]
            for name in filenames:
                seen += 1
                if seen > limit:
                    return "(too many files to inspect)"
                rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
                hit = self.match(rel)
                if hit:
                    return hit
        return None


# ---------------------------------------------------------------- config


DEFAULTS: Dict[str, Any] = {
    "version": SCHEMA_VERSION,
    "lang": "ko",
    "mode": "enforce",
    "on_error": "block",
    "protect": {"paths": [], "allow_commands": []},
    "deny_read": {"paths": []},
    "secrets": {"patterns": []},
    "verify": {
        "commands": [],
        "skip_when_unchanged": True,
        "max_continuations_per_turn": 2,
        "output_tail_lines": 40,
        "respect_blocked": True,
        "total_timeout": 570,
        "check_protected_changes": True,
    },
    "log": {"enabled": True, "path": ".codex/harness/logs/events.jsonl", "max_bytes": 1000000},
    "examples": {"block": [], "allow": []},
}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def validate(cfg: Dict[str, Any]) -> List[str]:
    errs: List[str] = []

    def want(cond: bool, msg: str) -> None:
        if not cond:
            errs.append(msg)

    want(cfg.get("version") == SCHEMA_VERSION, f"version must be {SCHEMA_VERSION}")
    want(cfg.get("lang") in ("ko", "en"), "lang must be 'ko' or 'en'")
    want(cfg.get("mode") in ("enforce", "observe"), "mode must be 'enforce' or 'observe'")
    want(cfg.get("on_error") in ("block", "allow"), "on_error must be 'block' or 'allow'")
    for key in ("protect", "deny_read"):
        paths = cfg.get(key, {}).get("paths")
        want(isinstance(paths, list) and all(isinstance(p, str) for p in paths), f"{key}.paths must be a list of strings")
    flags = cfg.get("protect", {}).get("update_flags", [])
    want(isinstance(flags, list) and all(isinstance(p, str) for p in flags), "protect.update_flags must be a list of strings")
    allow = cfg.get("protect", {}).get("allow_commands")
    want(isinstance(allow, list) and all(isinstance(p, str) for p in allow), "protect.allow_commands must be a list of strings")
    pats = cfg.get("secrets", {}).get("patterns")
    if not isinstance(pats, list):
        errs.append("secrets.patterns must be a list")
    else:
        for i, sp in enumerate(pats):
            if not isinstance(sp, dict) or not isinstance(sp.get("id"), str) or not isinstance(sp.get("regex"), str):
                errs.append(f"secrets.patterns[{i}] needs string 'id' and 'regex'")
                continue
            try:
                re.compile(sp["regex"])
            except re.error as e:
                errs.append(f"secrets.patterns[{i}] ({sp['id']}): bad regex: {e}")
    v = cfg.get("verify", {})
    cmds = v.get("commands")
    if not isinstance(cmds, list):
        errs.append("verify.commands must be a list")
    else:
        for i, c in enumerate(cmds):
            if not isinstance(c, dict) or not isinstance(c.get("run"), str) or not c["run"].strip():
                errs.append(f"verify.commands[{i}] needs a non-empty string 'run'")
            elif "timeout" in c and not (isinstance(c["timeout"], int) and c["timeout"] > 0):
                errs.append(f"verify.commands[{i}].timeout must be a positive integer (seconds)")
    t = v.get("total_timeout")
    want(isinstance(t, int) and 10 <= t <= 3600, "verify.total_timeout must be 10..3600 seconds")
    m = v.get("max_continuations_per_turn")
    want(isinstance(m, int) and 0 <= m <= 10, "verify.max_continuations_per_turn must be 0..10")
    return errs


def load_config() -> Dict[str, Any]:
    path = os.path.join(HARNESS_DIR, CONFIG_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found")
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: invalid JSON ({e})")
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be an object")
    cfg = _merge(DEFAULTS, raw)
    errs = validate(cfg)
    if errs:
        raise ConfigError(f"{path}: " + "; ".join(errs))
    if os.environ.get("CODEX_HARNESS_MODE") in ("enforce", "observe"):
        cfg["mode"] = os.environ["CODEX_HARNESS_MODE"]
    return cfg


# ---------------------------------------------------------------- snapshots


SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".nox", "dist", "build", ".next", "target"}


def protected_snapshot(root: str, rules: "PathRules", limit: int = 50000) -> Dict[str, str]:
    """Content hashes of every existing file that matches the protect rules."""
    out: Dict[str, str] = {}
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir.startswith(".codex/harness/logs"):
            continue
        for name in filenames:
            seen += 1
            if seen > limit:
                return out
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if not rules.match(rel):
                continue
            try:
                with open(os.path.join(dirpath, name), "rb") as f:
                    out[rel] = hashlib.sha256(f.read()).hexdigest()[:16]
            except OSError:
                continue
    return out


def changed_paths(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


# ---------------------------------------------------------------- secrets


def compile_secrets(cfg: Dict[str, Any]) -> List[Tuple[str, "re.Pattern[str]"]]:
    return [(p["id"], re.compile(p["regex"])) for p in cfg["secrets"]["patterns"]]


def find_secrets(text: str, rules: List[Tuple[str, "re.Pattern[str]"]]) -> List[str]:
    return [sid for sid, rx in rules if rx.search(text)]


def redact(text: str, rules: List[Tuple[str, "re.Pattern[str]"]], limit: int = 240) -> str:
    for sid, rx in rules:
        text = rx.sub(f"[REDACTED:{sid}]", text)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------- io


def read_payload() -> Dict[str, Any]:
    data = sys.stdin.read()
    obj = json.loads(data)
    if not isinstance(obj, dict):
        raise ValueError("hook input is not a JSON object")
    return obj


def emit(obj: Optional[Dict[str, Any]]) -> None:
    sys.stdout.write(json.dumps(obj or {}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log_event(cfg: Dict[str, Any], root: str, event: Dict[str, Any]) -> None:
    if not cfg.get("log", {}).get("enabled", True):
        return
    path = os.path.join(root, cfg["log"]["path"])
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > int(cfg["log"].get("max_bytes", 1000000)):
            os.replace(path, path + ".1")
        event = dict(event, ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass  # logging must never break the hook


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


MESSAGES = {
    "ko": {
        "protect": "보호된 검증 기준 파일({target}, 규칙 {rule})을 바꾸는 작업이라 막았습니다. 테스트와 검증 설정은 사람이 정한 기준입니다. 구현 코드를 고치고, 기준을 바꿔야 한다면 멈추고 바꿀 파일과 이유를 사람에게 보고하세요.",
        "update_flag": "`{target}`의 `{rule}`는 스냅숏과 기대값을 실행 결과에 맞춰 다시 쓰는 옵션이라 막았습니다. 기대값은 사람이 정한 기준입니다. 이 옵션 없이 테스트를 실행하고, 기대값을 바꿔야 한다면 멈추고 사람에게 보고하세요.",
        "test_setting": "검증 명령이 쓰는 테스트 설정({target})을 바꾸는 작업이라 막았습니다. 테스트 스크립트와 테스트 설정은 사람이 정한 기준입니다. 바꿔야 한다면 멈추고 바꿀 내용과 이유를 사람에게 보고하세요.",
        "protect_dir": "{target}을(를) 지우거나 옮기면 보호된 파일({rule})까지 바뀌어서 막았습니다. 범위를 좁혀 구현 코드만 다루세요.",
        "deny_read": "{target}은(는) 읽으면 안 되는 비밀 파일(규칙 {rule})이라 막았습니다. 값이 필요하면 사람에게 요청하세요.",
        "secret": "실제 비밀 값으로 보이는 문자열({ids})이 들어 있어 막았습니다. 테스트용 값이나 환경 변수 이름을 쓰세요.",
        "unparsed": "보호된 경로({rule})가 들어간 명령을 해석할 수 없어 안전을 위해 막았습니다. 단순한 명령으로 나눠서 다시 시도하세요.",
        "error": "하네스 훅에서 오류가 나서 안전을 위해 막았습니다: {err}. 사람에게 `python3 .codex/harness/harness.py doctor` 실행을 요청하세요.",
        "verify_fail": "응답을 마치기 전에 검증을 실행했는데 실패했습니다. 아래 결과를 보고 구현 코드를 고친 뒤 다시 확인하세요. 테스트나 검증 설정은 고치지 마세요.\n\n{details}",
        "verify_giveup": "검증이 이번 턴에서 {n}번 연속 실패해 자동 반복을 멈췄습니다. 사람이 확인해야 합니다.",
        "verify_handed_off": "코덱스가 BLOCKED로 멈춰 사람에게 넘겼으므로 다시 일하게 하지 않았습니다. 검증은 아직 실패 중입니다.",
        "verify_env": "검증 명령을 실행할 수 없습니다(명령이나 모듈을 찾지 못함). 구현 문제가 아니라 환경 문제라 코덱스에게 다시 시키지 않았습니다. `python3 .codex/harness/harness.py verify`로 확인하세요.",
        "verify_observe": "검증이 실패했습니다(observe 모드라 막지 않음).",
        "protected_changed": "이번 턴에 보호된 파일이 바뀌었습니다: {files}. 훅이 보지 못한 경로(스크립트 실행, MCP 도구 등)로 바뀐 것입니다. 직접 되돌리려 하지 말고 BLOCKED로 멈춰, 어떤 명령이 이 파일을 바꿨는지 사람에게 보고하세요.",
    },
    "en": {
        "protect": "Blocked: this changes a protected verification file ({target}, rule {rule}). Tests and verification config are human-owned. Fix the implementation instead; if the criteria must change, stop and report which files and why.",
        "update_flag": "Blocked: `{rule}` makes `{target}` rewrite snapshots and expectations to match the current output. Expectations are human-owned. Run the tests without it; if an expectation must change, stop and report to a human.",
        "test_setting": "Blocked: this changes test settings the verification commands rely on ({target}). Test scripts and settings are human-owned. If they must change, stop and report what and why to a human.",
        "protect_dir": "Blocked: removing or moving {target} would also change protected files ({rule}). Narrow the change to implementation code.",
        "deny_read": "Blocked: {target} is a secret file (rule {rule}). Ask a human if you need a value from it.",
        "secret": "Blocked: the input contains what looks like a real secret ({ids}). Use a test value or an environment variable name.",
        "unparsed": "Blocked: could not parse a command that mentions a protected path ({rule}). Split it into simpler commands.",
        "error": "Blocked because the harness hook failed: {err}. Ask a human to run `python3 .codex/harness/harness.py doctor`.",
        "verify_fail": "Verification ran before finishing and failed. Fix the implementation using the output below, then verify again. Do not edit tests or verification config.\n\n{details}",
        "verify_giveup": "Verification failed {n} times in a row this turn, so automatic retries stopped. A human needs to look.",
        "verify_handed_off": "Codex stopped with BLOCKED and handed off to a human, so it was not sent back to work. Verification is still failing.",
        "verify_env": "A verification command could not run (command or module not found). That is an environment problem, not the implementation, so Codex was not sent back. Run `python3 .codex/harness/harness.py verify` to see it.",
        "verify_observe": "Verification failed (observe mode, not blocking).",
        "protected_changed": "Protected files changed during this turn: {files}. They changed through a path the guard cannot see (a script, an MCP tool, ...). Do not try to revert them yourself; stop with BLOCKED and report which command changed them.",
    },
}


def msg(cfg: Dict[str, Any], key: str, **kw: Any) -> str:
    lang = cfg.get("lang", "ko") if isinstance(cfg, dict) else "ko"
    return MESSAGES.get(lang, MESSAGES["ko"])[key].format(**kw)
