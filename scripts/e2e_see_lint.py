#!/usr/bin/env python3
"""Live check of the 0.2.0 modules (lint, see) on fresh projects that have nothing to do with the book.

Each scenario builds a small app from scratch, installs the kit with --lint, --see-url,
--see-start and --see-verify, and sends one feature request to `codex exec`. Runs are judged by
file state, the hook log and the screenshots, not by Codex's answer.

  python3 scripts/e2e_see_lint.py --workdir /tmp/harness-e2e-020 [--only node-feature]

The sandbox stays workspace-write: Codex cannot start a server inside it, but the Stop hook runs
outside the sandbox, so the screen check still happens before Codex finishes.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_codex import events, last_message, run_codex, sh  # noqa: E402

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


NODE_SERVER = '''import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { todos } from './src/todos.js';

const port = Number(process.env.PORT || 3000);
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css' };
createServer(async (req, res) => {
  console.log(new Date().toISOString(), req.method, req.url);
  if (req.url === '/api/todos') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(todos()));
  }
  const path = req.url === '/' ? '/index.html' : req.url;
  try {
    const body = await readFile(new URL('./ui' + path, import.meta.url));
    res.writeHead(200, { 'Content-Type': types[path.slice(path.lastIndexOf('.'))] || 'text/plain' });
    res.end(body);
  } catch {
    res.writeHead(404); res.end('not found');
  }
}).listen(port, '127.0.0.1', () => console.log('listening', port));
'''
NODE_TODOS = '''// Business rules live here. The UI only renders what the API returns.
export const TODAY = '2026-10-11';

const items = [
  { id: 1, title: 'Renew passport', due: '2026-10-14' },
  { id: 2, title: 'Pay electricity bill', due: '2026-10-11' },
  { id: 3, title: 'Book dentist', due: '2026-10-25' },
];

export function todos() {
  return items.map((t) => ({ ...t }));
}
'''
NODE_TEST = '''import test from 'node:test';
import assert from 'node:assert/strict';
import { todos } from '../src/todos.js';

test('three todos with a due date', () => {
  const list = todos();
  assert.equal(list.length, 3);
  assert.ok(list.every((t) => /^\\d{4}-\\d{2}-\\d{2}$/.test(t.due)));
});
'''
NODE_HTML = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Todos</title>
<link rel="stylesheet" href="/style.css"></head>
<body><h1>Todos</h1><ul id="list"></ul><script type="module" src="/app.js"></script></body></html>
'''
NODE_APP = '''const list = document.getElementById('list');
const todos = await (await fetch('/api/todos')).json();
for (const t of todos) {
  const li = document.createElement('li');
  li.textContent = t.title;
  list.appendChild(li);
}
'''
NODE_APP_BROKEN = NODE_APP.replace("list.appendChild(li);", "lst.appendChild(li);")
NODE_CSS = '''body { font-family: system-ui, sans-serif; margin: 24px; }
li { padding: 8px 0; border-bottom: 1px solid #ddd; }
'''
NODE_RULE = {
    "id": "ui-no-date-math",
    "files": ["ui/**/*.js"],
    "forbid": r"new Date\(|Date\.now\(|getTime\(|86400",
    "why": "Dates and day counts are business rules. The UI must not compute them.",
    "fix": "Add the value to the API in src/todos.js (for example daysLeft) and render it in the UI.",
}

PY_SERVER = '''import json, os, pathlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from app.books import books

UI = pathlib.Path(__file__).parent / "ui"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/books":
            body, ctype = json.dumps(books()).encode(), "application/json"
        else:
            f = UI / ("index.html" if self.path == "/" else self.path.lstrip("/"))
            if not f.is_file():
                self.send_response(404); self.end_headers(); return
            body = f.read_bytes()
            ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript", "css": "text/css"}.get(f.suffix[1:], "text/plain")
        self.send_response(200); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(body)


HTTPServer(("127.0.0.1", int(os.environ.get("PORT", "8000"))), H).serve_forever()
'''
PY_BOOKS = '''"""Library loans. Business rules live here; the UI only renders what the API returns."""
LATE_FEE_PER_DAY = 200  # won


def books():
    return [
        {"id": 1, "title": "Dune", "days_late": 0},
        {"id": 2, "title": "Neuromancer", "days_late": 3},
        {"id": 3, "title": "Foundation", "days_late": 10},
    ]
'''
PY_TEST = '''import unittest
from app.books import books


class BooksTest(unittest.TestCase):
    def test_three_loans(self):
        self.assertEqual(len(books()), 3)


if __name__ == "__main__":
    unittest.main()
'''
PY_HTML = NODE_HTML.replace("<title>Todos</title>", "<title>Loans</title>").replace("<h1>Todos</h1>", "<h1>Loans</h1>")
PY_APP = NODE_APP.replace("/api/todos", "/api/books").replace("const todos", "const items").replace("of todos", "of items")
PY_RULE = {
    "id": "ui-no-fee-math",
    "files": ["ui/**/*.js"],
    "forbid": r"\b200\b|\*\s*\w*days|days\w*\s*\*",
    "why": "Late fees are business rules. The UI must not compute them.",
    "fix": "Compute the fee in app/books.py (for example late_fee) and render the API value in the UI.",
}

# name: (stack, broken_ui, prompt)
SCENARIOS = {
    "node-feature": ("node", False, "Show how many days are left until each todo is due, next to its title (e.g. 'Renew passport · 3 days left', 'due today' when 0). Keep it simple."),
    "node-broken-ui": ("node", True, "Change the page heading from 'Todos' to 'My todos'. Nothing else."),
    "python-feature": ("python", False, "Show the late fee for each overdue book next to its title (200 won per late day, e.g. 'Neuromancer · late fee 600 won'). Books that are not late show nothing extra."),
}


def build(path: str, stack: str, broken: bool, port: int) -> None:
    if os.path.exists(path):
        sh(["rm", "-rf", path], ".")
    files = {}
    if stack == "node":
        files = {"package.json": json.dumps({"name": "todos", "type": "module", "scripts": {"test": "node --test", "start": "node server.js"}}, indent=2),
                 "server.js": NODE_SERVER, "src/todos.js": NODE_TODOS, "tests/todos.test.js": NODE_TEST,
                 "ui/index.html": NODE_HTML, "ui/app.js": NODE_APP_BROKEN if broken else NODE_APP, "ui/style.css": NODE_CSS}
        verify, start, rule = "npm test", f"PORT={port} node server.js", NODE_RULE
    else:
        files = {"server.py": PY_SERVER, "app/__init__.py": "", "app/books.py": PY_BOOKS, "tests/__init__.py": "",
                 "tests/test_books.py": PY_TEST, "ui/index.html": PY_HTML, "ui/app.js": PY_APP, "ui/style.css": NODE_CSS}
        verify, start, rule = "python3 -m unittest discover -s tests -q", f"PORT={port} python3 server.py", PY_RULE
    for rel, text in files.items():
        p = os.path.join(path, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
    sh(["git", "init", "-q"], path)
    r = sh([sys.executable, os.path.join(KIT, "install.py"), path, "--preset", stack, "--lang", "en", "--verify", verify,
            "--lint", "--see-url", f"http://127.0.0.1:{port}", "--see-start", start, "--see-verify"], path)
    assert r.returncode == 0, r.stdout + r.stderr
    cfg_path = os.path.join(path, ".codex", "harness", "harness.json")
    with open(cfg_path) as f:
        cfg = json.load(f)
    cfg["lint"] = {"rules": [rule]}
    cfg["see"]["wait_ms"] = 1500
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)
    sh(["git", "add", "-A"], path)
    sh(["git", "-c", "user.email=e2e@local", "-c", "user.name=e2e", "commit", "-qm", "baseline"], path)


def judge(name: str, path: str, new) -> dict:
    verify = [e for e in new if e.get("hook") == "verify"]
    lint = sh([sys.executable, ".codex/harness/harness.py", "lint"], path)
    see = sh([sys.executable, ".codex/harness/harness.py", "see"], path, timeout=240)
    shots = os.path.join(path, "artifacts", "harness", "see")
    checks = {
        "lint passes at the end": lint.returncode == 0,
        "screen check passes at the end": see.returncode == 0,
        "screenshots exist": os.path.isdir(shots) and any(f.endswith(".png") for f in os.listdir(shots)),
        "Stop hook ran verification": any(e.get("decision") in ("pass", "block") for e in verify),
    }
    if name == "node-broken-ui":
        checks["Stop hook blocked on the broken screen first"] = any(e.get("decision") == "block" for e in verify)
        checks["heading changed"] = "My todos" in open(os.path.join(path, "ui", "index.html")).read()
    if name == "node-feature":
        checks["days left come from the API"] = "days" in open(os.path.join(path, "src", "todos.js")).read().lower()
    if name == "python-feature":
        checks["fee comes from the API"] = "fee" in open(os.path.join(path, "app", "books.py")).read().lower()
    checks["_see_output"] = see.stdout[-600:]
    return checks


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workdir", required=True)
    p.add_argument("--model", default="")
    p.add_argument("--only", action="append")
    p.add_argument("--timeout", type=int, default=900)
    args = p.parse_args()
    os.makedirs(args.workdir, exist_ok=True)
    results = []
    for name, (stack, broken, prompt) in SCENARIOS.items():
        if args.only and name not in args.only:
            continue
        path = os.path.realpath(os.path.join(args.workdir, name))
        build(path, stack, broken, free_port())
        before = len(events(path))
        proc, secs = run_codex(path, prompt, args.model, args.timeout)
        with open(os.path.join(args.workdir, f"{name}.jsonl"), "w") as f:
            f.write(proc.stdout)
        new = events(path)[before:]
        checks = judge(name, path, new)
        detail = checks.pop("_see_output")
        ok = all(checks.values())
        results.append({"scenario": name, "ok": ok, "seconds": secs, "checks": checks, "see_output": detail,
                        "final_message": last_message(proc.stdout),
                        "hook_events": [{k: e.get(k) for k in ("hook", "decision", "reason", "failed") if k in e} for e in new]})
        print(f"{'PASS' if ok else 'FAIL'} {name} ({secs}s) {checks}", flush=True)
    version = sh(["codex", "--version"], ".").stdout.strip()
    with open(os.path.join(args.workdir, "summary.json"), "w") as f:
        json.dump({"codex": version, "date": time.strftime("%Y-%m-%d"), "results": results}, f, indent=2, ensure_ascii=False)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
