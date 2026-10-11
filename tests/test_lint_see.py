"""Project rules (harness.py lint) and the screen check (harness.py see)."""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_verify_install import git, new_repo, run_install  # noqa: E402

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KIT, "kit", ".codex", "harness"))
import see as see_mod  # noqa: E402

RULE = {
    "id": "ui-no-money-math",
    "files": ["ui/**/*.js"],
    "exclude": ["ui/**/*.test.js"],
    "forbid": r"\b(refund|fee)\w*\s*[-+*/]",
    "why": "Money is decided in src/rules, not in the UI.",
    "fix": "Call calcRefund() from src/rules and render its result.",
}


def harness(root, *args):
    return subprocess.run([sys.executable, ".codex/harness/harness.py", *args], cwd=root,
                          capture_output=True, text=True, timeout=180)


def set_cfg(root, **over):
    path = os.path.join(root, ".codex", "harness", "harness.json")
    with open(path) as f:
        cfg = json.load(f)
    for k, v in over.items():
        cfg[k] = v
    with open(path, "w") as f:
        json.dump(cfg, f)


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LintTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = new_repo()
        r = run_install(self.root, "--preset", "generic", "--verify", "python3 check.py", "--lint", "--lang", "en")
        self.assertEqual(r.returncode, 0, r.stderr)
        set_cfg(self.root, lint={"rules": [RULE]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_violation_prints_location_and_fix(self):
        write(self.root, "ui/list.js", "const shown = 1;\nconst left = refund - 1500;\n")
        r = harness(self.root, "lint")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ui/list.js:2:", r.stdout)
        self.assertIn("[ui-no-money-math]", r.stdout)
        self.assertIn("fix: Call calcRefund()", r.stdout)

    def test_clean_code_and_excluded_files_pass(self):
        write(self.root, "ui/list.js", "render(calcRefund(r));\n")
        write(self.root, "ui/list.test.js", "expect(refund - 1).toBe(1)\n")
        write(self.root, "src/rules.js", "const x = refund - 1500;\n")
        r = harness(self.root, "lint")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("0 violations", r.stdout)

    def test_ignore_comment_needs_the_rule_id(self):
        write(self.root, "ui/a.js", "const a = fee * 2; // harness-lint-ignore: ui-no-money-math (display only)\n")
        write(self.root, "ui/b.js", "const b = fee * 2; // harness-lint-ignore: other-rule\n")
        r = harness(self.root, "lint")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("ui/a.js", r.stdout)
        self.assertIn("ui/b.js", r.stdout)

    def test_lint_runs_in_verification_and_agents_md_mentions_it(self):
        with open(os.path.join(self.root, ".codex", "harness", "harness.json")) as f:
            cmds = [c["run"] for c in json.load(f)["verify"]["commands"]]
        self.assertIn("python3 .codex/harness/harness.py lint", cmds)
        with open(os.path.join(self.root, "AGENTS.md")) as f:
            self.assertIn("harness.py lint", f.read())

    def test_rules_are_protected_from_the_agent(self):
        patch = ("*** Begin Patch\n*** Update File: .codex/harness/harness.json\n@@\n-x\n+y\n*** End Patch")
        r = harness(self.root, "check", "--bash", "rm .codex/harness/harness.json")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertTrue(patch)

    def test_bad_rule_is_a_config_error(self):
        set_cfg(self.root, lint={"rules": [{"id": "x", "files": ["**/*.js"], "forbid": "("}]})
        r = harness(self.root, "lint")
        self.assertEqual(r.returncode, 2)
        self.assertIn("bad regex", r.stderr)


class SeeConfigTest(unittest.TestCase):
    def test_out_inside_codex_is_rejected(self):
        tmp, root = new_repo()
        try:
            r = run_install(root, "--preset", "generic", "--verify", "python3 check.py",
                            "--see-url", "http://127.0.0.1:9", "--lang", "en")
            self.assertEqual(r.returncode, 0, r.stderr)
            set_cfg(root, see={"url": "http://127.0.0.1:9", "out": ".codex/shots"})
            r = harness(root, "see")
            self.assertEqual(r.returncode, 2)
            self.assertIn("outside .codex", r.stderr)
        finally:
            tmp.cleanup()

    def test_install_ignores_screenshots_and_mentions_see(self):
        tmp, root = new_repo()
        try:
            r = run_install(root, "--preset", "generic", "--verify", "python3 check.py",
                            "--see-url", "http://127.0.0.1:9", "--see-start", "python3 app.py", "--lang", "en")
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(root, ".gitignore")) as f:
                self.assertIn("artifacts/harness/see/", f.read())
            with open(os.path.join(root, "AGENTS.md")) as f:
                self.assertIn("harness.py see", f.read())
        finally:
            tmp.cleanup()


@unittest.skipUnless(see_mod.find_chrome({}), "Chrome or Chromium not installed")
class SeeRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = new_repo()
        self.port = free_port()
        write(self.root, "site/index.html", "<h1>ok</h1>")
        write(self.root, "site/broken.html", "<h1>x</h1><script>undefinedFn()</script>")
        r = run_install(self.root, "--preset", "generic", "--verify", "python3 check.py", "--lang", "en",
                        "--see-url", f"http://127.0.0.1:{self.port}",
                        "--see-start", f"python3 -m http.server {self.port} --bind 127.0.0.1 --directory site")
        self.assertEqual(r.returncode, 0, r.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def pages(self, *pages):
        with open(os.path.join(self.root, ".codex", "harness", "harness.json")) as f:
            cfg = json.load(f)
        cfg["see"]["pages"] = [{"name": n, "path": p} for n, p in pages]
        cfg["see"]["viewports"] = ["390x844"]
        cfg["see"]["wait_ms"] = 800
        set_cfg(self.root, see=cfg["see"])

    def test_clean_page_passes_and_server_is_stopped(self):
        self.pages(("home", "/index.html"))
        r = harness(self.root, "see")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.root, "artifacts/harness/see/home-390x844.png")))
        self.assertIn("GET /index.html", r.stdout)  # server log tail
        self.assertIsNone(see_mod.is_up(f"http://127.0.0.1:{self.port}/"))

    def test_narrow_viewport_is_the_real_layout_width(self):
        # Headless Chrome will not make a window narrower than 500px; a 390px phone screenshot
        # must still be laid out at 390px, not cut from a 500px page.
        write(self.root, "site/width.html",
              "<meta name='viewport' content='width=device-width, initial-scale=1'>"
              "<script>console.log('W=' + innerWidth)</script><div style='width:100%;height:40px'></div>")
        self.pages(("width", "/width.html"))
        r = harness(self.root, "see")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("W=390", r.stdout)
        with open(os.path.join(self.root, "artifacts/harness/see/width-390x844.png"), "rb") as f:
            head = f.read(24)
        self.assertEqual(struct.unpack(">II", head[16:24]), (390, 844))

    def test_console_error_and_failed_load_are_reported(self):
        write(self.root, "site/err.html",
              "<h1>x</h1><img src='/missing.png'><script>console.error('Error: boom')</script>")
        self.pages(("err", "/err.html"))
        r = harness(self.root, "see")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("boom", r.stdout)
        self.assertIn("missing.png", r.stdout)

    def test_script_error_and_missing_page_fail(self):
        self.pages(("broken", "/broken.html"), ("missing", "/nope.html"))
        r = harness(self.root, "see")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("undefinedFn", r.stdout)
        self.assertIn("HTTP 404", r.stdout)


