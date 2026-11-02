"""hivegrid-remote Web Push (bin/hivegrid_remote_push.py, /api/push and /sw.js in bin/hivegrid_remote.py, the Notifier and
scheduler hooks). Temp directories and fakes only: a fake push service records what would have been POSTed, the browser's
side of the encryption is played by the test with its own key pair, and nothing touches the network or a real home.
The encryption is checked against the published vector of RFC 8291 and by decrypting what the sender produced."""
import http.client
import io
import json
import os
import stat
import struct
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_remote as R  # noqa: E402
import hivegrid_remote_notify as N  # noqa: E402
import hivegrid_remote_push as P  # noqa: E402
import hivegrid_scheduler as SCH  # noqa: E402
from hivegrid_settings_core import remote_settings  # noqa: E402
from test_remote import TOKEN, AppCase  # noqa: E402

if P.unavailable():
    raise unittest.SkipTest(P.unavailable())

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

FCM = "https://fcm.googleapis.com/fcm/send/abc123"
APPLE = "https://web.push.apple.com/QGp9"


class Browser:
    """The receiving side: a P-256 key and an auth secret, as `PushSubscription.toJSON()` shows them."""

    def __init__(self, endpoint=FCM):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.pub = self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.auth = os.urandom(16)
        self.endpoint = endpoint

    def subscription(self, **extra):
        return dict({"endpoint": self.endpoint, "keys": {"p256dh": P.b64e(self.pub), "auth": P.b64e(self.auth)}}, **extra)

    def decrypt(self, body):
        """RFC 8291 section 3.4, receiving side."""
        salt, size, idlen = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
        as_pub, record = body[21:21 + idlen], body[21 + idlen:]
        assert size == 4096 and idlen == 65
        secret = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
        ikm = HKDF(hashes.SHA256(), 32, self.auth, b"WebPush: info\x00" + self.pub + as_pub).derive(secret)
        cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
        plain = AESGCM(cek).decrypt(nonce, record, None)
        assert plain.endswith(b"\x02")
        return plain[:-1]


class FakeService:
    """Stands in for http_post: records (endpoint, body, headers) and answers with `status` (an int, a dict by endpoint, or
    an exception to raise)."""

    def __init__(self, status=201):
        self.status, self.posts = status, []

    def __call__(self, url, data, headers):
        self.posts.append((url, data, headers))
        s = self.status.get(url, 201) if isinstance(self.status, dict) else self.status
        if isinstance(s, BaseException):
            raise s
        return s


class TestEncryption(unittest.TestCase):
    def test_the_rfc_8291_example_gives_the_published_message(self):
        b = P.b64d
        out = P.encrypt(b("V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"),
                        b("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"),
                        b("BTBZMqHH6r4Tts7J_aSIgg"), as_private="yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
                        salt=b("DGv6ra1nlYgDCS1FRnbzlw"))
        header = b("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8")
        record = b("8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHsUoEsbI_0LpXMuGvnzQ")
        self.assertEqual(out, header + record)

    def test_only_the_receiving_device_can_read_it_and_every_message_is_fresh(self):
        me, other = Browser(), Browser()
        one = P.encrypt(b"hello", me.pub, me.auth)
        two = P.encrypt(b"hello", me.pub, me.auth)
        self.assertEqual(me.decrypt(one), b"hello")
        self.assertNotEqual(one, two, "a new salt and key each time")
        with self.assertRaises(Exception):
            other.decrypt(one)

    def test_bad_keys_and_oversize_messages_are_refused(self):
        me = Browser()
        for pub, auth in ((me.pub[:-1], me.auth), (b"\x04" + b"\x01" * 64, me.auth), (me.pub, me.auth[:-1])):
            with self.subTest(pub=len(pub), auth=len(auth)), self.assertRaises(ValueError):
                P.encrypt(b"x", pub, auth)
        with self.assertRaises(ValueError):
            P.encrypt(b"x" * (P.MAX_PLAINTEXT + 1), me.pub, me.auth)

    def test_the_vapid_header_is_a_valid_es256_token_for_the_endpoints_origin(self):
        priv = P.new_vapid_private()
        header = P.vapid_authorization(FCM + "?x=1", priv, "mailto:me@example.org", 1_000_000)
        token, key = header[len("vapid t="):].split(", k=")
        head, claims, sig = token.split(".")
        self.assertEqual(json.loads(P.b64d(head)), {"typ": "JWT", "alg": "ES256"})
        self.assertEqual(json.loads(P.b64d(claims)), {"aud": "https://fcm.googleapis.com", "exp": 1_000_000 + P.VAPID_LIFETIME,
                                                      "sub": "mailto:me@example.org"})
        self.assertLessEqual(P.VAPID_LIFETIME, 24 * 3600, "RFC 8292 allows at most a day")
        self.assertEqual(key, P.public_key_b64(priv))
        raw = P.b64d(sig)
        self.assertEqual(len(raw), 64)
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), P.b64d(key))
        pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                   f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


