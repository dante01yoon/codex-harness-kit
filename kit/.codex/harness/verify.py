#!/usr/bin/env python3
"""Stop hook: check the turn before Codex ends it.

  verify.py              Stop hook
  verify.py --snapshot   UserPromptSubmit hook: remember protected files at the start of a turn

At Stop:
1. If a protected file changed during the turn (through a path the guard cannot see, such as
   a script or an MCP tool), Codex is told to stop and report it.
2. Otherwise the verification commands run. On failure Codex keeps working with the output
   as its next prompt, up to `verify.max_continuations_per_turn` times, unless it already
   handed off with BLOCKED. A missing command or module is reported as an environment
   problem instead of being sent back as a code problem.
3. Turns that changed nothing since the last pass are skipped (needs git).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harnesslib as hl  # noqa: E402

STATE_NAME = os.path.join("logs", "verify-state.json")
BLOCKED_LINE = re.compile(r"(?m)^[\s*`>_#\-\[]*BLOCKED\b")
ENV_ERROR = re.compile(r"command not found|No module named|is not recognized as an internal or external command")


def state_path() -> str:
    return os.path.join(hl.HARNESS_DIR, STATE_NAME)


def load_state() -> Dict[str, Any]:
    try:
        with open(state_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(state_path()), exist_ok=True)
        tmp = state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, state_path())
    except OSError:
        pass


def fingerprint(root: str, cfg: Dict[str, Any]) -> Optional[str]:
    """Hash of HEAD plus every changed or untracked file (path, size, mtime). None without git."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10)
        files = subprocess.run(["git", "ls-files", "-m", "-o", "-d", "--exclude-standard", "-z"],
                               cwd=root, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if files.returncode != 0:
        return None
    h = hashlib.sha256(head.stdout.strip().encode())
    for rel in sorted(set(files.stdout.split("\0"))):
        if not rel or rel.startswith(".codex/harness/logs/"):
            continue
        try:
            st = os.stat(os.path.join(root, rel))
            h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
        except OSError:
            h.update(f"{rel}\0deleted\n".encode())
    h.update(json.dumps(cfg["verify"]["commands"], sort_keys=True).encode())
    return h.hexdigest()


def tail(text: str, n: int) -> str:
    lines = text.rstrip().splitlines()
    if len(lines) <= n:
        return "\n".join(lines)
    return f"... ({len(lines) - n} lines omitted)\n" + "\n".join(lines[-n:])


def run_commands(cfg: Dict[str, Any], root: str) -> Tuple[bool, List[Dict[str, Any]]]:
    """Run verify.commands in order within verify.total_timeout seconds overall."""
    results = []
    ok = True
    env = dict(os.environ, CI=os.environ.get("CI", "1"), FORCE_COLOR="0", NO_COLOR="1")
    deadline = time.time() + int(cfg["verify"].get("total_timeout", 570))
    for c in cfg["verify"]["commands"]:
        name = c.get("name") or c["run"]
        started = time.time()
        budget = int(min(int(c.get("timeout", 300)), max(1, deadline - started)))
        cwd = os.path.join(root, c.get("cwd", "."))
        try:
            if not os.path.isdir(cwd):
                raise OSError(f"cwd does not exist: {c.get('cwd')}")
            proc = subprocess.run(c["run"], shell=True, cwd=cwd, capture_output=True, text=True, timeout=budget, env=env)
            code, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as e:
            raw = e.stdout or b""
            code = 124
            output = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw) + f"\n[timed out after {budget}s]"
        except OSError as e:
            code, output = 127, str(e)
        environment = code == 127 or (code != 0 and ENV_ERROR.search(output[-4000:]) is not None)
        results.append({"name": name, "run": c["run"], "code": code, "seconds": round(time.time() - started, 2),
                        "output": output, "environment": environment})
        if code != 0:
            ok = False
            if c.get("stop_on_failure", True):
                break
    return ok, results


def details(results: List[Dict[str, Any]], cfg: Dict[str, Any], secrets) -> str:
    n = int(cfg["verify"].get("output_tail_lines", 40))
    parts = []
    for r in results:
        status = "PASS" if r["code"] == 0 else f"FAIL (exit {r['code']})"
        parts.append(f"$ {r['run']}  -> {status}, {r['seconds']}s")
        if r["code"] != 0:
            body = tail(r["output"], n)
            for sid, rx in secrets:
                body = rx.sub(f"[REDACTED:{sid}]", body)
            parts.append(body)
    return "\n".join(parts)