if __name__ == "__main__":
    unittest.main()


class BaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = new_repo()
        r = run_install(self.root, "--preset", "generic", "--verify", "python3 check.py", "--lint", "--lang", "en")
        self.assertEqual(r.returncode, 0, r.stderr)
        set_cfg(self.root, lint={"rules": [RULE]})
        write(self.root, "ui/old.js", "const left = refund - 1500;\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_known_violations_pass_new_ones_fail(self):
        self.assertEqual(harness(self.root, "lint", "--baseline").returncode, 0)
        r = harness(self.root, "lint")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("1 known violations", r.stdout)
        write(self.root, "ui/new.js", "const x = fee * 2;\n")
        r = harness(self.root, "lint")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ui/new.js", r.stdout)
        self.assertNotIn("ui/old.js", r.stdout)

    def test_agent_cannot_rebaseline(self):
        r = harness(self.root, "check", "--bash", "python3 .codex/harness/harness.py lint --baseline")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        r = harness(self.root, "check", "--bash", "python3 .codex/harness/harness.py lint")
        self.assertEqual(r.returncode, 0, r.stdout)


class BaselineDirectTest(BaselineTest):
    def test_agent_cannot_rebaseline(self):
        r = harness(self.root, "check", "--bash", "python3 .codex/harness/lint.py --baseline")
        self.assertNotEqual(r.returncode, 0, r.stdout)