class TestEndpoints(unittest.TestCase):
    def test_only_https_endpoints_of_known_push_services_pass(self):
        for good in (FCM, APPLE, "https://updates.push.services.mozilla.com/wpush/v2/gAAA",
                     "https://wns2-par02p.notify.windows.com/w/?token=abc", "https://fcm.googleapis.com:443/fcm/send/x",
                     "https://web.push.apple.com/x", "https://updates.push.services.mozilla.com/wpush/v2/abc"):
            with self.subTest(good=good):
                self.assertTrue(P.endpoint_ok(good))
        for bad in (None, "", 5, ["https://fcm.googleapis.com/x"], "http://fcm.googleapis.com/fcm/send/x",
                    "https://fcm.googleapis.com:8443/x", "https://user:pw@fcm.googleapis.com/x", "https://fcm.googleapis.com@evil.example/x",
                    "https://fcm.googleapis.com.evil.example/x", "https://evilfcm.googleapis.com/x", "https://evil.example/fcm.googleapis.com",
                    "https://127.0.0.1/x", "https://[::1]/x", "https://localhost/x", "https://100.64.0.7/x", "https://push.apple.com/x",
                    "https://.push.apple.com/x", "https://web.push.apple.com/x#frag", "https://fcm.googleapis.com/x y",
                    "https://fcm.googleapis.com/\n", "ftp://fcm.googleapis.com/x", "//fcm.googleapis.com/x", "https://fcm.googleapis.com/" + "a" * 1000,
                    "https://FCM.googleapis.com/x", "https://fcm.googleapis.com./x", "https://127.0.0.1\\.push.apple.com/x",
                    "https://127.0.0.1%2f.push.apple.com/x", "https://evil.com_.push.apple.com/", "https://x..push.apple.com/",
                    "https://xn--80ak6aa92e.push.apple.com/\u0430", "https://\u0430.push.apple.com/x"):
            with self.subTest(bad=bad):
                self.assertFalse(P.endpoint_ok(bad))

    def test_a_subscription_needs_real_keys(self):
        b = Browser()
        sub = P.parse_subscription(b.subscription(label="Pixel\x1b[31m  7\n"))
        self.assertEqual((sub["id"], sub["label"]), (P.device_id(FCM), "Pixel [31m  7"))
        for bad in ({"endpoint": "https://evil.example/x", "keys": b.subscription()["keys"]}, {"endpoint": FCM},
                    {"endpoint": FCM, "keys": "x"}, {"endpoint": FCM, "keys": {"p256dh": "AAAA", "auth": P.b64e(b.auth)}},
                    {"endpoint": FCM, "keys": {"p256dh": P.b64e(b"\x04" + b"\x01" * 64), "auth": P.b64e(b.auth)}},
                    {"endpoint": FCM, "keys": {"p256dh": P.b64e(b.pub), "auth": P.b64e(b.auth[:8])}},
                    {"endpoint": FCM, "keys": {"p256dh": P.b64e(b.pub) + "=", "auth": P.b64e(b.auth)}},
                    {"endpoint": FCM, "keys": dict(b.subscription()["keys"], extra="x")},
                    dict(b.subscription(), label=5)):
            with self.subTest(bad=str(bad)[:60]), self.assertRaises(ValueError):
                P.parse_subscription(bad)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "cfg", "push.json")
        self.now = 1000.0
        self.store = P.Store(self.path, clock=lambda: self.now)

    def sub(self, endpoint=FCM, label="phone"):
        return P.parse_subscription(Browser(endpoint).subscription(label=label))


