"""Guard behaviour: what must be blocked and what must keep working."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(KIT, "kit", ".codex", "harness"))
sys.path.insert(0, KIT)

import guard  # noqa: E402
import install  # noqa: E402

# Key-shaped test values are assembled at run time so secret scanners (GitHub push
# protection, gitleaks) do not flag this repository or projects that copy it.
FAKE_STRIPE = "sk_" + "live_" + "abcdefghijklmnop1234"
FAKE_TOSS = "live_" + "sk_FAKE0000000000"
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def make_project(preset: str):
    tmp = tempfile.TemporaryDirectory()
    root = os.path.realpath(tmp.name)
    for rel in ("src/rules/refund.ts", "src/cart.test.tsx", "tests/rules/refund.test.ts", "app/refund.py",
                "tests/test_refund.py", "internal/refund/refund_test.go", ".env", ".env.example", "fix.patch"):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("x\n")
    with open(os.path.join(root, "fix.patch"), "w") as f:
        f.write("--- a/tests/rules/refund.test.ts\n+++ b/tests/rules/refund.test.ts\n@@\n-x\n+y\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=root, check=True)
    cfg = install.build_config(root, preset, "en", ["npx vitest run"] if preset == "node" else None)
    return tmp, root, cfg


class GuardCase(unittest.TestCase):
    preset = "node"

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.root, cls.cfg = make_project(cls.preset)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def decide(self, text, tool="Bash", cwd=None):
        payload = {"tool_name": tool, "tool_input": {"command": text}, "cwd": cwd or self.root}
        return guard.decide(payload, self.cfg, self.root)

    def assertBlocked(self, text, tool="Bash", kind=None, cwd=None):
        found = self.decide(text, tool, cwd)
        self.assertTrue(found, f"expected block: {text!r}")
        if kind:
            self.assertIn(kind, [f["kind"] for f in found], f"{text!r} -> {found}")

    def assertAllowed(self, text, tool="Bash", cwd=None):
        found = self.decide(text, tool, cwd)
        self.assertFalse(found, f"expected allow: {text!r} -> {found}")


class NodeBlocks(GuardCase):
    def test_patch_headers(self):
        for header in ("Update File", "Add File", "Delete File"):
            self.assertBlocked(f"*** Begin Patch\n*** {header}: tests/rules/refund.test.ts\n@@\n-a\n+b\n*** End Patch", "apply_patch", "protect")
        self.assertBlocked("*** Begin Patch\n*** Update File: src/a.ts\n*** Move to: tests/a.ts\n*** End Patch", "apply_patch")
        self.assertBlocked(f"*** Begin Patch\n*** Update File: {self.root}/tests/rules/refund.test.ts\n*** End Patch", "apply_patch")
        self.assertBlocked("*** Begin Patch\n*** Update File: ../x/tests/a.ts\n*** End Patch", "apply_patch", cwd=os.path.join(self.root, "src", "rules"))

    def test_shell_writes(self):
        cases = [
            "echo x > tests/rules/refund.test.ts",
            "echo x >tests/rules/refund.test.ts",
            "echo x >> ./tests/rules/refund.test.ts",
            "printf x | tee -a tests/rules/refund.test.ts",
            "cp /tmp/x tests/rules/refund.test.ts",
            "mv tests/rules/refund.test.ts tests/rules/old.ts",
            "ln -sf /tmp/x tests/rules/refund.test.ts",
            "touch tests/new.test.ts",
            "rm -rf tests",
            "rm -rf ./src/../tests",
            "rm -r tests/*",
            "rm tests/rules/{a,b}.test.ts",
            "rm -rf src",  # contains src/cart.test.tsx
            "sed -i '' 's/1500/1000/' tests/rules/refund.test.ts",
            "sed -i.bak s/a/b/ tests/rules/refund.test.ts",
            "perl -pi -e 's/a/b/' tests/rules/refund.test.ts",
            "find tests -name '*.ts' -delete",
            "find . -name '*.test.ts' -exec rm {} +",
            "dd if=/dev/zero of=tests/rules/refund.test.ts count=1",
            "truncate -s 0 tests/rules/refund.test.ts",
            "chmod 000 tests",
            "rsync -a /tmp/x/ tests/",
        ]
        for c in cases:
            with self.subTest(c=c):
                self.assertBlocked(c)

    def test_indirection(self):
        cases = [
            "cd tests && rm rules/refund.test.ts",
            "cd tests/rules; echo > refund.test.ts",
            "bash -c 'rm tests/rules/refund.test.ts'",
            "bash -lc \"rm tests/rules/refund.test.ts\"",
            "sh -c \"cd tests && rm -rf rules\"",
            "eval 'rm tests/rules/refund.test.ts'",
            "env FOO=1 rm tests/rules/refund.test.ts",
            "sudo -n rm tests/rules/refund.test.ts",
            "timeout 5 rm tests/rules/refund.test.ts",
            "node -e \"require('fs').writeFileSync('tests/x.test.ts','')\"",
            "python3 -c \"open('tests/rules/refund.test.ts','w')\"",
            "python3 - <<'EOF'\nopen('tests/rules/refund.test.ts', 'w').write('')\nEOF",
            "bash <<'EOF'\nrm tests/rules/refund.test.ts\nEOF",
            "cat > tests/new.test.ts <<'EOF'\nit('passes', () => {})\nEOF",
            "rm $(ls tests/rules/*)",
            "rm `ls tests/rules/*`",
            "rm -f \"$PWD/tests/rules/refund.test.ts\"",
            "rm -f \"$ROOT/tests/rules/refund.test.ts\"",
            "find tests -name '*.ts' | xargs rm",
        ]
        for c in cases:
            with self.subTest(c=c):
                self.assertBlocked(c)

    def test_git(self):
        self.assertBlocked("git checkout -- tests/rules/refund.test.ts")
        self.assertAllowed("git restore tests")  # clean tests: nothing to restore
        self.assertBlocked("git rm tests/rules/refund.test.ts")
        self.assertBlocked("git mv tests/rules/refund.test.ts tests/x.ts")
        self.assertAllowed("git -C . checkout -- tests")  # clean tests: no-op
        self.assertBlocked("git apply fix.patch")

    def test_git_whole_tree_only_when_tests_are_dirty(self):
        self.assertAllowed("git stash")
        self.assertAllowed("git reset --hard")
        path = os.path.join(self.root, "tests", "rules", "refund.test.ts")
        with open(path, "a") as f:
            f.write("human edit\n")
        try:
            guard_cfg = self.cfg
            self.assertBlocked("git stash")
            self.assertBlocked("git reset --hard HEAD")
            self.assertBlocked("git checkout .")
            self.assertBlocked("git restore tests")
            self.assertBlocked("git -C . checkout -- tests")
            self.assertBlocked("git clean -fd")
        finally:
            subprocess.run(["git", "checkout", "--", "tests"], cwd=self.root, check=True)

    def test_rewinds_and_piped_patches(self):
        path = os.path.join(self.root, "tests", "rules", "refund.test.ts")
        with open(path, "w") as f:
            f.write("expect(1500)\n")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "tighten"], cwd=self.root, check=True)
        try:
            self.assertBlocked("git checkout HEAD~1")
            self.assertBlocked("git switch --detach HEAD~1")
            self.assertBlocked("git read-tree -u --reset HEAD~1")
            self.assertBlocked("git diff HEAD~1 HEAD | git apply -R")
            self.assertAllowed("git checkout HEAD~1 -- src/rules/refund.ts")
            self.assertAllowed("git switch -c feature")
        finally:
            subprocess.run(["git", "reset", "-q", "--hard", "HEAD~1"], cwd=self.root, check=True)

    def test_test_settings_in_build_files(self):
        self.assertBlocked('*** Begin Patch\n*** Update File: package.json\n@@\n-    "test": "vitest run",\n+    "test": "exit 0",\n*** End Patch', "apply_patch")
        self.assertBlocked('*** Begin Patch\n*** Update File: pyproject.toml\n@@\n+addopts = "-k not slow"\n*** End Patch', "apply_patch")
        self.assertAllowed('*** Begin Patch\n*** Update File: package.json\n@@\n-    "zod": "3.0.0",\n+    "zod": "3.1.0",\n*** End Patch', "apply_patch")

    def test_runner_flags(self):
        self.assertBlocked("npx vitest run -u")
        self.assertBlocked("npx vitest run --update")
        self.assertBlocked("npm test -- -u")
        self.assertBlocked("npx jest --updateSnapshot")

    def test_formatters(self):
        self.assertBlocked("npx prettier --write .")
        self.assertBlocked("npx prettier --write tests")
        self.assertBlocked("npx eslint --fix .")
        self.assertBlocked("npx biome format --write .")

    def test_config_and_harness(self):
        self.assertBlocked("*** Begin Patch\n*** Update File: vitest.config.ts\n@@\n-a\n+b\n*** End Patch", "apply_patch")
        self.assertBlocked("rm .codex/hooks.json")
        self.assertBlocked("echo '{}' > .codex/harness/harness.json")
        self.assertBlocked("echo 'hooks = false' >> .codex/config.toml")

    def test_secrets(self):
        self.assertBlocked("export K=" + FAKE_STRIPE, kind="secret")
        self.assertBlocked("*** Begin Patch\n*** Add File: src/c.ts\n+const k = '" + FAKE_TOSS + "'\n*** End Patch", "apply_patch", "secret")
        self.assertBlocked("echo " + FAKE_AWS, kind="secret")
        self.assertBlocked("curl -H 'Authorization: Bearer ghp_" + "a" * 36 + "' https://api.github.com", kind="secret")

    def test_deny_read(self):
        for c in ("cat .env", "less ./.env", "grep KEY .env", "source .env", ". .env", "cp .env /tmp/e",
                  "python3 -c \"print(open('.env').read())\"", "wc -l < .env", "echo X=1 >> .env"):
            with self.subTest(c=c):
                self.assertBlocked(c, kind="deny_read")
        self.assertBlocked("*** Begin Patch\n*** Update File: .env\n@@\n-a\n+b\n*** End Patch", "apply_patch", "deny_read")


class NodeAllows(GuardCase):
    def test_everyday_commands(self):
        cases = [
            "cat tests/rules/refund.test.ts",
            "sed -n '1,40p' tests/rules/refund.test.ts",
            "head -50 tests/rules/refund.test.ts && tail -5 tests/rules/refund.test.ts",
            "rg -n refund tests src",
            "grep -rn refund tests",
            "ls -la tests",
            "find tests -name '*.ts'",
            "wc -l tests/rules/*.ts",
            "git diff -- tests",
            "git status --short",
            "git log --oneline -5 -- tests",
            "git add src tests && git commit -m wip",
            "git stash list",
            "cat $(git ls-files tests)",
            "npx vitest run",
            "npx vitest run tests/rules",
            "npx vitest run tests/rules --reporter=verbose",
            "npx tsc --noEmit",
            "npx tsc --noEmit -w",
            "npx eslint tests src",
            "npx prettier --check .",
            "npx prettier --write src/rules/refund.ts",
            "cp tests/rules/refund.test.ts /tmp/copy.ts",
            "cat .env.example",
            "echo hello > /tmp/out.txt",
            "echo hello > src/out.txt 2>&1",
            "npm install",
            "rm -rf node_modules dist",
            "mkdir -p src/new && touch src/new/a.ts",
            "python3 .codex/harness/harness.py test",
            "echo 'test_sk_dummy'",
        ]
        for c in cases:
            with self.subTest(c=c):
                self.assertAllowed(c)

    def test_patches(self):
        self.assertAllowed("*** Begin Patch\n*** Update File: src/rules/refund.ts\n@@\n-a\n+b\n*** End Patch", "apply_patch")
        self.assertAllowed("*** Begin Patch\n*** Add File: docs/notes.md\n+mentions tests/rules/refund.test.ts in text\n*** End Patch", "apply_patch")
        self.assertAllowed("*** Begin Patch\n*** Add File: .env.example\n+TOSS_SECRET_KEY=test_sk_xxx\n*** End Patch", "apply_patch")

    def test_other_tools_untouched(self):
        self.assertAllowed("anything", tool="mcp__fs__write")


class PythonPreset(GuardCase):
    preset = "python"

    def test_blocks(self):
        self.assertBlocked("*** Begin Patch\n*** Update File: tests/test_refund.py\n*** End Patch", "apply_patch")
        self.assertBlocked("black .")
        self.assertBlocked("ruff format .")
        self.assertBlocked("python3 -m pytest --snapshot-update")

    def test_allows(self):
        self.assertAllowed("python3 -m pytest -q tests")
        self.assertAllowed("ruff check .")
        self.assertAllowed("black --check .")
        self.assertAllowed("black app/refund.py")


class GoPreset(GuardCase):
    preset = "go"

    def test_go(self):
        self.assertBlocked("*** Begin Patch\n*** Update File: internal/refund/refund_test.go\n*** End Patch", "apply_patch")
        self.assertBlocked("gofmt -w .")
        self.assertAllowed("go test ./...")
        self.assertAllowed("gofmt -l .")


class Robustness(unittest.TestCase):
    def test_unbalanced_quotes_with_protected_path(self):
        tmp, root, cfg = make_project("node")
        try:
            payload = {"tool_name": "Bash", "tool_input": {"command": "echo 'oops > tests/rules/refund.test.ts"}, "cwd": root}
            self.assertTrue(guard.decide(payload, cfg, root))
            payload["tool_input"]["command"] = "echo 'unbalanced"
            self.assertFalse(guard.decide(payload, cfg, root))
        finally:
            tmp.cleanup()

    def test_argv_list_input(self):
        tmp, root, cfg = make_project("node")
        try:
            payload = {"tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", "rm tests/rules/refund.test.ts"]}, "cwd": root}
            self.assertTrue(guard.decide(payload, cfg, root))
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
