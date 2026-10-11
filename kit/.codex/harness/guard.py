#!/usr/bin/env python3
"""PreToolUse guard: keep verification criteria and secrets out of the agent's reach.

Reads one Codex hook event on stdin and prints a decision on stdout.
- protect.paths: files the agent must not create, edit, move or delete
- deny_read.paths: files the agent must not read or write (for example .env)
- secrets.patterns: strings that must never appear in a command or patch

This is a string-level guard for a cooperative agent under pressure, not a sandbox.
See docs/THREAT_MODEL.md in codex-harness-kit for what it does not catch.
"""
from __future__ import annotations

import fnmatch
import glob
import os
import re
import shlex
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harnesslib as hl  # noqa: E402

Finding = Dict[str, Any]

# Commands that only read. deny_read still applies to their arguments.
READONLY = {
    "cat", "less", "more", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "tree", "diff", "cmp", "comm", "sha256sum", "shasum", "md5", "md5sum", "uniq", "cut", "tr",
    "nl", "jq", "yq", "bat", "realpath", "readlink", "dirname", "basename", "echo", "printf",
    "true", "false", "pwd", "which", "type", "od", "xxd", "hexdump", "tac", "column", "fold",
    "paste", "join", "base64", "strings", "fzf", "watch", "sleep", "date", "printenv",
}
# Commands that look at names or metadata only, so deny_read does not apply either.
METADATA_ONLY = {"ls", "stat", "file", "du", "test", "[", "[[", "fd", "fdfind", "exa", "eza", "lsd"}
SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "}", "done", "fi", "esac"}
LOOP_HEADERS = {"for", "select", "case", "function"}
GIT_KEEPS_WORKTREE = {"status", "rev-parse", "shortlog", "describe", "ls-files", "ls-tree", "branch", "remote",
                "config", "fetch", "add", "commit", "push", "pull", "tag", "worktree",
                "merge-base", "reflog", "init", "clone"}
WRITE_ALL = {"rm", "rmdir", "unlink", "shred", "touch", "truncate", "mkdir", "chmod", "chown",
             "chgrp", "mv", "tee", "sponge"}
WRITE_LAST = {"cp", "ln", "install", "rsync", "scp"}
EDITORS = {"ed", "ex", "vi", "vim", "nvim", "nano", "emacs", "micro"}
INPLACE = {"sed", "gsed", "perl", "ruby", "awk", "gawk"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
INTERPRETERS = {"python", "python3", "node", "deno", "bun", "ruby", "perl", "php", "osascript"}
WRAPPERS = {"sudo", "env", "command", "builtin", "exec", "nice", "nohup", "stdbuf", "timeout",
            "caffeinate", "doas", "xargs", "time"}
# Package runners whose next word is the real program.
RUNNERS = {"npx", "pnpx", "bunx", "uvx", "pipx"}
PKG_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
PKG_BUILTINS = {"install", "i", "add", "remove", "rm", "uninstall", "update", "upgrade", "ci",
                "init", "create", "link", "publish", "pack", "audit", "outdated", "why", "list",
                "ls", "config", "cache", "dedupe", "import", "info", "view", "login", "logout",
                "version", "set", "patch", "rebuild", "prune", "store", "workspaces", "workspace",
                "help", "pkg", "env", "root", "bin", "prefix", "fund", "doctor", "query"}
# Programs that run tests; they may take protected paths as arguments.
TEST_RUNNERS = {"vitest", "jest", "mocha", "ava", "tap", "playwright", "cypress", "pytest", "py.test",
                "tox", "nox", "coverage", "tsx", "ts-node", "karma", "wdio", "c8", "nyc", "bats", "unittest"}
# Formatters that always rewrite their arguments, and those that only do with a flag or subcommand.
FORMAT_ALWAYS = {"black", "isort", "autopep8", "yapf", "rustfmt", "swiftformat", "shfmt"}
FORMAT_WITH = {
    "prettier": {"--write", "-w"}, "gofmt": {"-w"}, "goimports": {"-w"}, "eslint": {"--fix"},
    "standard": {"--fix"}, "biome": {"--write", "--apply", "--fix", "format"}, "ruff": {"--fix", "format"},
    "dprint": {"fmt"}, "clang-format": {"-i"}, "ktlint": {"-F", "--format"}, "stylelint": {"--fix"},
    "rubocop": {"-a", "-A", "--autocorrect", "--autocorrect-all"}, "cargo": {"fmt", "fix"},
}
SEPARATORS = set(";&|()\n")
DEFAULT_UPDATE_FLAGS = ["-u", "--update", "--updateSnapshot", "--update-snapshots", "--snapshot-update", "--baseline"]
WRITE_MARKERS = re.compile(
    r"""(['"](?:w|a|x|wb|ab|w\+|a\+|r\+)['"]|\bwrite|\bunlink|\bremove|\brmtree|\brmSync|\brmdir|"""
    r"""\brename|\breplace\(|\btruncate|\bmkdir|\bcopy|\bmove|\bsymlink|\bchmod|appendFile|\bfs\.rm)""")


class Ctx:
    def __init__(self, cfg: Dict[str, Any], root: str, cwd: str):
        self.cfg = cfg
        self.root = os.path.realpath(root)
        self.cwd = cwd
        self.protect = hl.PathRules(cfg["protect"]["paths"])
        self.deny_read = hl.PathRules(cfg["deny_read"]["paths"])
        self.secrets = hl.compile_secrets(cfg)
        allow = list(cfg["protect"].get("allow_commands", []))
        allow += [c["run"] for c in cfg["verify"]["commands"]]
        self.allow = []
        for a in allow:
            try:
                self.allow.append(" ".join(shlex.split(a)))
            except ValueError:
                pass
        self.update_flags = cfg["protect"].get("update_flags", DEFAULT_UPDATE_FLAGS)
        self.home_codex = os.path.realpath(os.path.expanduser("~/.codex"))
        self._status: Optional[List[Tuple[str, str]]] = None

    def rel(self, path: str, base: Optional[str] = None) -> Optional[str]:
        return hl.to_rel(path, base or self.cwd, self.root)

    def git(self, *args: str) -> Tuple[int, str]:
        try:
            p = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, timeout=10)
            return p.returncode, p.stdout
        except (OSError, subprocess.SubprocessError):
            return 1, ""

    def dirty_protected(self, include_untracked: bool) -> Optional[str]:
        """A protected path with uncommitted changes (for whole-tree git operations)."""
        if self._status is None:
            code, out = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all")
            self._status = [(e[:2], e[3:]) for e in out.split("\0") if len(e) > 3] if code == 0 else []
        for status, path in self._status:
            if status == "??" and not include_untracked:
                continue
            if self.protect.match(path):
                return path
        return None

    def protected_between(self, a: str, b: str, scope: str = "") -> Optional[str]:
        """A protected path that differs between two revisions, '?' if git cannot tell."""
        args = ["diff", "--name-only", a, b, "--"] + ([scope] if scope and scope != "." else [])
        code, out = self.git(*args)
        if code != 0:
            return "?"
        for path in out.splitlines():
            if self.protect.match(path):
                return path
        return None


