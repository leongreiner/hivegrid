"""Voice mode (bin/hivegrid_remote_voice.py): the key file, the settings, the token request to Google (faked: no
network) and the two routes over the real server with a fake mux behind it."""
import json
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_remote as R  # noqa: E402
import hivegrid_remote_mcp as M  # noqa: E402
import hivegrid_remote_voice as V  # noqa: E402
import test_remote_http as TH  # noqa: E402
from hivegrid_settings_core import remote_settings  # noqa: E402
from test_agents import NOW, pane  # noqa: E402
from test_remote import TOKEN  # noqa: E402

KEY = "AIzaSy" + "k" * 30 + ".x-_1"


class TestSettings(unittest.TestCase):
    def test_only_the_literal_true_switches_it_on(self):
        for value in (None, False, 1, "true", "yes", {}):
            self.assertFalse(remote_settings({"voice": value})["voice"], repr(value))
        self.assertTrue(remote_settings({"voice": True})["voice"])
        self.assertFalse(remote_settings(None)["voice"])

    def test_a_model_or_voice_of_the_wrong_shape_keeps_the_default(self):
        got = remote_settings({"voice_model": "gemini-3.8-live-extended-thinking", "voice_name": "Puck"})
        self.assertEqual((got["voice_model"], got["voice_name"]), ("gemini-3.8-live-extended-thinking", "Puck"))
        for model, name in (("gpt-4o", "Kore"), ("gemini-3.8-live?key=x", "Ko re"), ("models/../x", "<b>"), (5, None)):
            got = remote_settings({"voice_model": model, "voice_name": name})
            self.assertEqual((got["voice_model"], got["voice_name"]), ("gemini-3.8-live", "Kore"), (model, name))


class TestKeyFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "gemini-key")

    def write(self, text, mode=0o600):
        with open(self.path, "w") as f:
            f.write(text)
        os.chmod(self.path, mode)

    def test_a_private_file_with_a_key(self):
        self.write(KEY + "\n")
        self.assertEqual(V.read_key(self.path), KEY)

    def test_a_file_others_can_read_is_no_key(self):
        for mode in (0o644, 0o640, 0o604):
            self.write(KEY, mode)
            self.assertIsNone(V.read_key(self.path), oct(mode))

    def test_a_symlink_a_missing_file_and_junk_are_no_key(self):
        self.assertIsNone(V.read_key(self.path))
        self.write("not a key\nsecond line")
        self.assertIsNone(V.read_key(self.path))
        self.write(KEY)
        link = os.path.join(self.tmp.name, "link")
        os.symlink(self.path, link)
        self.assertIsNone(V.read_key(link))

    def test_it_sits_next_to_the_settings_unless_told_otherwise(self):
        self.assertEqual(V.key_path("/cfg/hivegrid/settings.json", {}), "/cfg/hivegrid/gemini-key")
        self.assertEqual(V.key_path("/cfg/hivegrid/settings.json", {"HIVEGRID_GEMINI_KEY_FILE": "/k"}), "/k")


class TestDeclarations(unittest.TestCase):
    def test_every_tool_is_declared_in_the_shape_gemini_takes(self):
        decl = {d["name"]: d for d in V.declarations()}
        self.assertEqual(set(decl), set(M.TOOLS))
        self.assertNotIn("parameters", decl["list_agents"], "a tool without arguments has no parameters")
        prompt = decl["send_prompt"]["parameters"]
        self.assertEqual((prompt["type"], prompt["required"]), ("object", ["agent", "text"]))
        self.assertEqual(prompt["properties"]["agent"]["type"], "string", "a number-or-string is one type")
        self.assertEqual(decl["wait_for_agents"]["parameters"]["properties"]["agents"], {
            "type": "array", "items": {"type": "string"},
            "description": M.TOOLS["wait_for_agents"][1]["inputSchema"]["properties"]["agents"]["description"]})
        self.assertEqual(decl["bot_action"]["parameters"]["properties"]["action"]["enum"],
                         ["run", "cancel", "pause", "resume", "skip", "unskip"])
        for d in decl.values():
            self.assertNotIn("additionalProperties", json.dumps(d), d["name"])


