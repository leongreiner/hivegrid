"""hivegrid-send and the outbox behind the phone's files (bin/hivegrid_outbox.py), and the endpoints that
serve them. Temp directories only: nothing touches a real runtime dir, home or window."""
import io
import json
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_outbox as O  # noqa: E402
import test_remote as TR  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
NOW = 1_800_000_000.0


class OutboxCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.box = os.path.join(self.tmp.name, "box")
        self.env = {"HIVEGRID_OUTBOX": self.box, "WEZTERM_UNIX_SOCKET": "/run/x/gui-sock-77"}

    def file(self, name, data=b"hello"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def send(self, name="a.txt", data=b"hello", pane=3, now=NOW, **kw):
        return O.send(self.file(name, data), pane, environ=self.env, now=now, **kw)


class TestLocation(unittest.TestCase):
    def test_outbox_is_in_memory_storage_never_the_home_cache(self):
        self.assertEqual(O.outbox_dir({"XDG_RUNTIME_DIR": "/run/user/5", "XDG_CACHE_HOME": "/c", "HOME": "/h"}), "/run/user/5/hivegrid-outbox")
        self.assertEqual(O.outbox_dir({"HOME": "/h"}), f"/tmp/hivegrid-outbox-{os.getuid()}")
        self.assertEqual(O.outbox_dir({"HIVEGRID_OUTBOX": "/x", "XDG_RUNTIME_DIR": "/run/user/5"}), "/x")


class TestSend(OutboxCase):
    def test_a_sent_file_is_listed_and_read_back_byte_for_byte(self):
        meta = self.send("shot.png", PNG, note="the chart")
        self.assertEqual((meta["pane"], meta["name"], meta["size"], meta["mime"], meta["note"]), (3, "shot.png", len(PNG), "image/png", "the chart"))
        box = O.Outbox(self.env)
        (item,) = box.for_pane(3)
        self.assertEqual(box.read(item["id"])[1], PNG)
        self.assertEqual(oct(os.stat(self.box).st_mode & 0o777), "0o700")
        self.assertEqual(box.for_pane(4), [], "another agent's files are not listed")

    def test_the_type_comes_from_the_bytes_not_the_name(self):
        self.assertEqual(self.send("fake.png", b"<html><script>1</script>")["mime"], "application/octet-stream")
        self.assertEqual(self.send("pic.bin", PNG)["mime"], "image/png")
        self.assertEqual(self.send("x.svg", b"<svg onload=alert(1)>")["mime"], "application/octet-stream", "svg is never drawn")

    def test_too_big_and_non_regular_files_are_refused_and_leave_nothing(self):
        with self.assertRaises(O.OutboxError):
            self.send("big", b"x" * (O.MAX_FILE + 1))
        with self.assertRaises(O.OutboxError):
            O.send(self.tmp.name, 3, environ=self.env, now=NOW)
        with self.assertRaises(O.OutboxError):
            O.send(os.path.join(self.tmp.name, "missing"), 3, environ=self.env, now=NOW)
        self.assertEqual(O.Outbox(self.env).items(), [])
        self.assertFalse(os.path.exists(self.box), "a refused file creates nothing, not even the directory")

    def test_old_and_surplus_items_are_dropped_on_the_next_send(self):
        old = self.send("old", now=NOW - O.KEEP_SECONDS - 10)
        for i in range(O.MAX_ITEMS + 2):
            self.send(f"f{i}", now=NOW + i)
        items = O.Outbox(self.env).items()
        self.assertEqual(len(items), O.MAX_ITEMS)
        self.assertNotIn(old["id"], [m["id"] for m in items])
        self.assertEqual(items[0]["name"], f"f{O.MAX_ITEMS + 1}", "the newest survive")

    def test_names_are_cleaned(self):
        self.assertEqual(O.clean_name("../../etc/pass\u202ewd"), "passwd")
        self.assertEqual(O.clean_name("\x00\x1b"), "file")
        self.assertLessEqual(len(O.clean_name("a" * 500 + ".png")), O.MAX_NAME)
        self.assertTrue(O.clean_name("a" * 500 + ".png").endswith(".png"))


class TestReadSideDistrust(OutboxCase):
    def test_tampered_entries_are_ignored(self):
        good = self.send("ok.txt")
        box = O.Outbox(self.env)
        # data of another size than the metadata says, a symlink for data, junk metadata, a bad id
        short = self.send("short.txt", b"abcdef")
        with open(os.path.join(self.box, short["id"] + ".bin"), "wb") as f:
            f.write(b"ab")
        linked = self.send("link.txt")
        os.unlink(os.path.join(self.box, linked["id"] + ".bin"))
        os.symlink("/etc/hostname", os.path.join(self.box, linked["id"] + ".bin"))
        with open(os.path.join(self.box, "00000000000-deadbeef.json"), "w") as f:
            f.write("{not json")
        with open(os.path.join(self.box, "../escape.json"), "w") as f:
            json.dump({"id": "../escape"}, f)
        os.utime(self.box, None)
        box._stamp = None
        self.assertEqual([m["id"] for m in box.items()], [good["id"]])
        self.assertIsNone(box.read(linked["id"]))
        self.assertIsNone(box.read("../escape"))
        self.assertIsNone(box.read("x"))

    def test_a_directory_others_can_write_is_not_read(self):
        self.send()
        os.chmod(self.box, 0o777)
        self.assertEqual(O.Outbox(self.env).items(), [])

    def test_items_of_another_window_run_are_hidden(self):
        self.send(pane=3)
        box = O.Outbox(self.env)
        self.assertEqual(len(box.for_pane(3, "gui-sock-77")), 1)
        self.assertEqual(box.for_pane(3, "gui-sock-99"), [], "pane 3 of an earlier run is not this pane 3")


class TestCli(OutboxCase):
    def run_cli(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        code = O.main(argv, dict(self.env, **(env or {})), out, err)
        return code, out.getvalue(), err.getvalue()

    def test_sends_to_the_pane_of_the_environment(self):
        code, out, _ = self.run_cli([self.file("a.txt")], {"WEZTERM_PANE": "12"})
        self.assertEqual(code, 0)
        self.assertIn("sent a.txt", out)
        self.assertEqual([m["pane"] for m in O.Outbox(self.env).items()], [12])

    def test_without_a_pane_it_says_so(self):
        code, _, err = self.run_cli([self.file("a.txt")], {"WEZTERM_PANE": ""})
        self.assertEqual(code, 2)
        self.assertIn("--pane", err)

    def test_a_note_belongs_to_the_first_file_only(self):
        a, b = self.file("a.txt"), self.file("b.txt")
        code, _, _ = self.run_cli(["--pane", "1", "--note", "see both", a, b])
        self.assertEqual(code, 0)
        notes = {m["name"]: m["note"] for m in O.Outbox(self.env).items()}
        self.assertEqual(notes, {"a.txt": "see both", "b.txt": ""})

    def test_one_bad_file_does_not_stop_the_others(self):
        code, out, err = self.run_cli(["--pane", "1", os.path.join(self.tmp.name, "nope"), self.file("b.txt")])
        self.assertEqual(code, 1)
        self.assertIn("sent b.txt", out)
        self.assertIn("cannot read", err)


class TestApi(TR.AppCase):
    def setUp(self):
        super().setUp()
        self.box = os.environ["HIVEGRID_OUTBOX"]

    def put(self, name, data, pane=3, **kw):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return O.send(path, pane, environ={"HIVEGRID_OUTBOX": self.box}, now=TR.NOW, **kw)

    def test_files_are_listed_for_their_agent_and_served(self):
        a = self.put("shot.png", PNG, note="n")
        self.put("log.txt", b"text", pane=99)
        r, data = self.call("GET", "/api/agents/3/files")
        self.assertEqual(r.status, 200)
        (f,) = data["files"]
        self.assertEqual((f["id"], f["name"], f["image"], f["note"]), (a["id"], "shot.png", True, "n"))
        r, _ = self.call("GET", f"/api/agents/3/files/{a['id']}")
        self.assertEqual((r.status, r.content_type, r.body), (200, "image/png", PNG))
        self.assertTrue(r.headers["Content-Disposition"].startswith("inline"))

    def test_a_non_image_is_an_attachment_and_never_html(self):
        a = self.put("page.html", b"<script>alert(1)</script>")
        r, _ = self.call("GET", f"/api/agents/3/files/{a['id']}")
        self.assertEqual((r.content_type, r.headers["Content-Disposition"].split(";")[0]), ("application/octet-stream", "attachment"))

    def test_another_agents_file_unknown_ids_and_no_token_are_refused(self):
        a = self.put("x.png", PNG, pane=99)
        self.assertEqual(self.call("GET", f"/api/agents/3/files/{a['id']}")[0].status, 404)
        self.assertEqual(self.call("GET", "/api/agents/3/files/00000000000-deadbeef")[0].status, 404)
        self.assertEqual(self.call("GET", "/api/agents/3/files/..%2F..%2Fetc%2Fpasswd")[0].status, 404)
        self.assertEqual(self.call("GET", "/api/agents/3/files", token=None)[0].status, 401)
        self.assertEqual(self.call("POST", "/api/agents/3/files", {})[0].status, 405)

    def test_the_chat_answer_lists_the_files_and_its_revision_changes_when_one_arrives(self):
        r, before = self.call("GET", "/api/agents/3/chat")
        self.assertEqual(before["files"], [])
        self.put("a.png", PNG)
        r, after = self.call("GET", f"/api/agents/3/chat?rev={before['rev']}")
        self.assertNotIn("unchanged", after, "a held or repeated chat request wakes for a new file")
        self.assertEqual(len(after["files"]), 1)
        self.assertNotEqual(before["rev"], after["rev"])

    def test_read_only_pages_still_see_files(self):
        a = self.put("a.png", PNG)
        app = self.make_app(readonly=True)
        self.assertEqual(self.call("GET", f"/api/agents/3/files/{a['id']}", app=app)[0].status, 200)

    def test_csp_allows_blob_images_only(self):
        csp = TR.R.SECURITY_HEADERS["Content-Security-Policy"]
        self.assertIn("img-src 'self' data: blob:", csp)
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("unsafe", csp)


if __name__ == "__main__":
    unittest.main()