def finding(kind: str, target: str, rule: str = "", ids: Optional[List[str]] = None) -> Finding:
    return {"kind": kind, "target": target, "rule": rule, "ids": ids or []}


# ------------------------------------------------------------- patches and diffs

PATCH_HEADER = re.compile(r"^\s*\*\*\*\s*(Add File|Update File|Delete File|Move to)\s*:\s*(.+?)\s*$")
DIFF_HEADER = re.compile(r"^(?:\+\+\+ |--- |rename (?:to|from) )(?:[ab]/)?(\S.*?)\s*$")


def clean_patch_path(p: str) -> str:
    p = re.sub(r"\t.*$", "", p.strip())
    return p.strip("\"'").replace("\\", "/")


def path_hits(ctx: Ctx, path: str, base: str) -> List[Finding]:
    """deny_read and protect matches for one concrete path (no globbing)."""
    out: List[Finding] = []
    try:
        full = hl.resolve(path, base)
    except (ValueError, OSError):
        return out
    if full == ctx.home_codex or full.startswith(ctx.home_codex + os.sep):
        return [finding("protect", path, "~/.codex/")]
    rel = ctx.rel(path, base)
    if rel is None or rel == ".":
        return out
    hit = ctx.deny_read.match(rel)
    if hit:
        out.append(finding("deny_read", rel, hit))
    hit = ctx.protect.match(rel)
    if hit:
        out.append(finding("protect", rel, hit))
    return out


# Lines inside build files that decide what the verification commands actually run.
TEST_SETTINGS = {
    "package.json": re.compile(r'"(?:test|test:[\w:-]+|typecheck|type-check|lint|vitest|jest)"\s*:'),
    "pyproject.toml": re.compile(r"\[tool\.(?:pytest|coverage)|^\s*(?:addopts|testpaths|python_files|python_functions)\s*="),
    "setup.cfg": re.compile(r"\[tool:pytest\]|^\s*(?:addopts|testpaths)\s*="),
}


def check_patch(patch: str, ctx: Ctx, base: Optional[str] = None) -> List[Finding]:
    out: List[Finding] = []
    added: List[str] = []
    current = ""
    for line in patch.splitlines():
        m = PATCH_HEADER.match(line)
        if m:
            path = clean_patch_path(m.group(2))
            out += path_hits(ctx, path, base or ctx.cwd)
            current = os.path.basename(path)
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        rule = TEST_SETTINGS.get(current)
        if rule and line[:1] in "+-" and not line.startswith(("+++", "---")) and rule.search(line[1:]):
            out.append(finding("test_setting", f"{current}: {line[1:].strip()[:60]}", "test settings"))
    ids = hl.find_secrets("\n".join(added), ctx.secrets)
    if ids:
        out.append(finding("secret", "patch", ids=ids))
    return out