class TestStore(StoreCase):
    def test_the_key_is_made_once_and_the_file_is_private(self):
        first = self.store.public_key()
        self.assertEqual(self.store.public_key(), first)
        self.assertEqual(len(P.b64d(first)), 65)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(P.Store(self.path).public_key(), first, "another process sees the same key")

    def test_adding_the_same_device_again_replaces_it_and_the_list_is_capped(self):
        self.store.add(self.sub(label="old"), "own")
        self.now += 5
        self.store.add(self.sub(label="new"), "own")
        self.assertEqual([(d["label"], d["added"]) for d in self.store.devices()], [("new", 1005)])
        for i in range(P.MAX_SUBS - 1):
            self.store.add(self.sub(f"https://fcm.googleapis.com/fcm/send/{i}"), "own")
        with self.assertRaises(P.StoreError):
            self.store.add(self.sub("https://fcm.googleapis.com/fcm/send/one-too-many"), "own")
        self.store.add(self.sub(label="again"), "own")      # replacing needs no free slot
        self.assertEqual(len(self.store.devices()), P.MAX_SUBS)

    def test_removing_and_sweeping_a_rotated_token(self):
        a = self.store.add(self.sub(FCM), "old-token")
        b = self.store.add(self.sub(APPLE), "new-token")
        self.assertEqual(self.store.sweep("new-token"), 1)
        self.assertEqual([d["id"] for d in self.store.devices()], [b])
        self.assertEqual(self.store.remove([a]), 0)
        self.assertEqual(self.store.remove([b]), 1)
        self.assertEqual(self.store.devices(), [])

    def test_a_sweep_with_no_file_creates_nothing(self):
        self.assertEqual(self.store.sweep("x"), 0)
        self.assertFalse(os.path.exists(self.path))

    def test_an_unsafe_or_damaged_file_is_refused_and_left_alone(self):
        self.store.public_key()
        os.chmod(self.path, 0o644)
        with self.assertRaises(P.StoreError):
            self.store.devices()
        os.chmod(self.path, 0o600)
        with open(self.path, "w") as f:
            f.write("{not json")
        with self.assertRaises(P.StoreError):
            self.store.public_key()
        with open(self.path) as f:
            self.assertEqual(f.read(), "{not json", "never overwritten: the devices would be lost silently")
        os.unlink(self.path)
        os.symlink(os.path.join(self.tmp.name, "elsewhere"), self.path)
        with self.assertRaises(P.StoreError):
            self.store.devices()

    def test_a_signing_key_of_the_wrong_size_or_zero_is_damaged(self):
        os.makedirs(os.path.dirname(self.path))
        for key in ("abc", P.b64e(b"\x01" * 31), P.b64e(b"\x00" * 32)):
            with self.subTest(key=key):
                with open(self.path, "w") as f:
                    json.dump({"vapid": key, "subs": []}, f)
                os.chmod(self.path, 0o600)
                with self.assertRaises(P.StoreError):
                    self.store.load()

    def test_a_file_of_the_wrong_shape_is_damaged(self):
        os.makedirs(os.path.dirname(self.path))
        for doc in ([], {"vapid": 5, "subs": []}, {"vapid": None, "subs": [{"id": "x"}]}, {"vapid": None, "subs": "no"}):
            with self.subTest(doc=doc):
                with open(self.path, "w") as f:
                    json.dump(doc, f)
                os.chmod(self.path, 0o600)
                with self.assertRaises(P.StoreError):
                    self.store.load()


