import os
import unittest
from pathlib import Path

from tests.helpers import make_env, make_fake_muse
from wakeagent import runners
from wakeagent.runners.base import MAX_ARG_BYTES, RunRequest


class MuseAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.cfg = make_env()
        self.r = runners.build("muse", self.cfg.runners["muse"])

    def req(self, mode, prompt="hi", **opts):
        d = self.tmp / "run"
        d.mkdir(exist_ok=True)
        return RunRequest(prompt, self.tmp, 20, mode, d, options=opts)

    def test_probe_reads_version(self):
        p = self.r.probe()
        self.assertTrue(p.ok)
        self.assertIn("0.1.0", p.version)

    def test_smoke_uses_echo_provider(self):
        self.assertTrue(self.r.probe(smoke=True).ok)

    def test_read_only_argv_locks_down(self):
        a = self.r.build_argv("muse", self.req("read-only"), "PROMPT")
        self.assertEqual(a[:3], ["muse", "exec", "--json"])
        for flag in ("--disable-write", "--disable-shell"):
            self.assertIn(flag, a)
        self.assertEqual(a[a.index("--approval-mode") + 1], "untrusted")
        self.assertEqual(a[-1], "PROMPT")

    def test_fix_argv_uses_configured_approval_mode(self):
        a = self.r.build_argv("muse", self.req("fix", fix_approval_mode="never"), "P")
        self.assertEqual(a[a.index("--approval-mode") + 1], "never")
        self.assertNotIn("--disable-write", a)

    def test_provider_option(self):
        a = self.r.build_argv("muse", self.req("fix", provider="echo"), "P")
        self.assertEqual(a[a.index("--provider") + 1], "echo")

    def test_oversized_prompt_falls_back_to_file_bootstrap(self):
        req = self.req("fix", prompt="x" * (MAX_ARG_BYTES + 1))
        arg = self.r.prompt_arg(req)
        self.assertIn("prompt.md", arg)
        self.assertLess(len(arg), 500)

    def test_binary_resolution_prefers_env_then_config(self):
        other = make_fake_muse(self.tmp / "run" if (self.tmp / "run").exists() else self.tmp)
        os.environ["WAKE_MUSE_BIN"] = str(other)
        try:
            self.assertEqual(self.r.resolve_binary(), str(other))
        finally:
            del os.environ["WAKE_MUSE_BIN"]

    def test_missing_binary_is_reported_not_raised(self):
        r = runners.build("muse", {"bin": "/nonexistent/muse"})
        old = os.environ.get("PATH")
        os.environ["PATH"] = "/nonexistent"
        try:
            p = r.probe()
        finally:
            os.environ["PATH"] = old
        self.assertFalse(p.ok)
        self.assertIn("not found", p.problems[0])

    def test_stream_parser_counts_events_and_tolerates_junk(self):
        f = self.tmp / "out.jsonl"
        f.write_text('{"type":"run.started"}\nnot json\n{"payload_type":"task.done"}\n')
        self.assertEqual(self.r.parse_stream(f), (2, "task.done"))


class OtherAdaptersTests(unittest.TestCase):
    def req(self, mode, **o):
        return RunRequest("p", Path("."), 5, mode, Path("."), options=o)

    def test_claude_argv(self):
        r = runners.build("claude", {})
        ro = r.build_argv("claude", self.req("read-only"), "P")
        self.assertEqual(ro[:3], ["claude", "-p", "P"])
        self.assertIn("Read,Grep,Glob", ro)
        fx = r.build_argv("claude", self.req("fix", allowed_tools=["Edit", "Bash(git status)"]), "P")
        self.assertIn("acceptEdits", fx)
        self.assertIn("Edit,Bash(git status)", fx)

    def test_codex_argv(self):
        r = runners.build("codex", {})
        self.assertEqual(r.build_argv("codex", self.req("read-only"), "P")[:4], ["codex", "exec", "--sandbox", "read-only"])
        self.assertIn("workspace-write", r.build_argv("codex", self.req("fix"), "P"))

    def test_kimi_argv_and_experimental_flag(self):
        r = runners.build("kimi", {})
        self.assertEqual(r.build_argv("kimi", self.req("fix"), "P"), ["kimi", "--prompt", "P"])
        self.assertTrue(r.experimental)

    def test_unknown_runner_type(self):
        with self.assertRaises(ValueError):
            runners.build("x", {"type": "nope"})


if __name__ == "__main__":
    unittest.main()