def check_diff_text(text: str, ctx: Ctx, base: str) -> List[Finding]:
    """Unified diffs (git apply, patch) and Codex patches found anywhere in a text."""
    out: List[Finding] = []
    for line in text.splitlines():
        m = DIFF_HEADER.match(line)
        if m and m.group(1) != "/dev/null":
            out += [f for f in path_hits(ctx, clean_patch_path(m.group(1)), ctx.root) if f["kind"] == "protect"]
    if PATCH_HEADER.search(text) or any(PATCH_HEADER.match(line) for line in text.splitlines()):
        out += check_patch(text, ctx, base)
    return out


# ------------------------------------------------------------- shell parsing

HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def split_heredocs(cmd: str) -> Tuple[str, List[str]]:
    """Remove heredoc bodies from the command and return them separately, in order."""
    kept, bodies = [], []
    pending: List[str] = []
    current: List[str] = []
    for line in cmd.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                bodies.append("\n".join(current))
                current = []
                pending.pop(0)
            else:
                current.append(line)
            continue
        kept.append(line)
        pending = [m.group(2) for m in HEREDOC.finditer(line) if not line[m.start():].startswith("<<<")]
    if current:
        bodies.append("\n".join(current))
    return "\n".join(kept), bodies


def tokenize(cmd: str) -> List[str]:
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def is_sep(tok: str) -> bool:
    return bool(tok) and all(c in SEPARATORS for c in tok)


def is_redirect(tok: str) -> bool:
    return bool(tok) and all(c in "<>&|" for c in tok) and ("<" in tok or ">" in tok)


Segment = Tuple[List[str], List[Tuple[str, str]], str]


