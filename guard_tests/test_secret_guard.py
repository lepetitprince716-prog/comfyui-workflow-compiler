"""The second secret scan (Betterleaks) as wired into the git hooks.

Each test builds a throwaway repository in a temporary directory with the real hooks and the real
tools/secret_scan.sh, and drives it with real git commands. The first scan (tools/privacy_scan.py)
is replaced by a stub in most tests so that a refusal can only come from Betterleaks: the real
privacy scanner also knows the GitHub token shape and would refuse the same input.

The fake secret is built at run time from random characters in the shape of a GitHub personal
access token, so no secret-looking literal exists in the repository. It lives only in the
temporary directory.

Betterleaks must be installed (brew install betterleaks). Missing, the tests that need it fail
instead of skipping: a skipped guard test reads as green while the guard is not running. The test
for the missing-tool case does not need it.
"""

import os
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ("pre-commit", "commit-msg", "pre-push")
STUB_PASS = "import sys\nsys.exit(0)\n"
STUB_FAIL = "import sys\nsys.stderr.write('FIRST-SCAN-REFUSED\\n')\nsys.exit(1)\n"
IDENTITY = "1+tester@users.noreply.github.com"


def random_text(length):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def fake_secret():
    """The shape of a GitHub personal access token."""
    return "ghp_" + random_text(36)


def fake_registry_token():
    """A token shape that the privacy scan's own rules do not know (checked 2026-11-18)."""
    return "np" + "m_" + random_text(36)