class TestMint(unittest.TestCase):
    def test_the_token_is_locked_to_the_setup_and_short_lived(self):
        sent = []
        setup = V.session_setup("gemini-3.8-live", "Kore", "box")
        token, expires = V.mint(KEY, setup, 1_800_000_000, lambda body, key: sent.append((body, key)) or (200, {"name": "auth_tokens/abc"}))
        self.assertEqual((token, expires), ("auth_tokens/abc", 1_800_000_000 + 1800))
        body, key = sent[0]
        self.assertEqual(key, KEY)
        self.assertEqual((body["uses"], body["expireTime"], body["newSessionExpireTime"]), (1, "2027-01-15T08:30:00Z", "2027-01-15T08:02:00Z"))
        locked = body["bidiGenerateContentSetup"]
        self.assertEqual(locked["model"], "models/gemini-3.8-live")
        self.assertEqual(locked["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"], "Kore")
        self.assertIn("on box", locked["systemInstruction"]["parts"][0]["text"])
        self.assertEqual(len(locked["tools"][0]["functionDeclarations"]), len(M.TOOLS))
        self.assertNotIn(KEY, json.dumps(body))

    def test_a_refusal_is_said_without_the_key(self):
        for answer in ((400, {"error": {"status": "INVALID_ARGUMENT", "message": f"API key {KEY} not valid"}}), (200, {}), (500, {})):
            with self.assertRaises(V.VoiceError) as caught:
                V.mint(KEY, {}, NOW, lambda body, key: answer)
            self.assertEqual(caught.exception.code, "mux_failed")
            self.assertNotIn(KEY, caught.exception.message)

    def test_no_answer_is_an_error_not_a_crash(self):
        def down(body, key):
            raise OSError("no route")
        with self.assertRaises(V.VoiceError) as caught:
            V.mint(KEY, {}, NOW, down)
        self.assertIn("does not answer", caught.exception.message)


class VoiceCase(TH.HttpCase):
    """The real server with two agents (panes 3 and 7: the dock's 1 and 2) and voice mode wired to it."""

    def setUp(self):
        super().setUp()
        self.fake.panes = [pane(3, "π > ready"), pane(7, "π > ready")]
        self.write_state(7, state="idle", title="billing")
        self.on, self.key, self.minted = True, KEY, []
        self.app.voice = self.voice(self.port)

    def voice(self, port):
        def post(body, key):
            self.minted.append(body)
            return 200, {"name": f"auth_tokens/t{len(self.minted)}"}
        return V.Voice(lambda token: M.Remote("box", "127.0.0.1", port, lambda: token), lambda: remote_settings({"voice": self.on}),
                       lambda: self.key, "box", wall=lambda: NOW, clock=self.clock, post=post)

    def tool(self, name, args=None, **kw):
        self.clock.t += 5
        r, data = self.jreq("POST", "/api/voice/tool", {"name": name, "args": args or {}, **kw})
        return r.status, data


class TestSession(VoiceCase):
    def test_the_page_gets_a_token_and_never_the_key_or_the_instructions(self):
        self.assertIn("voice", self.jreq("GET", "/api/meta")[1]["capabilities"])
        r, data = self.jreq("POST", "/api/voice/session", {})
        self.assertEqual((r.status, data["token"], data["setup"]), (200, "auth_tokens/t1", {"model": "models/gemini-3.8-live"}))
        self.assertTrue(data["url"].startswith("wss://generativelanguage.googleapis.com/"))
        self.assertEqual(data["confirm"], ["quit_agent", "switch_account"])
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        raw = json.dumps(data)
        self.assertNotIn(KEY, raw)
        self.assertNotIn("systemInstruction", raw)
        self.assertIn("systemInstruction", self.minted[0]["bidiGenerateContentSetup"], "it is locked into the token instead")

    def test_off_without_the_setting_or_the_key(self):
        for on, key, word in ((False, KEY, "remote.voice"), (True, None, "gemini-key")):
            self.on, self.key = on, key
            self.assertNotIn("voice", self.jreq("GET", "/api/meta")[1]["capabilities"])
            for path, body in (("/api/voice/session", {}), ("/api/voice/tool", {"name": "list_agents", "args": {}})):
                r, data = self.jreq("POST", path, body)
                self.assertEqual((r.status, data["error"]), (403, "voice_disabled"), path)
                self.assertIn(word, data["message"])
        self.assertEqual(self.minted, [])

    def test_the_page_learns_when_it_is_switched_on(self):
        self.on = False
        before = self.jreq("GET", "/api/agents")[1]["meta_rev"]
        self.on = True
        self.clock.t += 5
        self.assertNotEqual(self.jreq("GET", "/api/agents")[1]["meta_rev"], before, "the agent list tells the page to read /api/meta again")

    def test_it_needs_the_token_and_a_post(self):
        self.assertEqual(self.req("POST", "/api/voice/session", {}, token="wrong")[0].status, 401)
        self.assertEqual(self.req("GET", "/api/voice/session")[0].status, 405)
        self.assertEqual(self.req("POST", "/api/voice/else", {})[0].status, 404)
        self.assertEqual(self.jreq("POST", "/api/voice/session", {"model": "x"})[0].status, 400)
        self.assertEqual(self.jreq("POST", "/api/voice/session", {}, headers={"Origin": "https://evil.example"})[0].status, 403)
        self.assertEqual(self.minted, [])

    def test_sessions_are_rate_limited(self):
        self.tick = 0
        codes = [self.jreq("POST", "/api/voice/session", {})[0].status for _ in range(6)]
        self.assertEqual((codes[:4], set(codes[4:])), ([200] * 4, {429}))
        self.assertEqual(len(self.minted), 4)

    def test_a_refusal_of_google_is_a_502_with_words(self):
        self.app.voice.post = lambda body, key: (403, {"error": {"status": "PERMISSION_DENIED"}})
        r, data = self.jreq("POST", "/api/voice/session", {})
        self.assertEqual((r.status, data["error"]), (502, "mux_failed"))
        self.assertIn("PERMISSION_DENIED", data["message"])


