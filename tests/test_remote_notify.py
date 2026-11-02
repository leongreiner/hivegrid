import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import hivegrid_remote_notify as N  # noqa: E402
from hivegrid_settings_core import Settings, notify_url_setting, remote_settings  # noqa: E402

URL = "http://100.64.0.7:2586/hg-topic"


def agent(pane, state, title="fix the build"):
    return SimpleNamespace(pane=pane, state=state, title=title)


class Rig:
    def __init__(self, cfg=None):
        self.cfg = dict(remote_settings({"notify_url": URL}), **(cfg or {}))
        self.agents = []
        self.posts = []
        self.fail = None
        self.logs = []
        self.n = N.Notifier(lambda: self.cfg, lambda: self.agents, lambda a, all_: f"{a.pane} {a.title}",
                            post=self.post, log=self.logs.append)

    def post(self, url, data, headers):
        if self.fail:
            raise self.fail
        self.posts.append((url, data.decode("utf-8"), headers))

    def look(self, *agents):
        self.agents = list(agents)
        return self.n.tick()


class SettingsTest(unittest.TestCase):
    def test_only_a_plain_http_or_https_topic_url_counts(self):
        self.assertEqual(notify_url_setting(URL), URL)
        self.assertEqual(notify_url_setting("https://ntfy.example.ts.net/t"), "https://ntfy.example.ts.net/t")
        for bad in (None, "", 5, ["http://a/b"], "ftp://h/t", "http://", "http:///t", "http://u:p@h/t", "http://h/t t",
                    "http://h/t\n", "http://h/t?x=1", "javascript:alert(1)", "h/t", "http://" + "a" * 600):
            with self.subTest(bad=bad):
                self.assertIsNone(notify_url_setting(bad))

    def test_defaults_notify_nothing_and_events_are_a_known_subset(self):
        d = remote_settings(None)
        self.assertEqual((d["notify_url"], d["notify_events"]), (None, ["attention", "done", "limit"]))
        self.assertEqual(remote_settings({"notify_events": ["limit", "attention"]})["notify_events"], ["attention", "limit"])
        for bad in ([], ["done", "boom"], "done", [1], None):
            with self.subTest(bad=bad):
                self.assertEqual(remote_settings({"notify_events": bad})["notify_events"], ["attention", "done", "limit"])

    def test_saving_other_settings_keeps_the_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({"remote": {"notify_url": URL, "notify_events": ["done"]}}, f)
            r = Settings(path).remote()
            self.assertEqual((r["notify_url"], r["notify_events"]), (URL, ["done"]))


class NotifierTest(unittest.TestCase):
    def test_the_first_look_sends_nothing_even_for_finished_agents(self):
        r = Rig()
        self.assertEqual(r.look(agent(1, "done"), agent(2, "attention"), agent(3, "limit")), [])
        self.assertEqual(r.posts, [])

    def test_a_change_into_a_wanted_state_sends_one_message_once(self):
        r = Rig()
        r.look(agent(1, "working"))
        self.assertEqual(r.look(agent(1, "attention")), [(1, "attention")])
        self.assertEqual(r.look(agent(1, "attention")), [], "the same state again is not news")
        url, body, headers = r.posts[0]
        self.assertEqual((url, body), (URL, "1 fix the build"))
        self.assertEqual((headers["Title"], headers["Priority"]), ("Hivegrid: agent needs you", "4"))

    def test_done_and_limit_say_what_happened(self):
        r = Rig()
        r.look(agent(1, "working"), agent(2, "working"))
        r.look(agent(1, "done"), agent(2, "limit"))
        self.assertEqual([p[2]["Title"] for p in r.posts], ["Hivegrid: agent is done", "Hivegrid: agent hit its usage limit"])

    def test_states_the_user_did_not_ask_for_are_silent(self):
        r = Rig({"notify_events": ["attention"]})
        r.look(agent(1, "working"))
        self.assertEqual(r.look(agent(1, "done")), [])
        self.assertEqual(r.look(agent(1, "working")), [])
        self.assertEqual(r.look(agent(1, "idle")), [])
        self.assertEqual(r.posts, [])

    def test_an_agent_that_appears_already_done_is_silent_but_one_that_appears_waiting_is_not(self):
        r = Rig()
        r.look(agent(1, "working"))
        self.assertEqual(r.look(agent(1, "working"), agent(2, "done")), [])
        self.assertEqual(r.look(agent(1, "working"), agent(2, "done"), agent(3, "attention")), [(3, "attention")])

    def test_an_unreachable_server_is_logged_without_the_topic_and_the_loop_goes_on(self):
        r = Rig()
        r.look(agent(1, "working"))
        r.fail = OSError("down")
        r.look(agent(1, "attention"))
        self.assertEqual(len(r.logs), 1)
        self.assertNotIn("hg-topic", r.logs[0])
        self.assertNotIn("100.64", r.logs[0])
        r.fail = None
        self.assertEqual(r.look(agent(1, "working")), [])
        self.assertEqual(r.look(agent(1, "done")), [(1, "done")], "the next change still goes out")

    def test_removing_the_url_stops_it_and_adding_it_back_starts_from_a_clean_look(self):
        r = Rig()
        r.look(agent(1, "working"))
        r.cfg["notify_url"] = None
        self.assertEqual(r.look(agent(1, "attention")), [])
        r.cfg["notify_url"] = URL
        self.assertEqual(r.look(agent(1, "attention")), [], "the state at re-enabling is a first look, not an event")
        self.assertEqual(r.posts, [])

    def test_no_window_keeps_what_was_seen(self):
        r = Rig()
        r.look(agent(1, "working"))

        def gone():
            raise RuntimeError("no mux")
        r.n.agents_fn = gone
        self.assertEqual(r.n.tick(), [])
        r.n.agents_fn = lambda: [agent(1, "attention")]
        self.assertEqual(r.n.tick(), [(1, "attention")])

    def test_titles_with_control_characters_and_non_ascii_stay_safe_in_headers_and_body(self):
        r = Rig()
        r.n.describe_fn = lambda a, all_: "7 \u00fcber\x1b[31m\r\nSet-Cookie: x " + "y" * 400
        r.look(agent(1, "working"))
        r.look(agent(1, "attention"))
        _, body, headers = r.posts[0]
        self.assertNotIn("\x1b", body)
        self.assertNotIn("\n", body)
        self.assertLessEqual(len(body), 200)
        self.assertTrue(all("\n" not in v and "\r" not in v for v in headers.values()))
        headers["Title"].encode("ascii")