class TestPusher(StoreCase):
    def setUp(self):
        super().setUp()
        self.svc = FakeService()
        self.logs = []
        self.pusher = P.Pusher(self.store, lambda: "mailto:me@example.org", post=self.svc, clock=lambda: 5000.0, log=self.logs.append)
        self.phone = Browser()
        self.store.public_key()
        self.store.add(P.parse_subscription(self.phone.subscription(label="phone")), "own")

    EVENT = {"title": "Hivegrid: agent needs you", "body": "1 \u00b7 fix the build", "tag": "agent-7", "url": "#/a/7"}

    def test_the_device_gets_an_encrypted_signed_message_with_ttl_and_urgency(self):
        r = self.pusher.send_event(self.EVENT, "high")
        self.assertEqual(r, {"sent": 1, "failed": 0, "removed": 0})
        url, data, h = self.svc.posts[0]
        self.assertEqual(url, FCM)
        self.assertEqual(json.loads(self.phone.decrypt(data)), self.EVENT)
        self.assertEqual((h["Content-Encoding"], h["TTL"], h["Urgency"], h["Content-Type"]),
                         ("aes128gcm", "600", "high", "application/octet-stream"))
        self.assertTrue(h["Authorization"].startswith("vapid t="))
        self.assertIn(f"k={self.store.public_key()}", h["Authorization"], "the key the page subscribed with")

    def test_a_service_that_forgot_the_device_removes_it_and_other_failures_keep_it(self):
        other = Browser(APPLE)
        self.store.add(P.parse_subscription(other.subscription()), "own")
        for status, kept in ((404, False), (410, False), (429, True), (500, True), (400, True), (301, True)):
            with self.subTest(status=status):
                self.svc.status = {FCM: status}
                self.store.add(P.parse_subscription(self.phone.subscription(label="phone")), "own")
                r = self.pusher.send_event(self.EVENT)
                self.assertEqual((r["sent"], r["failed"], r["removed"]), (1, 1, 0 if kept else 1))
                self.assertEqual(any(d["id"] == P.device_id(FCM) for d in self.store.devices()), kept)

    def test_a_reply_that_is_no_http_counts_as_an_unreachable_service_and_the_others_still_get_theirs(self):
        other = Browser(APPLE)
        self.store.add(P.parse_subscription(other.subscription()), "own")
        self.svc.status = {FCM: http.client.BadStatusLine("garbage")}
        with mock.patch.object(P, "_OPENER") as opener:
            opener.open.side_effect = http.client.BadStatusLine("garbage")
            with self.assertRaises(OSError):
                P.http_post(FCM, b"x", {})
        self.pusher.post = lambda url, data, headers: (_ for _ in ()).throw(OSError("bad reply: BadStatusLine")) if url == FCM else 201
        r = self.pusher.send_event(self.EVENT)
        self.assertEqual((r["sent"], r["failed"]), (1, 1))

    def test_an_unreachable_service_is_logged_without_the_endpoint_and_never_retried(self):
        self.svc.status = OSError("down")
        r = self.pusher.send_event(self.EVENT)
        self.assertEqual((r["sent"], r["failed"]), (0, 1))
        self.assertEqual(len(self.svc.posts), 1)
        self.assertEqual(self.logs, ["push failed: OSError"])
        self.assertNotIn("fcm", " ".join(self.logs))

    def test_one_broken_device_does_not_stop_the_others(self):
        other = Browser(APPLE)
        self.store.add(P.parse_subscription(other.subscription()), "own")
        self.svc.status = {FCM: OSError("down")}
        r = self.pusher.send_event(self.EVENT)
        self.assertEqual((r["sent"], r["failed"]), (1, 1))
        self.assertEqual(json.loads(other.decrypt(self.svc.posts[1][1]))["tag"], "agent-7")

    def test_a_test_goes_only_to_the_device_asked_for(self):
        other = Browser(APPLE)
        ident = self.store.add(P.parse_subscription(other.subscription()), "own")
        self.pusher.send_event(self.EVENT, only=ident)
        self.assertEqual([p[0] for p in self.svc.posts], [APPLE])

    def test_nothing_is_sent_without_devices_or_without_the_package(self):
        self.store.remove([d["id"] for d in self.store.devices()])
        self.assertEqual(self.pusher.send_event(self.EVENT), {"sent": 0, "failed": 0, "removed": 0})
        self.assertEqual(self.svc.posts, [])
        self.store.add(P.parse_subscription(self.phone.subscription()), "own")
        with mock.patch.object(P, "_IMPORT_ERROR", "needs cryptography"):
            self.assertEqual(self.pusher.send_event(self.EVENT)["sent"], 0)
            self.pusher.send_event(self.EVENT)
        self.assertEqual(self.logs, ["push is on but unavailable: needs cryptography"], "said once, not every time")
        self.assertEqual(self.svc.posts, [])

    def test_a_stored_endpoint_that_is_not_a_push_service_is_never_contacted(self):
        with open(self.path) as f:
            doc = json.load(f)
        doc["subs"][0]["endpoint"] = "https://169.254.169.254/latest/meta-data"
        with open(self.path, "w") as f:
            json.dump(doc, f)
        r = self.pusher.send_event(self.EVENT)
        self.assertEqual((r["sent"], r["failed"], self.svc.posts), (0, 1, []))

    def test_a_message_of_emoji_still_fits_in_one_record(self):
        raw = P.Pusher.payload({"title": "\U0001F600" * 200, "body": "\U0001F600" * 400, "tag": "x"})
        self.assertLess(len(raw), P.MAX_PLAINTEXT, "ASCII-escaping would triple the size of every emoji")
        me = Browser()
        self.assertEqual(json.loads(me.decrypt(P.encrypt(raw, me.pub, me.auth)))["body"], "\U0001F600" * 299 + "\u2026")

    def test_the_payload_is_whitelisted_one_line_and_short(self):
        raw = P.Pusher.payload({"title": "t\x1b[31m\r\nx", "body": "b" * 900, "tag": "a" * 200, "url": "javascript:alert(1)",
                                "evil": "x", "click": "https://evil.example"})
        doc = json.loads(raw)
        self.assertEqual(set(doc), {"title", "body", "tag", "url"})
        self.assertEqual((doc["title"], len(doc["body"]), len(doc["tag"]), doc["url"]), ("t [31m x", 300, 60, "#/"))
        for url in ("#/a/12", "#/bots", "#/a/12/menu/usage"):
            self.assertEqual(json.loads(P.Pusher.payload({"url": url}))["url"], url)
        for url in ("https://evil.example/", "//evil", "#/../../x\n", "#/" + "a" * 200, None, 5):
            self.assertEqual(json.loads(P.Pusher.payload({"url": url}))["url"], "#/", url)
        self.assertLess(len(P.Pusher.payload({"title": "é" * 500, "body": "é" * 500})), P.MAX_PLAINTEXT)