class GuardedRepo:
    """A temporary repository with the real hooks and a chosen first scan."""

    def __init__(self, testcase, first_scan):
        self.base = Path(tempfile.mkdtemp(prefix="secret-guard-"))
        testcase.addCleanup(shutil.rmtree, self.base, True)
        self.home = self.base / "home"
        self.home.mkdir()
        self.path = self.base / "work"
        self.remote = self.base / "remote.git"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_OVERSIGHT_")}
        self.env["HOME"] = str(self.home)
        self.env["GIT_CONFIG_GLOBAL"] = os.devnull
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.git("init", "-q", "-b", "main", str(self.path), cwd=self.base)
        self.git("init", "-q", "--bare", str(self.remote), cwd=self.base)
        for key, value in (
            ("user.email", IDENTITY),
            ("user.name", "tester"),
            ("core.hooksPath", ".githooks"),
        ):
            self.git("config", key, value)
        self.git("remote", "add", "origin", str(self.remote))
        shutil.copytree(ROOT / ".githooks", self.path / ".githooks")
        tools = self.path / "tools"
        tools.mkdir()
        for name in ("secret_scan.sh", "secret_scan.tmpl"):
            shutil.copy2(ROOT / "tools" / name, tools / name)
        if first_scan == "real":
            for name in ("privacy_scan.py", "privacy_rules.json"):
                shutil.copy2(ROOT / "tools" / name, tools / name)
        else:
            (tools / "privacy_scan.py").write_text(STUB_FAIL if first_scan == "fail" else STUB_PASS)

    def run(self, *args, env=None, cwd=None, input=None):
        return subprocess.run(
            args,
            cwd=cwd or self.path,
            env=env or self.env,
            input=input,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def git(self, *args, cwd=None):
        result = self.run("git", *args, cwd=cwd)
        if result.returncode != 0:
            raise AssertionError("git %s failed: %s" % (" ".join(args), result.stderr))
        return result

    def write(self, name, text):
        (self.path / name).write_text(text)

    def stage(self, name, text):
        self.write(name, text)
        self.git("add", name)

    def commit(self, message):
        return self.run("git", "commit", "-q", "-m", message)

    def commit_unchecked(self, message):
        """Make a commit that the hooks never see, to put a secret into history."""
        return self.git("-c", "core.hooksPath=" + os.devnull, "commit", "-q", "-m", message)

    def push(self):
        return self.run("git", "push", "-q", "origin", "main")


def combined(result):
    return result.stdout + result.stderr


class SecretGuardTests(unittest.TestCase):
    def need_betterleaks(self):
        if shutil.which("betterleaks") is None:
            self.fail("betterleaks is not installed: brew install betterleaks")

    def assert_refused_by_betterleaks(self, result, secret):
        self.assertNotEqual(result.returncode, 0, combined(result))
        out = combined(result)
        self.assertIn("secret_scan:", out)
        self.assertIn("github-pat", out)
        self.assertNotIn(secret, out)

    def test_pre_commit_refuses_a_staged_secret(self):
        self.need_betterleaks()
        repo = GuardedRepo(self, "pass")
        secret = fake_secret()
        repo.stage("config.txt", "token = %s\n" % secret)
        result = repo.commit("add config")
        self.assert_refused_by_betterleaks(result, secret)
        self.assertIn("config.txt:1", combined(result))
        self.assertNotEqual(repo.run("git", "diff", "--cached", "--quiet").returncode, 0)
        self.assertNotEqual(repo.run("git", "rev-parse", "-q", "--verify", "HEAD").returncode, 0)

    def test_commit_msg_refuses_a_secret_in_the_message(self):
        self.need_betterleaks()
        repo = GuardedRepo(self, "pass")
        secret = fake_secret()
        repo.stage("notes.txt", "nothing secret here\n")
        result = repo.commit("add notes, token %s" % secret)
        self.assert_refused_by_betterleaks(result, secret)
        self.assertNotEqual(repo.run("git", "rev-parse", "-q", "--verify", "HEAD").returncode, 0)

    def test_pre_push_refuses_a_secret_in_history(self):
        self.need_betterleaks()
        repo = GuardedRepo(self, "pass")
        secret = fake_secret()
        repo.stage("ok.txt", "clean\n")
        self.assertEqual(repo.commit("clean start").returncode, 0)
        repo.stage("config.txt", "token = %s\n" % secret)
        repo.commit_unchecked("slip")
        repo.stage("config.txt", "token = removed\n")
        self.assertEqual(repo.commit("remove it again").returncode, 0)
        result = repo.push()
        self.assert_refused_by_betterleaks(result, secret)
        self.assertEqual(repo.run("git", "-C", str(repo.remote), "branch", "--list").stdout.strip(), "")

    def test_pre_push_refuses_a_secret_only_in_a_commit_message(self):
        self.need_betterleaks()
        repo = GuardedRepo(self, "pass")
        secret = fake_secret()
        repo.stage("ok.txt", "clean\n")
        repo.commit_unchecked("note the token %s" % secret)
        result = repo.push()
        self.assert_refused_by_betterleaks(result, secret)

    def test_a_clean_commit_passes_all_three_hooks_with_the_real_privacy_scan(self):
        self.need_betterleaks()
        repo = GuardedRepo(self, "real")
        repo.stage("README.md", "A clean file.\n")
        committed = repo.commit("add a clean file")
        self.assertEqual(committed.returncode, 0, combined(committed))
        pushed = repo.push()
        self.assertEqual(pushed.returncode, 0, combined(pushed))
        self.assertIn("main", repo.run("git", "-C", str(repo.remote), "branch", "--list").stdout)

    def test_a_secret_the_privacy_scan_misses_is_still_refused_with_the_real_privacy_scan(self):
        self.need_betterleaks()
        repo = GuardedRepo(self, "real")
        token = fake_registry_token()
        repo.stage("deploy.txt", "value: %s\n" % token)
        result = repo.commit("add deploy notes")
        out = combined(result)
        self.assertNotEqual(result.returncode, 0, out)
        self.assertIn("privacy_scan: clean", out)
        self.assertIn("secret_scan:", out)
        self.assertIn("deploy.txt:1", out)
        self.assertNotIn(token, out)

    def test_without_betterleaks_all_three_hooks_fail_and_say_how_to_install(self):
        repo = GuardedRepo(self, "pass")
        repo.stage("ok.txt", "clean\n")
        repo.commit_unchecked("start")
        repo.stage("more.txt", "also clean\n")
        message = repo.path / "msg.txt"
        message.write_text("a clean message\n")
        shim = repo.base / "bin"
        shim.mkdir()
        for name, target in (
            ("git", shutil.which("git")),
            ("python3", sys.executable),
            ("dirname", shutil.which("dirname")),
            ("sh", "/bin/sh"),
        ):
            self.assertIsNotNone(target, name)
            (shim / name).symlink_to(target)
        env = dict(repo.env, PATH=str(shim))
        self.assertIsNone(shutil.which("betterleaks", path=env["PATH"]))
        for hook, args in (("pre-commit", ()), ("commit-msg", (str(message),)), ("pre-push", ())):
            with self.subTest(hook=hook):
                result = repo.run(str(repo.path / ".githooks" / hook), *args, env=env)
                self.assertNotEqual(result.returncode, 0, combined(result))
                self.assertIn("brew install betterleaks", combined(result))

    def test_a_failing_first_scan_still_fails_every_hook(self):
        self.need_betterleaks()
        repo = GuardedRepo(self, "fail")
        repo.stage("ok.txt", "clean\n")
        message = repo.path / "msg.txt"
        message.write_text("a clean message\n")
        for hook, args in (("pre-commit", ()), ("commit-msg", (str(message),)), ("pre-push", ())):
            with self.subTest(hook=hook):
                result = repo.run(str(repo.path / ".githooks" / hook), *args)
                self.assertNotEqual(result.returncode, 0, combined(result))
                self.assertIn("FIRST-SCAN-REFUSED", combined(result))

    def test_false_positives_can_be_allowed_inline_or_by_fingerprint(self):
        self.need_betterleaks()
        repo = GuardedRepo(self, "pass")
        secret = fake_secret()
        repo.stage("inline.txt", "token = %s  # betterleaks:allow\n" % secret)
        inline = repo.commit("allowed inline")
        self.assertEqual(inline.returncode, 0, combined(inline))
        repo.stage("listed.txt", "token = %s\n" % secret)
        refused = repo.commit("not yet listed")
        self.assert_refused_by_betterleaks(refused, secret)
        repo.stage(".betterleaksignore", "listed.txt:github-pat:1\n")
        listed = repo.commit("listed by fingerprint")
        self.assertEqual(listed.returncode, 0, combined(listed))

    def test_the_wrapper_rejects_bad_usage(self):
        repo = GuardedRepo(self, "pass")
        for args in ((), ("bogus",), ("message",), ("message", "/nonexistent/file"), ("staged", "extra")):
            with self.subTest(args=args):
                result = repo.run("sh", "tools/secret_scan.sh", *args)
                self.assertEqual(result.returncode, 2, combined(result))


if __name__ == "__main__":
    unittest.main()