class BotEventTest(unittest.TestCase):
    """The daemon's helper for a bot's result: title, body and the deep link."""

    def event(self, name="digest", status="failed", summary="boom", base="https://box.tail1234.ts.net"):
        posts = []
        N.bot_event(URL, name, status, summary, base, post=lambda u, d, h: posts.append((u, d.decode("utf-8"), h)))
        return posts[0]

    def test_title_and_body_say_which_bot_and_what_happened(self):
        url, body, headers = self.event()
        self.assertEqual((url, body), (URL, "boom"))
        self.assertEqual((headers["Title"], headers["Priority"], headers["Tags"]), ("Hivegrid bot: digest failed", "4", "x"))
        titles = {s: self.event(status=s)[2]["Title"] for s in ("ok", "limit", "timeout", "cost", "missed")}
        self.assertEqual(titles, {"ok": "Hivegrid bot: digest finished", "limit": "Hivegrid bot: digest hit its usage limit",
                                  "timeout": "Hivegrid bot: digest timed out", "cost": "Hivegrid bot: digest hit its cost cap",
                                  "missed": "Hivegrid bot: digest missed a run"})
        self.assertEqual(self.event(status="strange")[2]["Priority"], "3")

    def test_tapping_the_notification_opens_the_bots_menu_when_the_page_address_is_known(self):
        self.assertEqual(self.event()[2]["Click"], "https://box.tail1234.ts.net/#/bots")
        self.assertEqual(self.event(base="https://box.ts.net/")[2]["Click"], "https://box.ts.net/#/bots")
        for base in (None, "", "ftp://x/y", "http://user:pw@h/", "not a url", "http://h/?x=1", 5):
            self.assertNotIn("Click", self.event(base=base)[2], base)

    def test_the_text_is_one_clean_short_line_and_the_headers_stay_ascii(self):
        _, body, headers = self.event(name="d\u00fcgest\r\nX-Evil: 1", summary="a\x1b[31m\nb " + "z" * 400)
        self.assertNotIn("\x1b", body)
        self.assertNotIn("\n", body)
        self.assertLessEqual(len(body), 200)
        self.assertTrue(all("\n" not in v and "\r" not in v for v in headers.values()))
        headers["Title"].encode("ascii")
        self.assertLessEqual(len(headers["Title"]), 120)

    def test_send_keeps_its_shape_and_a_failing_server_raises_for_the_caller_to_log(self):
        posts = []
        N.send(URL, "t", "b", "5", "bell", post=lambda u, d, h: posts.append((u, d, h)))
        self.assertEqual(posts, [(URL, b"b", {"Title": "t", "Priority": "5", "Tags": "bell"})])

        def refuse(u, d, h):
            raise OSError("down")
        with self.assertRaises(OSError):
            N.bot_event(URL, "x", "ok", "y", None, post=refuse)

    def test_page_url_is_read_from_settings_tolerantly(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            self.assertIsNone(N.page_url(path), "no file")
            for doc, want in (({"remote": {"base_url": "https://box.ts.net"}}, "https://box.ts.net"), ({"remote": {"base_url": "x"}}, None),
                              ({"remote": {}}, None), ({"remote": 5}, None), ([], None), ({"remote": {"base_url": 5}}, None)):
                with open(path, "w") as f:
                    json.dump(doc, f)
                self.assertEqual(N.page_url(path), want, doc)
            with open(path, "w") as f:
                f.write("{broken")
            self.assertIsNone(N.page_url(path))


if __name__ == "__main__":
    unittest.main()