def segments(tokens: List[str]) -> List[Segment]:
    """Split into simple commands: (words, [(redirect_op, target)], separator_after)."""
    out: List[Segment] = []
    words: List[str] = []
    redirs: List[Tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if is_redirect(t):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if words and words[-1].isdigit():
                words.pop()  # the fd number of `2>` or `3>`
            redirs.append((t, target))
            i += 2
            continue
        if is_sep(t):
            out.append((words, redirs, t))
            words, redirs = [], []
            i += 1
            continue
        words.append(t)
        i += 1
    if words or redirs:
        out.append((words, redirs, ""))
    return out


def strip_wrappers(words: List[str]) -> Tuple[List[str], bool]:
    """Drop `VAR=x`, sudo/env/timeout/xargs and shell keywords. Returns (words, via_xargs)."""
    i, via_xargs = 0, False
    while i < len(words):
        w = words[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w) or w in SHELL_KEYWORDS:
            i += 1
            continue
        name = os.path.basename(w)
        if name in WRAPPERS:
            via_xargs = via_xargs or name == "xargs"
            i += 1
            while i < len(words):
                a = words[i]
                if name == "xargs" and a in ("-I", "-J", "-n", "-P", "-L", "-d", "-E", "-s") and i + 1 < len(words):
                    i += 2
                    continue
                if a.startswith("-") or re.match(r"^[A-Za-z_]\w*=", a) or (name in ("timeout", "nice") and re.match(r"^[\d.]+[smhd]?$", a)):
                    i += 1
                    continue
                break
            continue
        break
    return words[i:], via_xargs


def prog_name(word: str) -> str:
    name = os.path.basename(word)
    if name.startswith("@"):
        return "@" + name[1:].split("@", 1)[0]
    return name.split("@", 1)[0] if "@" in name else name


def program(words: List[str]) -> Tuple[str, List[str]]:
    """Name and arguments of the program that actually runs (through npx, pnpm exec, python -m)."""
    name, args = prog_name(words[0]), words[1:]
    for _ in range(4):
        if name in RUNNERS and args:
            k = 0
            while k < len(args) and args[k].startswith("-"):
                k += 1
            if name == "pipx" and k < len(args) and args[k] == "run":
                k += 1
            if k >= len(args):
                break
            name, args = prog_name(args[k]), args[k + 1:]
        elif name in PKG_MANAGERS and args:
            sub = args[0]
            if sub in ("exec", "dlx", "x") and len(args) > 1:
                name, args = prog_name(args[1]), args[2:]
            elif name in ("pnpm", "yarn", "bun") and not sub.startswith("-") and sub not in PKG_BUILTINS | {"run", "test", "t"}:
                name, args = prog_name(sub), args[1:]  # `pnpm vitest`, `yarn jest`
            else:
                break
        elif name in ("uv", "poetry", "pdm", "hatch", "pipenv") and args[:1] == ["run"] and len(args) > 1:
            name, args = prog_name(args[1]), args[2:]
        elif re.match(r"^python3?(\.\d+)?$", name) and len(args) >= 2 and args[0] == "-m":
            name, args = prog_name(args[1]), args[2:]
        else:
            break
    return name, args


def non_options(args: List[str]) -> List[str]:
    out, after_dd = [], False
    for a in args:
        if a == "--":
            after_dd = True
            continue
        if after_dd or not a.startswith("-") or a == "-":
            out.append(a)
    return out


def looks_like_path(a: str) -> bool:
    return bool(a) and a != "-" and "\0" not in a and not re.match(r"^[a-z]+://", a) and not re.search(r"\s", a)


def has_glob(a: str) -> bool:
    return bool(re.search(r"[*?\[]", a))


# ------------------------------------------------------------- shell checks


def path_findings(ctx: Ctx, arg: str, base: str, write: bool, whole_dir: bool = False) -> List[Finding]:
    """Findings for one shell argument: variables, globs and directories included."""
    if not looks_like_path(arg):
        return []
    arg = arg.replace("${PWD}", base).replace("$PWD", base)
    if "$" in arg or "`" in arg:
        # Unknown variable: judge the literal tail, e.g. "$ROOT/tests/a.ts" -> "tests/a.ts".
        tail = re.split(r"[$`][^/]*/?", arg)[-1]
        out: List[Finding] = []
        if tail:
            for b in (ctx.root, base):
                out += [f for f in path_hits(ctx, tail, b) if write or f["kind"] == "deny_read"]
        return out
    if has_glob(arg):
        pattern = os.path.expanduser(arg if os.path.isabs(arg) else os.path.join(base, arg))
        try:
            matches = glob.glob(pattern, recursive="**" in arg)[:5000]
        except (OSError, re.error, ValueError):
            matches = []
        out = []
        if write and not matches:
            out += [f for f in path_hits(ctx, arg, base) if f["kind"] == "protect"]
        for m in matches:
            out += path_findings(ctx, m, base, write, whole_dir)
        return out
    out = [f for f in path_hits(ctx, arg, base) if write or f["kind"] == "deny_read"]
    if write and whole_dir and not any(f["kind"] == "protect" for f in out):
        rel = ctx.rel(arg, base)
        if rel is not None and (rel == "." or os.path.isdir(os.path.join(ctx.root, rel))):
            hit = ctx.protect.covers_inside(rel, ctx.root)
            if hit:
                out.append(finding("protect_dir", rel, hit))
    return out


def deny_read_in_dir(ctx: Ctx, arg: str, base: str) -> List[Finding]:
    rel = ctx.rel(arg, base)
    if rel is None or not os.path.isdir(os.path.join(ctx.root, rel)):
        return []
    hit = ctx.deny_read.covers_inside(rel, ctx.root)
    return [finding("deny_read", rel, hit)] if hit and not hit.startswith("(") else []


def mentions(ctx: Ctx, text: str, base: str, writes: Optional[bool] = None) -> List[Finding]:
    """Path-like words inside inline code. Protected paths count only if the code writes."""
    if writes is None:
        writes = bool(WRITE_MARKERS.search(text))
    out: List[Finding] = []
    for word in set(re.findall(r"[\w.~/-]*[\w~-][\w.~/-]*", text)):
        if "/" not in word and "." not in word:
            continue
        for f in path_hits(ctx, word, base):
            if f["kind"] == "deny_read" or writes:
                out.append(f)
    return out


def update_flag(ctx: Ctx, words: List[str]) -> Optional[str]:
    """A flag that makes a test runner rewrite expectations (snapshots, fixtures)."""
    for f in ctx.update_flags:
        if f in words or any(w.startswith(f + "=") for w in words):
            return f
    return None


def allowlisted(ctx: Ctx, words: List[str]) -> bool:
    line = " ".join(words)
    return any(line == a or line.startswith(a + " ") for a in ctx.allow)


def is_test_run(ctx: Ctx, words: List[str], name: str, args: List[str]) -> bool:
    if name in TEST_RUNNERS or allowlisted(ctx, words):
        return True
    if name in PKG_MANAGERS and args and (args[0] in ("test", "t") or (args[0] == "run" and len(args) > 1)):
        return True  # package.json scripts; their arguments are passed through
    return name in ("node", "bun", "deno", "go", "cargo") and bool(args) and args[0] in ("--test", "test")


def check_shell(cmd: str, ctx: Ctx, depth: int = 0) -> List[Finding]:
    if depth > 4:
        return [finding("unparsed", "nested shell", "depth")]
    out: List[Finding] = []
    ids = hl.find_secrets(cmd, ctx.secrets)
    if ids and depth == 0:
        out.append(finding("secret", "command", ids=ids))
    base = ctx.cwd
    stripped, bodies = split_heredocs(cmd)
    for pat, repl in ((r"\$\(\s*pwd\s*\)|`\s*pwd\s*`", base),
                      (r"\$\(\s*git rev-parse --show-toplevel\s*\)|`\s*git rev-parse --show-toplevel\s*`", ctx.root)):
        stripped = re.sub(pat, lambda _m, r=repl: shlex.quote(r), stripped)
    try:
        tokens = tokenize(stripped)
    except ValueError:
        hits = mentions(ctx, cmd, base, writes=True)
        return out + [finding("unparsed", h["target"], h["rule"]) for h in hits[:1]]

    segs = segments(tokens)
    body_iter = iter(bodies)
    dynamic = re.search(r"\$\(|`|<\(|>\(", stripped) is not None
    for idx, (raw_words, redirs, sep) in enumerate(segs):
        heredoc_body: Optional[str] = None
        here_string: Optional[str] = None
        for op, target in redirs:
            if op.startswith("<<") and op != "<<<":
                heredoc_body = next(body_iter, None)
            elif op == "<<<":
                here_string = target
            elif op in ("<", "<>"):
                out += path_findings(ctx, target, base, write=op == "<>")
            elif ">" in op and not target.startswith("&") and not target.isdigit() and target != "/dev/null":
                out += path_findings(ctx, target, base, write=True)
        if heredoc_body:
            out += check_diff_text(heredoc_body, ctx, base)
        if not raw_words or raw_words[0] in LOOP_HEADERS:
            continue
        words, via_xargs = strip_wrappers(raw_words)
        if not words:
            continue
        name, args = program(words)
        input_files = [t for op, t in redirs if op in ("<", "<>")]

        # Arguments built at run time cannot be checked; refuse if this command may write.
        if dynamic and any("$" in w or "`" in w for w in words) and name not in READONLY | METADATA_ONLY | {"git", "cd"} \
                and not is_test_run(ctx, words, name, args):
            hits = [h for h in mentions(ctx, stripped, base, writes=True) if h["kind"] == "protect"]
            if hits:
                out.append(finding("unparsed", hits[0]["target"], hits[0]["rule"]))

        if name not in METADATA_ONLY and name not in ("cd", "pushd", "popd", "git", "echo", "printf"):
            for a in non_options(args):
                out += [f for f in path_findings(ctx, a, base, write=False) if f["kind"] == "deny_read"]

        if name in ("cd", "pushd"):
            target = non_options(args)[:1]
            if target and "$" not in target[0]:
                base = hl.resolve(target[0], base)
            continue
        if name == "popd":
            base = ctx.cwd
            continue
        if name in SHELLS:
            ci = next((k for k, a in enumerate(args) if re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", a)), None)
            script = args[ci + 1] if ci is not None and ci + 1 < len(args) else (heredoc_body or here_string)
            if script is not None:
                saved, ctx.cwd = ctx.cwd, base
                out += check_shell(script, ctx, depth + 1)
                ctx.cwd = saved
            continue  # `bash tests/run.sh` runs a script the human wrote
        if name == "eval":
            saved, ctx.cwd = ctx.cwd, base
            out += check_shell(" ".join(args), ctx, depth + 1)
            ctx.cwd = saved
            continue
        if name == "apply_patch":
            out += check_patch(heredoc_body or here_string or (args[0] if args else ""), ctx, base)
            continue
        if name == "patch" or (name == "git" and non_options(args)[:1] in (["apply"], ["am"])):
            sources = input_files + [a for a in non_options(args) if a not in ("apply", "am", "-")]
            for src in sources:
                out += read_diff_file(ctx, src, base)
            from_pipe = idx > 0 and segs[idx - 1][2] == "|"
            if from_pipe and not sources and heredoc_body is None and here_string is None:
                out.append(finding("unparsed", "a patch piped from another command", "patch from a pipe"))
            if name == "patch":
                continue
        if name in ("perl", "ruby") and any(re.match(r"^-[a-zA-Z]*i", a) for a in args):
            for a in non_options(args)[1:]:
                out += path_findings(ctx, a, base, write=True)
            if via_xargs:
                out += pipeline_paths(ctx, segs, idx, base)
            continue
        if name in INTERPRETERS or re.match(r"^python3?(\.\d+)?$", name):
            flag = update_flag(ctx, words)
            if flag and (allowlisted(ctx, words) or any(".codex/harness/" in a.replace("\\", "/") for a in args)):
                out.append(finding("update_flag", " ".join(words), flag))
                continue
            code_parts = []
            for flag in ("-c", "-e", "--eval", "-p", "--print"):
                if flag in args and args.index(flag) + 1 < len(args):
                    code_parts.append(args[args.index(flag) + 1])
            if heredoc_body is not None and (not args or args[0] == "-"):
                code_parts.append(heredoc_body)
            for code in code_parts:
                out += mentions(ctx, code, base)
            continue  # running a script file: what it writes is not visible here
        if name == "git":
            out += check_git(args, ctx, base)
            continue
        if name == "find":
            out += check_find(args, ctx, base)
            continue
        if name == "sort":
            for k, a in enumerate(args):
                if a == "-o" and k + 1 < len(args):
                    out += path_findings(ctx, args[k + 1], base, write=True)
                elif a.startswith("--output="):
                    out += path_findings(ctx, a.split("=", 1)[1], base, write=True)
            continue
        if name in ("grep", "egrep", "fgrep") and any(a in ("-r", "-R", "--recursive") or re.match(r"^-[a-zA-Z]*[rR]", a) for a in args):
            for d in non_options(args)[1:] or ["."]:
                out += deny_read_in_dir(ctx, d, base)
            continue
        if name in ("rg", "ag") and any(a in ("--hidden", "--no-ignore", "-uu", "-uuu", "-u") for a in args):
            for d in non_options(args)[1:] or ["."]:
                out += deny_read_in_dir(ctx, d, base)
            continue
        if name in READONLY or name in METADATA_ONLY:
            continue
        if name in WRITE_ALL:
            whole = name in ("rm", "rmdir", "mv", "chmod", "chown", "chgrp")
            for a in non_options(args):
                out += path_findings(ctx, a, base, write=True, whole_dir=whole)
            if via_xargs:
                out += pipeline_paths(ctx, segs, idx, base)
            continue
        if name in WRITE_LAST:
            targets = non_options(args)
            tdir = target_dir_flag(args)
            for t in ([tdir] if tdir else targets[-1:]):
                out += path_findings(ctx, t, base, write=True, whole_dir=True)
            if via_xargs:
                out += pipeline_paths(ctx, segs, idx, base)
            continue
        if name == "dd":
            for a in args:
                if a.startswith("of="):
                    out += path_findings(ctx, a[3:], base, write=True)
            continue
        if name in INPLACE:
            if any(a == "-i" or re.match(r"^-[a-zA-Z]*i", a) or a.startswith("--in-place") or a == "inplace" for a in args):
                files = non_options(args)
                if name in ("sed", "gsed") and not any(a in ("-e", "-f") or a.startswith("--expression") for a in args):
                    files = files[1:]  # the first word is the sed script (or the empty BSD suffix)
                    if files and args and "-i" in args and args[args.index("-i") + 1:args.index("-i") + 2] == [""]:
                        files = files[1:]
                for a in files:
                    out += path_findings(ctx, a, base, write=True)
                if via_xargs:
                    out += pipeline_paths(ctx, segs, idx, base)
            continue
        if name in EDITORS:
            for a in non_options(args):
                out += path_findings(ctx, a, base, write=True)
            continue
        if name in FORMAT_ALWAYS or name in FORMAT_WITH:
            if formatter_writes(name, args):
                targets = [a for a in non_options(args) if a not in FORMAT_WITH.get(name, set())] or ["."]
                for a in targets:
                    out += path_findings(ctx, a, base, write=True, whole_dir=True)
                if via_xargs:
                    out += pipeline_paths(ctx, segs, idx, base)
            continue
        if name in PKG_MANAGERS and args[:2] in (["pkg", "set"], ["pkg", "delete"]) and any(a.startswith("scripts.") for a in args):
            out.append(finding("test_setting", "package.json scripts", "npm pkg set scripts.*"))
            continue
        flag = update_flag(ctx, words)
        test_run = is_test_run(ctx, words, name, args)
        if flag and test_run:
            out.append(finding("update_flag", " ".join(words), flag))
            continue
        if test_run:
            continue
        # Unknown command: a protected path as an argument means it may be written.
        for a in non_options(args):
            out += [f for f in path_findings(ctx, a, base, write=True) if f["kind"] == "protect"]
    return out


def formatter_writes(name: str, args: List[str]) -> bool:
    if name in FORMAT_ALWAYS:
        return "--check" not in args and "--diff" not in args
    flags = FORMAT_WITH.get(name)
    return bool(flags) and any(a in flags for a in args)


def target_dir_flag(args: List[str]) -> Optional[str]:
    for k, a in enumerate(args):
        if a in ("-t", "--target-directory") and k + 1 < len(args):
            return args[k + 1]
        if a.startswith("--target-directory="):
            return a.split("=", 1)[1]
    return None


def pipeline_paths(ctx: Ctx, segs: List[Segment], idx: int, base: str) -> List[Finding]:
    """`... | xargs rm`: the paths come from the earlier commands in the pipeline."""
    out: List[Finding] = []
    j = idx - 1
    while j >= 0 and segs[j][2] == "|":
        words, _ = strip_wrappers(segs[j][0])
        for a in non_options(words[1:]):
            out += [f for f in path_findings(ctx, a, base, write=True, whole_dir=True) if f["kind"] in ("protect", "protect_dir")]
        if words[:1] in (["find"], ["fd"]) and not non_options(words[1:]):
            out += path_findings(ctx, ".", base, write=True, whole_dir=True)
        j -= 1
    return out


def read_diff_file(ctx: Ctx, path: str, base: str) -> List[Finding]:
    try:
        with open(hl.resolve(path, base), encoding="utf-8", errors="replace") as f:
            text = f.read(2_000_000)
    except (OSError, ValueError):
        return []
    return check_diff_text(text, ctx, base)


def check_find(args: List[str], ctx: Ctx, base: str) -> List[Finding]:
    if not any(a in args for a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls")):
        return []
    starts = []
    for a in args:
        if a.startswith("-") or a in ("(", "!", ")"):
            break
        starts.append(a)
    names = [args[k + 1] for k, a in enumerate(args[:-1]) if a in ("-name", "-iname")]
    out: List[Finding] = []
    for start in starts or ["."]:
        rel = ctx.rel(start, base)
        if rel is None:
            continue
        if not names:
            out += path_findings(ctx, start, base, write=True, whole_dir=True)
            continue
        top = os.path.join(ctx.root, "" if rel == "." else rel)
        seen = 0
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
            for fname in filenames + dirnames:
                seen += 1
                if seen > 50000:
                    return out + [finding("unparsed", start, "too many files")]
                hit_name = any(fnmatch.fnmatchcase(fname.lower(), n.lower()) if hl.CASE_INSENSITIVE_FS
                               else fnmatch.fnmatchcase(fname, n) for n in names)
                if hit_name:
                    p = os.path.relpath(os.path.join(dirpath, fname), ctx.root).replace(os.sep, "/")
                    hit = ctx.protect.match(p) or ctx.protect.covers_inside(p, ctx.root)
                    if hit:
                        return out + [finding("protect", p, hit)]
    return out


def check_git(args: List[str], ctx: Ctx, base: str) -> List[Finding]:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-C", "-c", "--git-dir", "--work-tree") and i + 1 < len(args):
            if args[i] == "-C":
                base = hl.resolve(args[i + 1], base)
            i += 2
        else:
            i += 1
    if i >= len(args):
        return []
    sub, rest = args[i], args[i + 1:]
    out: List[Finding] = []
    if sub in ("show", "cat-file", "grep", "log", "diff", "blame"):
        for a in rest:
            if ":" in a and not a.startswith("-") and "://" not in a:
                out += [f for f in path_findings(ctx, a.split(":", 1)[1], ctx.root, write=False) if f["kind"] == "deny_read"]
        if sub == "diff" and any(a.startswith("--output") for a in rest):
            out += [f for a in rest if a.startswith("--output=") for f in path_findings(ctx, a.split("=", 1)[1], base, write=True)]
        return out
    if sub in GIT_KEEPS_WORKTREE or sub in ("apply", "am"):
        return []
    paths = non_options(rest)
    if sub in ("checkout", "switch") and "--" not in rest:
        revs = [p for p in paths if not os.path.exists(hl.resolve(p, base))]
        if sub == "switch" and not any(a in rest for a in ("--detach", "-d")):
            return []  # switching branches is normal work
        if revs and len(revs) == len(paths) and not any(a in rest for a in ("-b", "-B", "-c", "-C", "--orphan")):
            rev = revs[0]
            if sub == "checkout" and ctx.git("show-ref", "--verify", "--quiet", f"refs/heads/{rev}")[0] == 0:
                return []  # a local branch
            hit = ctx.protected_between(rev, "HEAD")
            if hit and hit != "?":  # "?": not a revision git knows, so git itself will refuse
                return [finding("protect_dir", f"git {sub} {rev}", hit)]
            return []
        if sub == "switch":
            return []
    if sub == "read-tree" and any(a in rest for a in ("-u", "--reset", "-m")):
        rev = paths[-1] if paths else "HEAD"
        hit = ctx.protected_between(rev, "HEAD") if rev != "HEAD" else ctx.dirty_protected(include_untracked=False)
        return [finding("protect_dir", f"git read-tree {rev}", hit)] if hit else []
    if sub in ("checkout", "restore"):
        if sub == "restore" and ("--staged" in rest or "-S" in rest) and not ("--worktree" in rest or "-W" in rest):
            return []
        if any(a in rest for a in ("-b", "-B", "--orphan")):
            return []
        source = None
        for k, a in enumerate(rest):
            if a in ("--source", "-s") and k + 1 < len(rest):
                source = rest[k + 1]
            elif a.startswith("--source="):
                source = a.split("=", 1)[1]
        if "--" in rest:
            before = non_options(rest[:rest.index("--")])
            explicit = rest[rest.index("--") + 1:]
            if before and sub == "checkout":
                source = before[0]
        else:
            explicit = [p for p in paths if p != source and (p == "." or os.path.exists(hl.resolve(p, base)) or has_glob(p))]
            if sub == "checkout" and explicit and paths and paths[0] not in explicit:
                source = paths[0]
        for p in explicit:
            if source:
                hit = ctx.protected_between(source, "HEAD", "" if p == "." else (ctx.rel(p, base) or ""))
                if hit:
                    out.append(finding("protect_dir", f"git {sub} {source} {p}", hit))
                continue
            rel = ctx.rel(p, base)
            if rel is not None and (rel == "." or os.path.isdir(os.path.join(ctx.root, rel))):
                dirty = ctx.dirty_protected(include_untracked=False)
                if dirty and (rel == "." or dirty == rel or dirty.startswith(rel + "/")):
                    out.append(finding("protect_dir", f"git {sub} {p}", dirty))
                continue
            dirty = ctx.dirty_protected(include_untracked=False)
            if rel is not None and dirty == rel:
                out.append(finding("protect", rel, ctx.protect.match(rel) or rel))
            else:
                out += [f for f in path_findings(ctx, p, base, write=True) if f["kind"] == "protect"]
    elif sub in ("rm", "mv"):
        if sub == "rm" and "--cached" in rest:
            return []
        for p in paths:
            out += path_findings(ctx, p, base, write=True, whole_dir=True)
    elif sub == "stash":
        if paths[:1] in (["list"], ["show"], ["branch"], ["pop"], ["apply"], ["drop"], ["clear"]):
            return []
        scoped = rest[rest.index("--") + 1:] if "--" in rest else [p for p in paths if p not in ("push", "save")]
        if scoped:
            for p in scoped:
                out += [f for f in path_findings(ctx, p, base, write=True, whole_dir=True) if f["kind"] != "deny_read"]
        else:
            include_untracked = any(a in rest for a in ("-u", "--include-untracked", "-a", "--all"))
            dirty = ctx.dirty_protected(include_untracked)
            if dirty:
                out.append(finding("protect_dir", "git stash", dirty))
    elif sub == "reset":
        if not any(a in rest for a in ("--hard", "--merge", "--keep")):
            return []
        dirty = ctx.dirty_protected(include_untracked=False)
        rev = paths[0] if paths else "HEAD"
        if dirty:
            out.append(finding("protect_dir", "git reset", dirty))
        elif rev != "HEAD":
            hit = ctx.protected_between(rev, "HEAD")
            if hit:
                out.append(finding("protect_dir", f"git reset {rev}", hit))
    elif sub == "clean":
        if any(a in rest for a in ("-n", "--dry-run")):
            return []
        dirty = ctx.dirty_protected(include_untracked=True)
        if dirty:
            out.append(finding("protect_dir", "git clean", dirty))
    elif sub in ("revert", "cherry-pick"):
        for rev in [p for p in paths][:5]:
            hit = ctx.protected_between(f"{rev}^", rev)
            if hit:
                out.append(finding("protect_dir", f"git {sub} {rev}", hit))
                break
    elif sub == "checkout-index":
        out.append(finding("protect_dir", "git checkout-index", "whole tree"))
    return out


# ------------------------------------------------------------- entry point


def decide(payload: Dict[str, Any], cfg: Dict[str, Any], root: str) -> List[Finding]:
    tool = str(payload.get("tool_name", ""))
    ti = payload.get("tool_input")
    if isinstance(ti, dict):
        command = ti.get("command", ti.get("cmd", ti.get("patch", ti.get("input"))))
    else:
        command = ti
    if isinstance(command, list):
        command = shlex.join(str(c) for c in command)
    if not isinstance(command, str):
        return []
    root = os.path.realpath(root)
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else root
    workdir = ti.get("workdir") if isinstance(ti, dict) and isinstance(ti.get("workdir"), str) else ""
    cwd = os.path.realpath(os.path.join(root, cwd, workdir))
    ctx = Ctx(cfg, root, cwd)
    if "apply_patch" in tool or tool in ("Edit", "Write"):
        return check_patch(command, ctx)
    if tool in ("Bash", "shell", "exec_command", "local_shell", "unified_exec") or "exec" in tool:
        return check_shell(command, ctx)
    return []


def render(f: Finding, cfg: Dict[str, Any]) -> str:
    if f["kind"] == "secret":
        return hl.msg(cfg, "secret", ids=", ".join(f["ids"]))
    return hl.msg(cfg, f["kind"], target=f["target"], rule=f["rule"])


def deny(reason: str) -> None:
    hl.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                    "permissionDecision": "deny",
                                    "permissionDecisionReason": reason}})


def main() -> int:
    root = hl.project_root()
    cfg: Dict[str, Any] = dict(hl.DEFAULTS)
    try:
        cfg = hl.load_config()
        payload = hl.read_payload()
        findings = decide(payload, cfg, root)
    except Exception as e:  # never crash: Codex would treat a crash as "allow"
        err = f"{type(e).__name__}: {e}"[:300]
        hl.log_event(cfg, root, {"hook": "guard", "decision": "error", "error": err})
        if cfg.get("on_error", "block") == "block":
            deny(hl.msg(cfg, "error", err=err))
        else:
            hl.emit({"systemMessage": f"codex harness guard error (allowed): {err}"})
        return 0

    if not findings:
        hl.emit({})
        return 0
    secrets = hl.compile_secrets(cfg)
    ti = payload.get("tool_input")
    raw = next((ti[k] for k in ("command", "cmd", "patch", "input") if k in ti), "") if isinstance(ti, dict) else ti
    event = {"hook": "guard", "tool": payload.get("tool_name"), "turn": payload.get("turn_id"),
             "findings": [{k: v for k, v in f.items() if v} for f in findings[:5]],
             "input_sha": hl.sha(str(raw)), "preview": hl.redact(str(raw), secrets)}
    if cfg["mode"] == "observe":
        hl.log_event(cfg, root, dict(event, decision="would_deny"))
        hl.emit({})
        return 0
    hl.log_event(cfg, root, dict(event, decision="deny"))
    deny(render(findings[0], cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
