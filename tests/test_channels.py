import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from wakeagent import channels as chan
from wakeagent.channels.base import ChannelError

REQS: list[dict] = []
FAIL = {"code": 0}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        REQS.append({"path": self.path, "body": body, "headers": dict(self.headers)})
        if FAIL["code"]:
            self.send_response(FAIL["code"]); self.end_headers(); self.wfile.write(b"nope"); return
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass


class ChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), H)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        REQS.clear()
        FAIL["code"] = 0
        os.environ.update({"T_TOKEN": "tok123", "T_CHAT": "42", "T_HOOK": self.base + "/hook",
                           "T_SID": "AC1", "T_TW": "twtok", "T_FROM": "+100", "T_TO": "+200"})

    def test_telegram(self):
        chan.build("t", {"type": "telegram", "token_env": "T_TOKEN", "chat_id_env": "T_CHAT",
                         "api_base": self.base}).send("title", "body", "urgent")
        r = REQS[0]
        self.assertEqual(r["path"], "/bottok123/sendMessage")
        self.assertEqual(json.loads(r["body"])["chat_id"], "42")
        self.assertIn("title", json.loads(r["body"])["text"])

    def test_slack_and_discord_webhooks(self):
        chan.build("s", {"type": "slack", "webhook_env": "T_HOOK"}).send("t", "b", "alert")
        chan.build("d", {"type": "discord", "webhook_env": "T_HOOK"}).send("t", "b" * 5000, "alert")
        self.assertIn("text", json.loads(REQS[0]["body"]))
        self.assertLessEqual(len(json.loads(REQS[1]["body"])["content"]), 2000)   # discord limit respected
        self.assertIn("wake-agent", REQS[1]["headers"]["User-Agent"])              # discord blocks default UA

    def test_twilio_sms_is_short_and_authenticated(self):
        chan.build("sms", {"type": "twilio", "sid_env": "T_SID", "token_env": "T_TW", "from_env": "T_FROM",
                           "to_env": "T_TO", "api_base": self.base}).send("title", "x" * 900, "urgent")
        r = REQS[0]
        self.assertEqual(r["path"], "/2010-04-01/Accounts/AC1/Messages.json")
        self.assertTrue(r["headers"]["Authorization"].startswith("Basic "))
        q = parse_qs(r["body"])
        self.assertEqual((q["From"], q["To"]), (["+100"], ["+200"]))
        self.assertLessEqual(len(q["Body"][0]), 320)

    def test_ntfy(self):
        chan.build("n", {"type": "ntfy", "topic": "mytopic", "server": self.base}).send("T", "hello", "urgent")
        self.assertEqual(REQS[0]["path"], "/mytopic")
        self.assertEqual(REQS[0]["headers"]["Priority"], "5")

    def test_client_error_raises_without_retrying(self):
        FAIL["code"] = 403
        with self.assertRaises(ChannelError):
            chan.build("s", {"type": "slack", "webhook_env": "T_HOOK"}).send("t", "b", "info")
        self.assertEqual(len(REQS), 1)

    def test_missing_env_is_reported_by_check_and_send(self):
        c = chan.build("t", {"type": "telegram", "token_env": "NOPE_X", "chat_id_env": "T_CHAT"})
        self.assertTrue(c.check())
        with self.assertRaises(ChannelError):
            c.send("t", "b", "info")

    def test_unknown_type(self):
        with self.assertRaises(ChannelError):
            chan.build("x", {"type": "carrier-pigeon"})


if __name__ == "__main__":
    unittest.main()