class TestSending(unittest.TestCase):
    """http_post against a real server on 127.0.0.1 (the endpoint rule applies to Pusher, not to http_post itself)."""

    def serve(self, handler):
        import http.server
        import threading
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}/x"

    def test_the_status_of_any_answer_comes_back_and_a_redirect_is_not_followed(self):
        import http.server
        hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(self.path)
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                code = {"/ok": 201, "/gone": 410, "/busy": 429}.get(self.path, 302)
                self.send_response(code)
                if code == 302:
                    self.send_header("Location", "/ok")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass
        base = self.serve(H).rsplit("/", 1)[0]
        self.assertEqual([P.http_post(f"{base}{p}", b"x", {}) for p in ("/ok", "/gone", "/busy")], [201, 410, 429])
        self.assertEqual(P.http_post(f"{base}/moved", b"x", {}), 302)
        self.assertEqual(hits, ["/ok", "/gone", "/busy", "/moved"], "the Location was not visited")

    def test_a_reply_that_is_no_http_is_an_oserror_and_a_dead_port_too(self):
        import socket
        import threading
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)

        def junk():
            c, _ = srv.accept()
            c.recv(4096)
            c.sendall(b"not http at all\r\n\r\n")
            c.close()
        threading.Thread(target=junk, daemon=True).start()
        with self.assertRaises(OSError):
            P.http_post(f"http://127.0.0.1:{srv.getsockname()[1]}/x", b"x", {})
        port = srv.getsockname()[1]
        srv.close()
        with self.assertRaises(OSError):
            P.http_post(f"http://127.0.0.1:{port}/x", b"x", {})

    def test_the_vapid_audience_is_the_origin_without_the_default_port(self):
        header = P.vapid_authorization("https://fcm.googleapis.com:443/fcm/send/x", P.new_vapid_private(), "mailto:a@b.c", 1)
        claims = json.loads(P.b64d(header[len("vapid t="):].split(".")[1]))
        self.assertEqual(claims["aud"], "https://fcm.googleapis.com")

    def test_messages_queued_for_the_worker_leave_in_order_and_a_hung_service_drops_the_overflow(self):
        import threading
        store_dir = tempfile.TemporaryDirectory()
        self.addCleanup(store_dir.cleanup)
        store = P.Store(os.path.join(store_dir.name, "push.json"))
        phone = Browser()
        store.public_key()
        store.add(P.parse_subscription(phone.subscription()), "own")
        gate, seen, logs = threading.Event(), [], []

        def post(url, data, headers):
            gate.wait(5)
            seen.append(json.loads(phone.decrypt(data))["body"])
            return 201
        pusher = P.Pusher(store, post=post, log=logs.append)
        for i in range(P.QUEUE_MAX + 5):
            pusher.send_async({"title": "t", "body": str(i), "tag": "agent-1"})
        gate.set()
        deadline = time.time() + 5
        while len(seen) < P.QUEUE_MAX and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(seen, [str(i) for i in range(len(seen))], "never out of order")
        self.assertGreaterEqual(len(seen), P.QUEUE_MAX)
        self.assertLessEqual(len(seen), P.QUEUE_MAX + 1, "the worker holds one while the queue holds the rest")
        self.assertEqual(logs.count("push failed: queue full"), P.QUEUE_MAX + 5 - len(seen))


class TestSettings(unittest.TestCase):
    def test_only_the_literal_true_turns_push_on(self):
        self.assertIs(remote_settings(None)["push"], False)
        self.assertIs(remote_settings({"push": True})["push"], True)
        for bad in (1, "true", "yes", None, [True], {"on": True}):
            with self.subTest(bad=bad):
                self.assertIs(remote_settings({"push": bad})["push"], False)

    def test_the_contact_is_a_mailto_or_https_address_or_nothing(self):
        for ok in ("mailto:me@example.org", "https://example.org/contact"):
            self.assertEqual(remote_settings({"push_contact": ok})["push_contact"], ok)
        for bad in (None, "", "me@example.org", "mailto:nobody", "mailto:a b@c.d", "http://example.org/", "https://u:p@example.org/x",
                    "mailto:" + "a" * 300 + "@x.y", 5, ["mailto:a@b.c"]):
            self.assertIsNone(remote_settings({"push_contact": bad})["push_contact"], bad)


def agent(pane, state, title="fix the build"):
    return SimpleNamespace(pane=pane, state=state, title=title)


class TestNotifierSinks(unittest.TestCase):
    def rig(self, **cfg):
        r = SimpleNamespace(cfg=dict(remote_settings({}), **cfg), agents=[], posts=[], pushes=[], logs=[])
        r.n = N.Notifier(lambda: r.cfg, lambda: r.agents, lambda a, all_: f"{a.pane} {a.title}",
                         post=lambda u, d, h: r.posts.append((u, d, h)), log=r.logs.append,
                         push_fn=lambda event, urgency: r.pushes.append((event, urgency)))

        def look(*agents):
            r.agents = list(agents)
            return r.n.tick()
        r.look = look
        return r

    def test_push_alone_sends_the_event_with_a_deep_link_and_urgency(self):
        r = self.rig(push=True)
        r.look(agent(7, "working"), agent(8, "working"))
        self.assertEqual(r.look(agent(7, "attention"), agent(8, "done")), [(7, "attention"), (8, "done")])
        self.assertEqual(r.pushes, [({"title": "Hivegrid: agent needs you", "body": "7 fix the build", "tag": "agent-7", "url": "#/a/7"}, "high"),
                                    ({"title": "Hivegrid: agent is done", "body": "8 fix the build", "tag": "agent-8", "url": "#/a/8"}, "normal")])
        self.assertEqual(r.posts, [], "no ntfy topic: no ntfy message")

    def test_both_ways_run_side_by_side_and_one_failing_does_not_stop_the_other(self):
        r = self.rig(push=True, notify_url="http://ntfy.test/topic")
        r.n.push_fn = mock.Mock(side_effect=RuntimeError("boom"))
        r.look(agent(1, "working"))
        r.look(agent(1, "attention"))
        self.assertEqual(len(r.posts), 1)
        self.assertEqual(r.logs, ["push failed: RuntimeError"])

        def down(*a):
            raise OSError("down")
        r.n.post, r.n.push_fn = down, lambda e, u: r.pushes.append(e)
        r.look(agent(1, "done"))
        self.assertEqual(len(r.pushes), 1, "ntfy being down does not stop push")

    def test_push_follows_the_same_rules_as_ntfy(self):
        r = self.rig(push=True, notify_events=["attention"])
        self.assertEqual(r.look(agent(1, "done"), agent(2, "attention")), [], "the first look is silent")
        self.assertEqual(r.look(agent(1, "working"), agent(2, "attention"), agent(3, "done")), [], "a pane that appears done is not news")
        self.assertEqual(r.look(agent(1, "done")), [], "a state the user did not ask for")
        self.assertEqual(r.pushes, [])

    def test_push_off_and_no_topic_means_nothing_and_a_clean_restart_when_it_comes_back(self):
        r = self.rig()
        r.look(agent(1, "working"))
        self.assertEqual(r.look(agent(1, "attention")), [])
        r.cfg["push"] = True
        self.assertEqual(r.look(agent(1, "attention")), [], "turning it on is a first look")
        self.assertEqual(r.pushes, [])