def snapshot(cfg: Dict[str, Any], root: str) -> None:
    state = load_state()
    if state.get("continuations", 0) > 0:
        return  # a Stop continuation is in progress: keep the snapshot from the real start of the turn
    state["snapshot"] = hl.protected_snapshot(root, hl.PathRules(cfg["protect"]["paths"]))
    save_state(state)


def finish(state: Dict[str, Any], **update: Any) -> None:
    state.update(update)
    save_state(state)


def stop(cfg: Dict[str, Any], root: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    state = load_state()
    turn = str(payload.get("turn_id") or payload.get("session_id") or "")
    continued = bool(payload.get("stop_hook_active"))
    used = int(state.get("continuations", 0)) if continued else 0

    if cfg["verify"].get("check_protected_changes", True) and isinstance(state.get("snapshot"), dict):
        now = hl.protected_snapshot(root, hl.PathRules(cfg["protect"]["paths"]))
        changed = hl.changed_paths(state["snapshot"], now)
        if changed:
            files = ", ".join(changed[:10]) + (" ..." if len(changed) > 10 else "")
            hl.log_event(cfg, root, {"hook": "verify", "turn": turn, "decision": "protected_changed", "files": changed[:50]})
            state["snapshot"] = now  # report each change once
            if cfg["mode"] == "observe" or continued:
                finish(state, continuations=0)
                return {"systemMessage": hl.msg(cfg, "protected_changed", files=files)}
            finish(state, continuations=1)
            return {"decision": "block", "reason": hl.msg(cfg, "protected_changed", files=files)}

    fp = fingerprint(root, cfg)
    if not cfg["verify"]["commands"] or (cfg["verify"].get("skip_when_unchanged", True) and fp and fp == state.get("last_pass")):
        finish(state, continuations=0)
        return {}

    ok, results = run_commands(cfg, root)
    summary = [{"name": r["name"], "code": r["code"], "seconds": r["seconds"]} for r in results]
    if ok:
        finish(state, last_pass=fingerprint(root, cfg) or fp, turn=turn, continuations=0)
        hl.log_event(cfg, root, {"hook": "verify", "turn": turn, "decision": "pass", "results": summary})
        return {}

    text = details(results, cfg, hl.compile_secrets(cfg))
    last = str(payload.get("last_assistant_message") or "")
    limit = int(cfg["verify"].get("max_continuations_per_turn", 2))
    if any(r["environment"] for r in results):
        decision, warn = "environment", hl.msg(cfg, "verify_env")
    elif cfg["mode"] == "observe":
        decision, warn = "would_block", hl.msg(cfg, "verify_observe")
    elif cfg["verify"].get("respect_blocked", True) and BLOCKED_LINE.search(last):
        decision, warn = "handed_off", hl.msg(cfg, "verify_handed_off")
    elif used >= limit:
        decision, warn = "give_up", hl.msg(cfg, "verify_giveup", n=used + 1)
    else:
        finish(state, turn=turn, continuations=used + 1)
        hl.log_event(cfg, root, {"hook": "verify", "turn": turn, "decision": "block", "attempt": used + 1, "results": summary})
        return {"decision": "block", "reason": hl.msg(cfg, "verify_fail", details=text)}
    finish(state, turn=turn, continuations=0)
    hl.log_event(cfg, root, {"hook": "verify", "turn": turn, "decision": decision, "results": summary})
    return {"systemMessage": warn + "\n" + text[:1500]}


def main(argv: List[str]) -> int:
    root = hl.project_root()
    cfg: Dict[str, Any] = dict(hl.DEFAULTS)
    snapshot_mode = "--snapshot" in argv
    try:
        cfg = hl.load_config()
        payload = hl.read_payload()
        if snapshot_mode:
            snapshot(cfg, root)  # UserPromptSubmit: no output means "continue"
            return 0
        hl.emit(stop(cfg, root, payload))
    except Exception as e:  # never leave Codex without valid JSON
        err = f"{type(e).__name__}: {e}"[:300]
        hl.log_event(cfg, root, {"hook": "verify", "decision": "error", "error": err})
        if not snapshot_mode:
            hl.emit({"systemMessage": f"codex harness verify hook error: {err}"})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
