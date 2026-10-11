"""Stop hook and installer, exercised end to end in temporary git repositories."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
INSTALL = os.path.join(KIT, "install.py")

CHECK = """import sys, pathlib
state = pathlib.Path('state.txt').read_text().strip()
print('checking state', state)
if state == 'secret':
    print('leaked ' + 'sk_' + 'live_' + 'abcdefghijklmnop1234')
if state == 'slow':
    import time; time.sleep(5)
sys.exit(0 if state == 'ok' else 1)
"""


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def new_repo():
    tmp = tempfile.TemporaryDirectory()
    root = os.path.realpath(tmp.name)
    git(root, "init", "-q")
    os.makedirs(os.path.join(root, "tests"))
    with open(os.path.join(root, "tests", "check.sh"), "w") as f:
        f.write("true\n")
    with open(os.path.join(root, "check.py"), "w") as f:
        f.write(CHECK)
    with open(os.path.join(root, "state.txt"), "w") as f:
        f.write("ok\n")
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return tmp, root


def run_install(root, *extra):
    return subprocess.run([sys.executable, INSTALL, root, *extra], capture_output=True, text=True)


def hook_command(root, event):
    with open(os.path.join(root, ".codex", "hooks.json")) as f:
        data = json.load(f)
    for group in data["hooks"][event]:
        for h in group["hooks"]:
            if ".codex/harness/" in h["command"]:
                return h["command"]
    raise AssertionError(f"no harness hook for {event}")


def call_hook(root, event, payload, cwd=None):
    """Run the hook exactly as hooks.json says, through a shell, like Codex does."""
    proc = subprocess.run(["/bin/sh", "-c", hook_command(root, event)], input=json.dumps(payload),
                          capture_output=True, text=True, cwd=cwd or root, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout or "{}")


def set_state(root, value):
    with open(os.path.join(root, "state.txt"), "w") as f:
        f.write(value + "\n")


def edit_config(root, fn):
    path = os.path.join(root, ".codex", "harness", "harness.json")
    with open(path) as f:
        cfg = json.load(f)
    fn(cfg)
    with open(path, "w") as f:
        json.dump(cfg, f)


class VerifyHook(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = new_repo()
        r = run_install(self.root, "--preset", "generic", "--lang", "en", "--verify", "python3 check.py")
        self.assertEqual(r.returncode, 0, r.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def stop(self, active=False, turn="t1"):
        return call_hook(self.root, "Stop", {"hook_event_name": "Stop", "turn_id": turn, "stop_hook_active": active,
                                             "cwd": self.root, "session_id": "s"})

    def log(self):
        path = os.path.join(self.root, ".codex", "harness", "logs", "events.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f]

    def test_pass_then_skip_when_unchanged(self):
        self.assertEqual(self.stop(), {})
        self.assertEqual(self.stop(turn="t2"), {})
        decisions = [e["decision"] for e in self.log() if e["hook"] == "verify"]
        self.assertEqual(decisions, ["pass"], "second stop should skip without running")

    def test_runs_again_after_a_change(self):
        self.stop()
        with open(os.path.join(self.root, "new.txt"), "w") as f:
            f.write("x")
        self.stop(turn="t2")
        self.assertEqual([e["decision"] for e in self.log() if e["hook"] == "verify"], ["pass", "pass"])

    def test_failure_blocks_then_gives_up(self):
        set_state(self.root, "broken")
        first = self.stop()
        self.assertEqual(first.get("decision"), "block")
        self.assertIn("python3 check.py", first["reason"])
        self.assertIn("checking state broken", first["reason"])
        second = self.stop(active=True)
        self.assertEqual(second.get("decision"), "block")
        third = self.stop(active=True)
        self.assertNotIn("decision", third)
        self.assertIn("failed 3 times", third["systemMessage"])
        decisions = [e["decision"] for e in self.log() if e["hook"] == "verify"]
        self.assertEqual(decisions, ["block", "block", "give_up"])

    def test_blocked_handoff_is_respected(self):
        set_state(self.root, "broken")
        out = call_hook(self.root, "Stop", {"hook_event_name": "Stop", "turn_id": "t1", "stop_hook_active": False,
                                            "last_assistant_message": "BLOCKED: the test conflicts with the request.",
                                            "cwd": self.root, "session_id": "s"})
        self.assertNotIn("decision", out)
        self.assertIn("BLOCKED", out["systemMessage"])
        self.assertEqual([e["decision"] for e in self.log() if e["hook"] == "verify"], ["handed_off"])

    def test_new_chain_resets_the_counter(self):
        set_state(self.root, "broken")
        self.stop()
        self.stop(active=True)
        self.stop(active=True)
        self.assertEqual(self.stop(active=False, turn="t9").get("decision"), "block")

    def test_observe_mode_never_blocks(self):
        edit_config(self.root, lambda c: c.update(mode="observe"))
        set_state(self.root, "broken")
        out = self.stop()
        self.assertNotIn("decision", out)
        self.assertIn("observe", out["systemMessage"])

    def test_secrets_in_output_are_redacted(self):
        set_state(self.root, "secret")
        out = self.stop()
        self.assertIn("[REDACTED:stripe-live-key]", out["reason"])
        self.assertNotIn("sk_" + "live_abcdefghijklmnop1234", out["reason"])

    def test_timeout(self):
        edit_config(self.root, lambda c: c["verify"]["commands"][0].update(timeout=1))
        set_state(self.root, "slow")
        out = self.stop()
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("timed out", out["reason"])

    def test_broken_config_does_not_crash(self):
        with open(os.path.join(self.root, ".codex", "harness", "harness.json"), "w") as f:
            f.write("{ not json")
        out = self.stop()
        self.assertIn("error", out["systemMessage"])


class Backstop(unittest.TestCase):
    """Protected files changed by something the guard cannot see (a script, an MCP tool)."""

    def setUp(self):
        self.tmp, self.root = new_repo()
        run_install(self.root, "--preset", "generic", "--lang", "en", "--verify", "python3 check.py")

    def tearDown(self):
        self.tmp.cleanup()

    def test_change_during_turn_is_reported_once(self):
        call_hook(self.root, "UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "prompt": "x", "cwd": self.root})
        with open(os.path.join(self.root, "tests", "check.sh"), "a") as f:
            f.write("exit 0\n")  # as if a script the agent ran had done it
        first = call_hook(self.root, "Stop", {"turn_id": "t", "stop_hook_active": False, "cwd": self.root})
        self.assertEqual(first.get("decision"), "block")
        self.assertIn("tests/check.sh", first["reason"])
        call_hook(self.root, "UserPromptSubmit", {"prompt": "continuation", "cwd": self.root})  # must not reset
        second = call_hook(self.root, "Stop", {"turn_id": "t", "stop_hook_active": True, "cwd": self.root})
        self.assertNotIn("decision", second)

    def test_human_edits_before_the_turn_are_not_reported(self):
        with open(os.path.join(self.root, "tests", "check.sh"), "a") as f:
            f.write("# human edit\n")
        call_hook(self.root, "UserPromptSubmit", {"prompt": "x", "cwd": self.root})
        self.assertEqual(call_hook(self.root, "Stop", {"turn_id": "t", "stop_hook_active": False, "cwd": self.root}), {})


class VerifyEdges(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = new_repo()

    def tearDown(self):
        self.tmp.cleanup()

    def stop(self, message=""):
        return call_hook(self.root, "Stop", {"turn_id": "t", "stop_hook_active": False, "cwd": self.root,
                                            "last_assistant_message": message})

    def test_missing_runner_is_an_environment_problem(self):
        run_install(self.root, "--preset", "generic", "--verify", "definitely-not-a-command-xyz")
        out = self.stop()
        self.assertNotIn("decision", out)
        self.assertIn("환경", out["systemMessage"])
        run_install(self.root, "--force", "--preset", "generic", "--verify", "python3 -m no_such_module_xyz")
        self.assertNotIn("decision", self.stop())

    def test_blocked_must_start_a_line(self):
        run_install(self.root, "--preset", "generic", "--lang", "en", "--verify", "python3 check.py")
        set_state(self.root, "broken")
        self.assertEqual(self.stop("I was not BLOCKED by anything.").get("decision"), "block")
        self.assertNotIn("decision", self.stop("**BLOCKED:** the test conflicts with the request"))

    def test_total_budget(self):
        run_install(self.root, "--preset", "generic", "--lang", "en", "--verify", "python3 check.py")
        edit_config(self.root, lambda c: c["verify"].update(total_timeout=10))
        edit_config(self.root, lambda c: c["verify"]["commands"].insert(0, {"name": "slow", "run": "python3 -c 'import time; time.sleep(30)'", "timeout": 300}))
        out = self.stop()
        self.assertRegex(out["reason"], r"timed out after (9|10)s")


class GuardThroughHooksJson(unittest.TestCase):
    def test_runs_from_a_subfolder_and_fails_closed(self):
        tmp, root = new_repo()
        try:
            run_install(root, "--preset", "generic", "--lang", "en")
            sub = os.path.join(root, "tests")
            payload = {"tool_name": "Bash", "tool_input": {"command": "rm check.sh"}, "cwd": sub}
            out = call_hook(root, "PreToolUse", payload, cwd=sub)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
            ok = call_hook(root, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": root})
            self.assertEqual(ok, {})
            with open(os.path.join(root, ".codex", "harness", "harness.json"), "w") as f:
                f.write("[]")
            broken = call_hook(root, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": root})
            self.assertEqual(broken["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertIn("doctor", broken["hookSpecificOutput"]["permissionDecisionReason"])
            log = os.path.join(root, ".codex", "harness", "logs", "events.jsonl")
            with open(log) as f:
                text = f.read()
            self.assertIn('"decision": "deny"', text)
        finally:
            tmp.cleanup()


class Installer(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = new_repo()

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, rel):
        with open(os.path.join(self.root, rel)) as f:
            return f.read()

    def test_dry_run_writes_nothing(self):
        r = run_install(self.root, "--dry-run")
        self.assertIn("DRY RUN", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".codex")))

    def test_idempotent_and_keeps_user_content(self):
        os.makedirs(os.path.join(self.root, ".codex"))
        other = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]}}
        with open(os.path.join(self.root, ".codex", "hooks.json"), "w") as f:
            json.dump(other, f)
        with open(os.path.join(self.root, "AGENTS.md"), "w") as f:
            f.write("# My rules\n\nKeep this.\n")
        run_install(self.root)
        run_install(self.root)
        hooks = json.loads(self.read(".codex/hooks.json"))["hooks"]
        commands = [h["command"] for g in hooks["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(commands.count("echo mine"), 1)
        self.assertEqual(sum(".codex/harness/guard.py" in c for c in commands), 1)
        self.assertEqual(len(hooks["Stop"]), 1)
        agents = self.read("AGENTS.md")
        self.assertIn("Keep this.", agents)
        self.assertEqual(agents.count("codex-harness-kit:start"), 1)
        self.assertTrue(any(n.startswith("hooks.json.bak-") for n in os.listdir(os.path.join(self.root, ".codex"))))
        self.assertEqual(self.read(".gitignore").count(".codex/harness/logs/"), 1)

    def test_keeps_existing_config_unless_forced(self):
        run_install(self.root, "--verify", "python3 check.py")
        edit_config(self.root, lambda c: c.update(lang="en"))
        run_install(self.root)
        cfg = json.loads(self.read(".codex/harness/harness.json"))
        self.assertEqual(cfg["lang"], "en")
        run_install(self.root, "--force")
        self.assertEqual(json.loads(self.read(".codex/harness/harness.json"))["lang"], "ko")

    def test_uninstall(self):
        os.makedirs(os.path.join(self.root, ".codex"))
        other = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
        with open(os.path.join(self.root, ".codex", "hooks.json"), "w") as f:
            json.dump(other, f)
        run_install(self.root)
        run_install(self.root, "--uninstall")
        hooks = json.loads(self.read(".codex/hooks.json"))["hooks"]
        self.assertEqual(hooks, other["hooks"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "AGENTS.md")), "AGENTS.md only held the harness block")
        self.assertFalse(os.path.exists(os.path.join(self.root, ".codex", "harness", "guard.py")))
        self.assertTrue(os.path.exists(os.path.join(self.root, ".codex", "harness", "harness.json")))
        run_install(self.root, "--uninstall", "--purge")
        self.assertFalse(os.path.exists(os.path.join(self.root, ".codex", "harness")))

    def test_invalid_input_changes_nothing(self):
        os.makedirs(os.path.join(self.root, ".codex"))
        with open(os.path.join(self.root, ".codex", "hooks.json"), "w") as f:
            f.write('{"hooks": {')
        r = run_install(self.root)
        self.assertEqual(r.returncode, 2)
        self.assertIn("nothing was changed", r.stderr)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, ".codex"))), ["hooks.json"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "AGENTS.md")))
        with open(os.path.join(self.root, ".codex", "hooks.json"), "w") as f:
            f.write('{"hooks": {"Stop": {}}}')
        self.assertEqual(run_install(self.root).returncode, 2)
        self.assertEqual(run_install(self.root, "--verify", "").returncode, 2)
        self.assertEqual(run_install(self.root, "--purge").returncode, 2)

    def test_reinstall_without_changes_writes_nothing(self):
        run_install(self.root, "--verify", "python3 check.py")
        r = run_install(self.root)
        self.assertIn("nothing to change", r.stdout)
        self.assertNotIn("trust the hooks", r.stdout)

    def test_subfolder_install_still_finds_its_scripts(self):
        sub = os.path.join(self.root, "apps", "web")
        os.makedirs(os.path.join(sub, "tests"))
        run_install(sub, "--preset", "generic", "--lang", "en")
        deep = os.path.join(sub, "tests")
        out = call_hook(sub, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "rm -rf ../tests"}, "cwd": deep}, cwd=deep)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        elsewhere = call_hook(sub, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, cwd=self.root)
        self.assertEqual(elsewhere, {})  # outside the project the hook answers {} instead of failing

    def test_purge_cleans_up_generated_files(self):
        run_install(self.root, "--verify", "python3 check.py")
        run_install(self.root, "--uninstall", "--purge")
        for rel in (".codex", "AGENTS.md", "docs/harness.md", ".gitignore"):
            self.assertFalse(os.path.exists(os.path.join(self.root, rel)), rel)

    def test_ci_command(self):
        run_install(self.root, "--verify", "python3 check.py")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "harness")
        git(self.root, "checkout", "-qb", "feature")
        with open(os.path.join(self.root, "tests", "check.sh"), "a") as f:
            f.write("exit 0\n")
        git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "loosen")
        r = subprocess.run([sys.executable, ".codex/harness/harness.py", "ci", "--base", "master"], cwd=self.root, capture_output=True, text=True)
        if "unknown revision" in r.stderr:
            r = subprocess.run([sys.executable, ".codex/harness/harness.py", "ci", "--base", "main"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("tests/check.sh", r.stdout)

    def test_preset_detection_and_verify_commands(self):
        with open(os.path.join(self.root, "package.json"), "w") as f:
            json.dump({"scripts": {"test": "vitest", "lint": "eslint ."}}, f)
        with open(os.path.join(self.root, "tsconfig.json"), "w") as f:
            f.write("{}")
        run_install(self.root)
        cfg = json.loads(self.read(".codex/harness/harness.json"))
        self.assertIn("tests/", cfg["protect"]["paths"])
        runs = [c["run"] for c in cfg["verify"]["commands"]]
        self.assertEqual(runs, ["npm test --silent", "npx --no-install tsc --noEmit", "npm run lint --silent"])

    def test_installed_config_has_no_key_shaped_text(self):
        run_install(self.root, "--verify", "true")
        raw = self.read(".codex/harness/harness.json")
        import re
        self.assertIsNone(re.search(r"(?:sk|rk)_live_|live_(?:sk|ck)_|-----BEGIN [A-Z ]+PRIVATE KEY", raw))
        r = subprocess.run([sys.executable, ".codex/harness/harness.py", "test"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_vitest_without_a_test_script(self):
        with open(os.path.join(self.root, "package.json"), "w") as f:
            json.dump({"scripts": {"typecheck": "tsc --noEmit"}, "devDependencies": {"vitest": "^3.0.0"}}, f)
        run_install(self.root)
        runs = [c["run"] for c in json.loads(self.read(".codex/harness/harness.json"))["verify"]["commands"]]
        self.assertEqual(runs, ["npx --no-install vitest run", "npm run typecheck --silent"])

    def test_doctor_and_examples_after_install(self):
        run_install(self.root, "--verify", "python3 check.py")
        for cmd in ("doctor", "test"):
            r = subprocess.run([sys.executable, ".codex/harness/harness.py", cmd], cwd=self.root, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