class TestSchedulerPush(unittest.TestCase):
    def sched(self, **cfg):
        base = dict(SCH.SETTINGS_DEFAULTS, notify_url=None, push=False)
        out = SimpleNamespace(pushes=[], posts=[])
        out.s = SCH.Scheduler({"HOME": tempfile.gettempdir()}, settings=lambda: dict(base, **cfg),
                              notify=lambda *a: out.posts.append(a), push=lambda e, u: out.pushes.append((e, u)))
        return out

    def test_a_bot_result_goes_to_push_when_it_is_on_and_to_ntfy_when_that_is_set(self):
        x = self.sched(push=True)
        x.s._push("Hivegrid bot: Digest failed", "it broke", "4", "x")
        self.assertEqual(x.pushes, [({"title": "Hivegrid bot: Digest failed", "body": "it broke", "tag": "bots", "url": "#/bots"}, "high")])
        self.assertEqual(x.posts, [])
        y = self.sched(push=True, notify_url="http://ntfy.test/t")
        y.s._push("Hivegrid bot: Digest done", "fine", "3", "white_check_mark")
        self.assertEqual((len(y.posts), len(y.pushes), y.pushes[0][1]), (1, 1, "normal"))

    def test_neither_set_sends_nothing_and_a_failing_sender_never_raises(self):
        x = self.sched()
        x.s._push("t", "b", "3", None)
        self.assertEqual((x.pushes, x.posts), ([], []))
        y = self.sched(push=True, notify_url="http://ntfy.test/t")
        y.s.push_fn = mock.Mock(side_effect=RuntimeError("boom"))
        y.s.notify_fn = mock.Mock(side_effect=OSError("down"))
        y.s._push("t", "b", "3", None)

    def test_the_settings_reader_carries_the_flag(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({"remote": {"push": True}}, f)
            self.assertIs(SCH.read_settings({"HIVEGRID_SETTINGS": path})["push"], True)
            self.assertIs(SCH.read_settings({"HIVEGRID_SETTINGS": os.path.join(d, "none.json")})["push"], False)

    def test_send_from_settings_does_nothing_until_push_is_on(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({"remote": {}}, f)
            self.assertEqual(P.send_from_settings({"title": "t"}, settings_file=path)["sent"], 0)
            self.assertFalse(os.path.exists(os.path.join(d, "push.json")), "off means no key is made either")


class TestApi(AppCase):
    def setUp(self):
        super().setUp()
        self.svc = FakeService()
        self.phone = Browser()
        self.pusher = P.Pusher(P.Store(os.path.join(self.tmp.name, "push.json"), clock=lambda: 1000.0), post=self.svc, log=self.logs.append)
        self.app = self.make_app(pusher=self.pusher)

    def enable(self, **extra):
        with open(self.settings, "w") as f:
            json.dump({"remote": dict({"push": True}, **extra)}, f)

    def subscribe(self, browser=None, **kw):
        return self.call("POST", "/api/push/subscribe", (browser or self.phone).subscription(label="my phone", **kw))

    def test_off_by_default_state_says_so_and_writes_are_refused(self):
        r, d = self.call("GET", "/api/push")
        self.assertEqual((r.status, d["enabled"], d["supported"], d["public_key"], d["devices"]), (200, False, True, None, []))
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        for path, body in (("/api/push/subscribe", self.phone.subscription()), ("/api/push/unsubscribe", {"id": "0" * 16}), ("/api/push/test", {"id": "0" * 16})):
            r, d = self.call("POST", path, body)
            self.assertEqual((r.status, d["error"]), (403, "push_disabled"), path)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "push.json")), "off means no key is made")

    def test_turning_it_on_in_settings_needs_no_restart_and_gives_the_page_its_key(self):
        self.enable()
        r, d = self.call("GET", "/api/push")
        self.assertEqual((d["enabled"], d["devices"], d["max_devices"]), (True, [], P.MAX_SUBS))
        self.assertEqual(len(P.b64d(d["public_key"])), 65)
        self.assertEqual(self.call("GET", "/api/push")[1]["public_key"], d["public_key"])

    def test_subscribe_list_test_and_unsubscribe(self):
        self.enable()
        r, d = self.subscribe()
        self.assertEqual((r.status, d), (200, {"ok": True, "id": P.device_id(FCM)}))
        devices = self.call("GET", "/api/push")[1]["devices"]
        self.assertEqual([(x["id"], x["label"]) for x in devices], [(P.device_id(FCM), "my phone")])
        self.assertNotIn("endpoint", json.dumps(devices), "the endpoint is a capability: never sent back")
        r, d = self.call("POST", "/api/push/test", {"id": P.device_id(FCM)})
        self.assertEqual((r.status, d["sent"], d["failed"]), (200, 1, 0))
        note = json.loads(self.phone.decrypt(self.svc.posts[0][1]))
        self.assertEqual((note["title"], note["url"], note["test"]), ("Hivegrid: test", "#/", True))
        r, d = self.call("POST", "/api/push/unsubscribe", {"id": P.device_id(FCM)})
        self.assertEqual((r.status, d["removed"]), (200, 1))
        self.assertEqual(self.call("GET", "/api/push")[1]["devices"], [])

    def test_a_device_that_subscribes_before_any_state_call_still_gets_messages(self):
        self.enable()
        self.subscribe()
        self.assertEqual(self.call("POST", "/api/push/test", {"id": P.device_id(FCM)})[1]["sent"], 1, "the signing key was made with the first device")
        self.assertEqual(self.call("POST", "/api/push/test", {})[0].status, 400, "a test names the device it is for")

    def test_bad_subscriptions_and_ids_are_400(self):
        self.enable()
        for body in (dict(self.phone.subscription(), endpoint="https://169.254.169.254/x"), {"endpoint": FCM}, dict(self.phone.subscription(), extra=1)):
            r, d = self.call("POST", "/api/push/subscribe", body)
            self.assertEqual((r.status, d["error"]), (400, "bad_request"), body)
        for path in ("/api/push/unsubscribe", "/api/push/test"):
            for ident in ("short", "G" * 16, 5, ["0" * 16]):
                r, d = self.call("POST", path, {"id": ident})
                self.assertEqual(r.status, 400, (path, ident))
        self.assertEqual(self.call("POST", "/api/push/unsubscribe", {})[0].status, 400)

    def test_the_eleventh_device_is_refused_with_a_reason(self):
        self.enable()
        for i in range(P.MAX_SUBS):
            self.assertEqual(self.subscribe(Browser(f"https://fcm.googleapis.com/fcm/send/{i}"))[0].status, 200)
        r, d = self.subscribe(Browser("https://fcm.googleapis.com/fcm/send/last"))
        self.assertEqual((r.status, d["error"]), (409, "conflict"))
        self.assertIn("remove one", d["message"])

    def test_tests_are_rate_limited(self):
        self.enable()
        self.subscribe()
        self.tick = 0.01
        codes = [self.call("POST", "/api/push/test", {"id": P.device_id(FCM)})[0].status for _ in range(4)]
        self.assertEqual(codes, [200, 200, 429, 429])
        self.assertEqual(len(self.svc.posts), 2)

    def test_a_read_only_server_still_serves_and_manages_push(self):
        self.enable()
        app = self.make_app(pusher=self.pusher, readonly=True)
        self.assertEqual(self.call("GET", "/api/push", app=app)[1]["enabled"], True)
        r, _ = self.call("POST", "/api/push/subscribe", self.phone.subscription(), app=app)
        self.assertEqual(r.status, 200)

    def test_the_api_needs_the_token_and_a_same_origin_json_post(self):
        self.enable()
        self.assertEqual(self.call("GET", "/api/push", token=None)[0].status, 401)
        self.assertEqual(self.call("POST", "/api/push/subscribe", self.phone.subscription(), token="wrong")[0].status, 401)
        for headers in ({"x-hivegrid-remote": None}, {"content-type": "text/plain"}, {"origin": "https://evil.example"}):
            r, _ = self.call("POST", "/api/push/subscribe", self.phone.subscription(), headers=headers)
            self.assertIn(r.status, (403, 415), headers)
        self.assertEqual(self.call("POST", "/api/push", {})[0].status, 405)
        self.assertEqual(self.call("GET", "/api/push/test")[0].status, 405)
        self.assertEqual(self.call("GET", "/api/push/nope")[0].status, 404)

    def test_a_package_that_is_missing_says_why_instead_of_pretending(self):
        self.enable()
        with mock.patch.object(P, "_IMPORT_ERROR", "needs cryptography"):
            r, d = self.call("GET", "/api/push")
            self.assertEqual((d["enabled"], d["supported"], d["reason"], d["public_key"]), (True, False, "needs cryptography", None))
            r, d = self.call("POST", "/api/push/subscribe", self.phone.subscription())
            self.assertEqual((r.status, d["message"]), (500, "needs cryptography"))

    def test_a_signing_key_that_is_no_key_is_reported_not_a_500(self):
        self.enable()
        path = os.path.join(self.tmp.name, "push.json")
        with open(path, "w") as f:
            json.dump({"vapid": "not-a-key", "subs": []}, f)
        os.chmod(path, 0o600)
        r, d = self.call("GET", "/api/push")
        self.assertEqual((r.status, d["enabled"], d["public_key"]), (200, False, None))
        self.assertIn("signing key", d["reason"])

    def test_a_device_belongs_to_the_token_that_registered_it_even_if_the_file_changes_meanwhile(self):
        self.enable()
        self.app.tokens.fingerprint = lambda: R._fingerprint(R._token_digest("N" * 40))   # rotated between the token check and the write
        self.assertEqual(self.subscribe()[0].status, 200)
        del self.app.tokens.fingerprint
        with open(os.path.join(self.tmp.name, "push.json")) as f:
            self.assertEqual(json.load(f)["subs"][0]["owner"], R._fingerprint(R._token_digest(TOKEN)), "the token it presented")
        R.write_token(self.tokfile, "N" * 40)
        self.app.push_tick()
        self.assertEqual(self.pusher.store.devices(), [], "registered with the old token: swept once the new one is seen")

    def test_rotating_the_default_token_removes_its_devices_without_a_running_server(self):
        self.enable()
        self.subscribe()
        env = {"HIVEGRID_SETTINGS": self.settings}
        with mock.patch.dict(os.environ, env), mock.patch.object(R, "default_token_path", lambda: self.tokfile), \
                mock.patch("sys.stdout", new_callable=io.StringIO), mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            R.cmd_token(SimpleNamespace(show=False, rotate=True, token_file=None))
        self.assertEqual(self.pusher.store.devices(), [])
        self.assertIn("1 push device", err.getvalue())
        self.call("POST", "/api/push/subscribe", self.phone.subscription(label="again"), token=R.read_token(self.tokfile))
        with mock.patch.dict(os.environ, env), mock.patch("sys.stdout", new_callable=io.StringIO):
            R.cmd_token(SimpleNamespace(show=False, rotate=True, token_file=self.tokfile))
        self.assertEqual(len(self.pusher.store.devices()), 1, "a custom token file belongs to another server: not swept from here")

    def test_an_unusable_push_file_is_reported_once_per_reason_not_every_round(self):
        self.enable()
        self.subscribe()
        path = os.path.join(self.tmp.name, "push.json")
        os.chmod(path, 0o644)
        R.write_token(self.tokfile, "N" * 40)
        before = len(self.logs)
        for _ in range(4):
            self.app.push_tick()
        said = [m for m in self.logs[before:] if "push sweep failed" in m]
        self.assertEqual(len(said), 1)
        self.assertIn("readable by others", said[0], "the reason is in the log")
        os.chmod(path, 0o600)
        self.app.push_tick()
        self.assertEqual(self.pusher.store.devices(), [], "tried again, and swept, once the file is fine")

    def test_a_signing_key_in_the_file_that_is_no_key_is_one_clear_answer_on_every_route(self):
        self.enable()
        path = os.path.join(self.tmp.name, "push.json")
        with open(path, "w") as f:
            json.dump({"vapid": "abc", "subs": []}, f)
        os.chmod(path, 0o600)
        r, d = self.subscribe()
        self.assertEqual((r.status, d["error"]), (409, "conflict"))
        self.assertIn("signing key", d["message"])

    def test_a_broken_push_file_is_reported_not_overwritten(self):
        self.enable()
        path = os.path.join(self.tmp.name, "push.json")
        with open(path, "w") as f:
            f.write("{nope")
        os.chmod(path, 0o600)
        r, d = self.call("GET", "/api/push")
        self.assertEqual((r.status, d["enabled"], d["public_key"]), (200, False, None))
        self.assertIn("damaged", d["reason"])
        self.assertEqual(self.subscribe()[0].status, 409)
        with open(path) as f:
            self.assertEqual(f.read(), "{nope")

    def test_replacing_the_token_removes_the_devices_registered_with_the_old_one(self):
        self.enable()
        self.subscribe()
        self.app.push_tick()
        self.assertEqual(len(self.pusher.store.devices()), 1, "the same token keeps its devices")
        R.write_token(self.tokfile, "N" * 40)
        self.app.push_tick()
        self.assertEqual(self.pusher.store.devices(), [])
        self.assertTrue(any("removed" in m for m in self.logs))

    def test_the_service_worker_is_public_javascript_that_is_revalidated(self):
        r, _ = self.call("GET", "/sw.js", token=None)
        self.assertEqual((r.status, r.content_type), (200, "text/javascript; charset=utf-8"))
        self.assertIn(b"showNotification", r.body)
        self.assertEqual(r.headers["Cache-Control"], "no-cache")
        r2, _ = self.call("GET", "/sw.js", token=None, headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual(r2.status, 304)
        self.assertEqual(self.call("POST", "/sw.js", {}, token=None)[0].status, 405)


if __name__ == "__main__":
    unittest.main()
