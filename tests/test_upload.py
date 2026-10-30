"""Files uploaded from the phone (bin/hivegrid_upload.py) and `POST /api/agents/<id>/upload`. Temp directories only."""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_upload as X  # noqa: E402
import test_remote as TR  # noqa: E402

NOW = 1_800_000_000.0


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "cache", "hivegrid", "uploads")

    def save(self, name, data, now=NOW):
        return X.save(self.root, name, len(data), io.BytesIO(data).read, now=now)

    def test_the_file_is_stored_privately_under_a_safe_name(self):
        got = self.save("My Photo (1).JPG", b"abc")
        self.assertEqual((got["name"], got["size"]), ("My_Photo_1.JPG", 3))
        self.assertEqual(os.path.dirname(os.path.dirname(got["path"])), self.root)
        with open(got["path"], "rb") as f:
            self.assertEqual(f.read(), b"abc")
        self.assertEqual(oct(os.stat(got["path"]).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(os.path.dirname(got["path"])).st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(self.root).st_mode & 0o777), "0o700")

    def test_a_name_cannot_leave_its_folder_or_hide(self):
        for name in ("../../etc/passwd", "/etc/shadow", "..", ".bashrc", "a\x00b\u202etxt.exe", "\u00e4\u00f6"):
            with self.subTest(name=name):
                got = self.save(name, b"x")
                self.assertEqual(os.path.dirname(os.path.dirname(got["path"])), self.root)
                self.assertRegex(got["name"], r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    def test_same_name_twice_keeps_both(self):
        a, b = self.save("a.txt", b"1"), self.save("a.txt", b"2")
        self.assertNotEqual(a["path"], b["path"])

    def test_empty_too_big_and_short_uploads_store_nothing(self):
        with self.assertRaises(X.UploadError):
            X.save(self.root, "a", 0, io.BytesIO(b"").read, now=NOW)
        with self.assertRaises(X.UploadError):
            X.save(self.root, "a", X.MAX_UPLOAD + 1, io.BytesIO(b"").read, now=NOW)
        with self.assertRaises(X.UploadError):
            X.save(self.root, "a", 10, io.BytesIO(b"short").read, now=NOW)
        self.assertEqual(os.listdir(self.root) if os.path.isdir(self.root) else [], [])

    def test_a_stalled_upload_leaves_nothing_behind(self):
        def stalls(n):
            raise TimeoutError("request deadline passed")
        with self.assertRaises(TimeoutError):
            X.save(self.root, "a", 10, stalls, now=NOW)
        self.assertEqual(os.listdir(self.root), [])

    def test_old_uploads_and_the_oldest_beyond_the_caps_go_on_the_next_one(self):
        old = self.save("old.txt", b"x", now=NOW)
        os.utime(os.path.dirname(old["path"]), (NOW, NOW))
        self.save("new.txt", b"y", now=NOW + X.KEEP_SECONDS + 5)
        self.assertFalse(os.path.exists(old["path"]))
        keep = []
        for i in range(3):
            got = self.save(f"f{i}.txt", b"z" * 10, now=NOW + X.KEEP_SECONDS + 10 + i)
            os.utime(os.path.dirname(got["path"]), (NOW + X.KEEP_SECONDS + 10 + i,) * 2)
            keep.append(got["path"])
        X.MAX_ITEMS, saved = 2, X.MAX_ITEMS
        try:
            self.save("last.txt", b"z", now=NOW + X.KEEP_SECONDS + 20)
        finally:
            X.MAX_ITEMS = saved
        self.assertFalse(os.path.exists(keep[0]), "the oldest goes first")
        self.assertTrue(os.path.exists(keep[2]))

    def test_the_root_follows_the_cache_variable_else_the_home(self):
        self.assertEqual(X.upload_root({"XDG_CACHE_HOME": "/c", "HOME": "/h"}), "/c/hivegrid/uploads")
        self.assertEqual(X.upload_root({"XDG_CACHE_HOME": "rel", "HOME": "/h"}), "/h/.cache/hivegrid/uploads")
        self.assertEqual(X.upload_root({}, home="/x"), "/x/.cache/hivegrid/uploads")


class TestApi(TR.AppCase):
    def upload(self, name, data, ident="3", token=TR.TOKEN, headers=None, app=None, length=None):
        h = {"host": "127.0.0.1:8787", "content-type": "application/octet-stream", "x-hivegrid-remote": "1",
             "content-length": str(len(data) if length is None else length)}
        if token:
            h["authorization"] = "Bearer " + token
        h.update(headers or {})
        stream = io.BytesIO(data)
        extended = []
        req = TR.R.Request("POST", f"/api/agents/{ident}/upload?name={name}", h, "127.0.0.1", stream.read, extend=extended.append)
        resp = (app or self.app).dispatch(req)
        self.clock.t += self.tick
        body = json.loads(resp.body) if resp.body else None
        return resp, body, extended

    def test_an_upload_is_stored_and_its_path_answered(self):
        r, data, extended = self.upload("shot%20one.png", b"\x89PNG-bytes")
        self.assertEqual(r.status, 200)
        self.assertEqual((data["ok"], data["name"], data["size"]), (True, "shot_one.png", 10))
        with open(data["path"], "rb") as f:
            self.assertEqual(f.read(), b"\x89PNG-bytes")
        self.assertTrue(data["path"].startswith(os.environ["XDG_CACHE_HOME"]), "the test's cache, never a real home")
        self.assertEqual(extended, [TR.R.UPLOAD_DEADLINE], "the body gets the long deadline once the request is accepted")
        self.assertTrue(any("write upload" in line for line in self.logs))
        self.assertNotIn("shot", " ".join(self.logs), "the log names no file")

    def test_meta_offers_it(self):
        _, meta = self.call("GET", "/api/meta")
        self.assertIn("upload", meta["capabilities"])
        self.assertEqual(meta["limits"]["upload"], X.MAX_UPLOAD)

    def test_it_is_refused_without_the_token_the_header_the_type_or_on_a_read_only_server(self):
        self.assertEqual(self.upload("a", b"x", token=None)[0].status, 401)
        self.assertEqual(self.upload("a", b"x", headers={"x-hivegrid-remote": None} if False else {"x-hivegrid-remote": "0"})[0].status, 403)
        self.assertEqual(self.upload("a", b"x", headers={"content-type": "application/json"})[0].status, 415)
        self.assertEqual(self.upload("a", b"x", headers={"origin": "http://evil.example"})[0].status, 403)
        ro = self.make_app(readonly=True)
        r, data, _ = self.upload("a", b"x", app=ro)
        self.assertEqual((r.status, data["error"]), (403, "readonly"))
        self.assertFalse(os.path.exists(os.path.join(os.environ["XDG_CACHE_HOME"], "hivegrid")), "nothing was written")

    def test_bad_requests_store_nothing(self):
        self.assertEqual(self.upload("a", b"")[0].status, 400)
        self.assertEqual(self.upload("", b"x")[0].status, 400)
        self.assertEqual(self.upload("a", b"x", length=X.MAX_UPLOAD + 1)[0].status, 413)
        self.assertEqual(self.upload("a", b"short", length=50)[0].status, 400, "fewer bytes than announced")
        self.assertEqual(self.upload("a", b"x", ident="99")[0].status, 404)
        self.assertEqual(self.upload("a", b"x", ident="../x")[0].status, 404)
        self.assertEqual(self.call("GET", "/api/agents/3/upload")[0].status, 405)
        root = X.upload_root(home=self.home)
        self.assertEqual(os.listdir(root) if os.path.isdir(root) else [], [])

    def test_only_two_uploads_run_at_once(self):
        for _ in range(TR.R.UPLOADS_AT_ONCE):
            self.assertTrue(self.app._uploading.acquire(blocking=False))
        r, data, _ = self.upload("a", b"x")
        self.assertEqual((r.status, data["error"], r.headers["Retry-After"]), (429, "rate_limited", "2"))
        self.app._uploading.release()
        self.assertEqual(self.upload("a", b"x")[0].status, 200)


if __name__ == "__main__":
    unittest.main()
