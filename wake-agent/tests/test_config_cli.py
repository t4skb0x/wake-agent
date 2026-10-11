import contextlib
import io
import os
import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import make_env
from wakeagent import cli, result
from wakeagent.config import ConfigError, load


class ConfigTests(unittest.TestCase):
    def write(self, body):
        d = Path(tempfile.mkdtemp())
        (d / "wake.toml").write_text(body)
        (d / "p.md").write_text("x")
        return d / "wake.toml"

    def test_job_needs_known_runner(self):
        p = self.write('[jobs.a]\nrunner="nope"\nprompt="p.md"\n')
        with self.assertRaises(ConfigError):
            load(p)

    def test_mute_requires_expiry_owner_reason(self):
        p = self.write('[[mute]]\npattern="x"\n')
        with self.assertRaisesRegex(ConfigError, "until"):
            load(p)

    def test_routing_must_reference_real_channels(self):
        p = self.write('[routing]\nurgent=["ghost"]\n')
        with self.assertRaisesRegex(ConfigError, "ghost"):
            load(p)

    def test_bad_toml_and_bad_mode(self):
        with self.assertRaises(ConfigError):
            load(self.write("[[[bad"))
        p = self.write('[runners.muse]\ntype="muse"\n[jobs.a]\nrunner="muse"\nprompt="p.md"\nmode="yolo"\n')
        with self.assertRaisesRegex(ConfigError, "mode"):
            load(p)


class ResultTests(unittest.TestCase):
    def w(self, text):
        p = Path(tempfile.mkdtemp()) / "r.json"
        p.write_text(text)
        return p

    def test_valid_defaults_notify_from_status(self):
        r = result.load(self.w('{"status":"fixed","summary":"restarted"}'))
        self.assertEqual(r.notify, "info")

    def test_rejects_bad_values(self):
        for bad in ('{"status":"great","summary":"x"}', '{"status":"ok","summary":""}',
                    '{"status":"ok","summary":"x","notify":"scream"}', "[]"):
            with self.assertRaises(result.ResultError):
                result.load(self.w(bad))

    def test_requires_verdict_for_every_event(self):
        p = self.w('{"status":"ok","summary":"x","verdicts":[{"event_id":"a"}]}')
        with self.assertRaisesRegex(result.ResultError, "b"):
            result.load(p, ["a", "b"])


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_init_writes_loadable_config(self):
        d = Path(tempfile.mkdtemp())
        rc, out, _ = self.run_cli("init", str(d))
        self.assertEqual(rc, 0)
        cfg = load(d / "wake.toml")
        self.assertEqual(set(cfg.jobs), {"hello", "triage"})
        rc2, out2, _ = self.run_cli("init", str(d))
        self.assertIn("skip", out2)                                         # never clobbers without --force

    def test_run_emit_status_runs_and_systemd(self):
        tmp, cfg = make_env()
        c = ["--config", str(tmp / "wake.toml")]
        rc, out, _ = self.run_cli(*c, "emit", "triage", "--line", "something broke", "--wake")
        self.assertEqual(rc, 0)
        rc, out, _ = self.run_cli(*c, "status")
        self.assertIn("triage", out)
        rc, out, _ = self.run_cli(*c, "runs")
        self.assertIn("ok", out)
        rc, out, _ = self.run_cli(*c, "systemd", "--print")
        self.assertIn("wake@.service", out)
        self.assertIn("wake-check.timer", out)
        rc, out, _ = self.run_cli(*c, "check", "--drill")
        self.assertEqual(rc, 1)                                             # qa is overdue

    def test_systemd_units_quote_paths_with_spaces_and_percent(self):
        """QC bug H: unquoted ExecStart broke on paths with spaces."""
        from wakeagent import systemd
        tmp, cfg = make_env()
        cfg.path = tmp / "my dir" / "100%wake.toml"
        line = [l for l in systemd.units(cfg)["wake@.service"].splitlines() if l.startswith("ExecStart")][0]
        self.assertIn("'" + str(cfg.path).replace("%", "%%") + "'", line)

    def test_prune_removes_only_old_transcripts(self):
        tmp, cfg = make_env()
        old, new = cfg.data / "runs" / "old", cfg.data / "runs" / "new"
        old.mkdir(parents=True); new.mkdir(parents=True)
        t = time.time() - 40 * 86400
        os.utime(old, (t, t))
        c = ["--config", str(tmp / "wake.toml"), "prune", "--days", "30"]
        self.run_cli(*c, "--dry-run")
        self.assertTrue(old.exists())
        rc, out, _ = self.run_cli(*c)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_unknown_job_is_a_clean_error(self):
        tmp, _ = make_env()
        rc, _, err = self.run_cli("--config", str(tmp / "wake.toml"), "run", "nope")
        self.assertEqual(rc, 2)
        self.assertIn("unknown job", err)

    def test_missing_config_is_a_clean_error(self):
        old = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        os.environ.pop("WAKE_CONFIG", None)
        os.environ["WAKE_HOME"] = tempfile.mkdtemp()
        try:
            rc, _, err = self.run_cli("status")
        finally:
            os.chdir(old)
            del os.environ["WAKE_HOME"]
        self.assertIn("no wake.toml", err)


if __name__ == "__main__":
    unittest.main()