class TestTools(VoiceCase):
    def test_a_spoken_prompt_reaches_the_pane_behind_the_dock_number(self):
        status, data = self.tool("send_prompt", {"agent": "2", "text": "run the tests"})
        self.assertEqual((status, data["error"], json.loads(data["text"])), (200, False, {"sent": True, "agent": "2"}))
        self.assertEqual(self.sends(), [("send-text", "--pane-id", "7", "--", "run the tests"),
                                        ("send-text", "--pane-id", "7", "--no-paste", "\r")])

    def test_what_the_api_refuses_is_a_tool_error_the_model_can_say(self):
        status, data = self.tool("send_prompt", {"agent": "9", "text": "x"})
        self.assertEqual((status, data["error"]), (200, True))
        self.assertIn("no agent 9", data["text"])
        self.assertEqual(self.sends(), [])

    def test_quitting_needs_the_confirmation_of_the_screen(self):
        for extra in ({}, {"confirm": False}):
            status, data = self.tool("quit_agent", {"agent": "1"}, **extra)
            self.assertEqual((status, data["error"]), (409, "confirm_required"))
        self.assertEqual(self.jreq("POST", "/api/voice/tool", {"name": "quit_agent", "args": {"agent": "1"}, "confirm": "yes"})[0].status, 400)
        self.assertEqual(self.sends(), [], "nothing was pressed")

    def test_only_the_declared_tools_exist(self):
        for name in ("open_terminal", "", "__class__", 5, None):
            status, data = self.tool(name)
            self.assertEqual((status, data["error"]), (400, "bad_request"), repr(name))
        self.assertEqual(self.tool("list_agents", ["x"])[0], 400)
        self.assertEqual(self.tool("send_prompt", {"agent": "1", "text": "x" * 9000})[0], 400)
        self.assertEqual(self.jreq("POST", "/api/voice/tool", {"name": "list_agents", "args": {}, "shell": "id"})[0].status, 400)
        self.assertEqual(self.sends(), [])

    def test_a_read_only_server_reads_and_refuses_the_writes(self):
        app = self.make_app(readonly=True)
        self.serve(app)
        app.voice = self.voice(self.port)
        status, data = self.tool("list_agents")
        self.assertEqual((status, data["error"], len(json.loads(data["text"])["agents"])), (200, False, 2))
        status, data = self.tool("send_prompt", {"agent": "1", "text": "x"})
        self.assertEqual((status, data["error"]), (200, True))
        self.assertTrue(data["text"].startswith("403 readonly"), data["text"])
        self.assertEqual(self.sends(), [])

    def test_a_spoken_wait_is_held_for_two_minutes_at_most(self):
        self.write_state(3, state="working")
        status, data = self.tool("wait_for_agents", {"agents": ["1"], "timeout": 600})
        got = json.loads(data["text"])
        self.assertEqual((status, got["timed_out"], got["still_working"]), (200, True, ["1"]))
        self.assertLess(sum(self.slept), V.TOOL_WAIT_MAX + 10)

    def test_the_tool_runs_with_the_callers_token(self):
        new = R.generate_token()
        R.write_token(self.tokfile, new)
        self.clock.t += 5
        r, _ = self.req("POST", "/api/voice/tool", {"name": "list_agents", "args": {}})
        self.assertEqual(r.status, 401, "a rotated token ends voice mode like everything else")


if __name__ == "__main__":
    unittest.main()
