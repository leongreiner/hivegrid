"""hivegrid-remote over real HTTP: the server on 127.0.0.1:0 in a thread, a fake mux behind it.
What only a socket shows: the status line and headers, body framing, connection handling."""
import http.client
import json
import os
import select
import socket
import sys
import threading
import time
import unittest
import unittest.mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_remote as R  # noqa: E402
import test_remote as TR  # noqa: E402
from test_agents import pane  # noqa: E402

TOKEN = TR.TOKEN


class HttpCase(TR.AppCase):
    def setUp(self):
        super().setUp()
        self.tick = 0.6
        self.serve(self.app)

    def serve(self, app, **kw):
        server = R.make_server(("127.0.0.1", 0), app, **kw)
        t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        t.start()
        self.addCleanup(t.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.server = server
        self.port = server.server_address[1]

    def req(self, method, path, body=None, headers=None, token=TOKEN, raw=None, host=None, json_headers=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            h["Authorization"] = "Bearer " + token
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if method == "POST" and json_headers:
            h.update({"Content-Type": "application/json", "X-Hivegrid-Remote": "1"})
        h.update(headers or {})
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None and "Content-Length" not in h:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            payload = r.read()
            self.clock.t += self.tick
            return r, payload
        finally:
            c.close()

    def jreq(self, *a, **kw):
        r, payload = self.req(*a, **kw)
        return r, (json.loads(payload) if payload and r.getheader("Content-Type") == "application/json" else None)


class TestHeaders(HttpCase):
    def check_headers(self, r):
        for name, value in R.SECURITY_HEADERS.items():
            if name == "Cache-Control" and r.getheader("ETag") and r.status == 200 and not r.getheader("Content-Type") == "application/json":
                value = "no-cache"  # the static files revalidate by ETag
            self.assertEqual(r.getheader(name), value, name)
        self.assertNotIn("unsafe-inline", r.getheader("Content-Security-Policy"))
        self.assertNotIn("unsafe-eval", r.getheader("Content-Security-Policy"))
        for name, _ in r.getheaders():
            self.assertFalse(name.lower().startswith("access-control-"), name)

    def test_every_response_carries_them(self):
        cases = (("GET", "/", None), ("GET", "/app.js", None), ("GET", "/theme.css", None), ("GET", "/healthz", None),
                 ("GET", "/api/meta", None), ("GET", "/api/agents", None), ("GET", "/api/agents/3/tail", None),
                 ("GET", "/nope", None), ("GET", "/api/meta", "bad"), ("POST", "/api/agents/3/ack", None),
                 ("OPTIONS", "/api/meta", None), ("PUT", "/", None), ("DELETE", "/api/agents", None))
        for method, path, token in cases:
            with self.subTest(method=method, path=path, token=token):
                r, _ = self.req(method, path, token=TOKEN if token is None else token)
                self.check_headers(r)
        r, _ = self.req("GET", "/", host="evil.example")
        self.assertEqual(r.status, 403)
        self.check_headers(r)

    def test_content_types(self):
        for path, ctype in (("/", "text/html; charset=utf-8"), ("/app.js", "text/javascript; charset=utf-8"),
                            ("/app.css", "text/css; charset=utf-8"), ("/theme.css", "text/css; charset=utf-8"),
                            ("/manifest.webmanifest", "application/manifest+json"), ("/api/meta", "application/json")):
            r, body = self.req("GET", path, token=None if path != "/api/meta" else TOKEN)
            self.assertEqual((r.status, r.getheader("Content-Type")), (200, ctype), path)
            self.assertEqual(int(r.getheader("Content-Length")), len(body))

    def test_server_does_not_name_itself_after_python(self):
        r, _ = self.req("GET", "/healthz", token=None)
        self.assertNotIn("Python", r.getheader("Server"))


class TestAuthOverHttp(HttpCase):
    def test_pages_open_api_closed(self):
        self.assertEqual(self.req("GET", "/", token=None)[0].status, 200)
        r, payload = self.req("GET", "/api/agents", token=None)
        self.assertEqual(r.status, 401)
        self.assertEqual(r.getheader("WWW-Authenticate"), "Bearer")
        self.assertEqual(json.loads(payload)["error"], "unauthorized")
        self.assertEqual(self.req("GET", "/api/agents", token="wrong")[0].status, 401)
        self.assertEqual(self.req("GET", "/api/agents")[0].status, 200)

    def test_token_only_in_the_header(self):
        r, _ = self.req("GET", f"/api/agents?token={TOKEN}&access_token={TOKEN}", token=None)
        self.assertEqual(r.status, 401)
        r, _ = self.req("GET", "/api/agents", token=None, headers={"Cookie": f"token={TOKEN}"})
        self.assertEqual(r.status, 401)

    def test_rotation_and_lockout(self):
        new = R.generate_token()
        R.write_token(self.tokfile, new)
        self.assertEqual(self.req("GET", "/api/agents")[0].status, 401)
        self.assertEqual(self.req("GET", "/api/agents", token=new)[0].status, 200)
        self.tick = 0
        for _ in range(6):
            self.req("GET", "/api/agents", token="bad")
        r, payload = self.req("GET", "/api/agents", token="bad")
        self.assertEqual(r.status, 429)
        self.assertEqual(json.loads(payload)["error"], "rate_limited")
        self.assertGreater(int(r.getheader("Retry-After")), 0)
        self.assertEqual(self.req("GET", "/api/agents", token=new)[0].status, 200, "the right token is never locked out")
        self.assertEqual(self.req("GET", "/")[0].status, 200, "the page itself stays reachable")

    def test_hosts(self):
        r, payload = self.req("GET", "/api/agents", host="evil.example")
        self.assertEqual((r.status, json.loads(payload)["error"]), (403, "forbidden_host"))
        self.assertEqual(self.req("GET", "/healthz", host="phone.tailnet.ts.net", token=None)[0].status, 200)
        self.assertEqual(self.req("GET", "/healthz", host="localhost", token=None)[0].status, 200)


class TestPostRules(HttpCase):
    def test_cross_origin_post_is_refused(self):
        r, payload = self.req("POST", "/api/agents/3/ack", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual((r.status, json.loads(payload)["error"]), (403, "forbidden_origin"))
        r, _ = self.req("POST", "/api/agents/3/ack", {}, headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(r.status, 200)
        r, _ = self.req("POST", "/api/agents/3/ack", {}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status, 403)

    def test_header_and_content_type_rules(self):
        self.assertEqual(self.req("POST", "/api/agents/3/ack", {}, json_headers=False)[0].status, 415)
        self.assertEqual(self.req("POST", "/api/agents/3/ack", {}, json_headers=False,
                                  headers={"Content-Type": "application/json"})[0].status, 403)
        self.assertEqual(self.req("POST", "/api/agents/3/ack", {}, headers={"Content-Type": "text/plain"})[0].status, 415)
        self.assertEqual(self.req("POST", "/api/agents/3/ack", {}, headers={"Content-Type": "application/x-www-form-urlencoded"})[0].status, 415)

    def test_body_rules(self):
        r, payload = self.req("POST", "/api/agents/3/ack", raw=b"x" * 20000)
        self.assertEqual((r.status, json.loads(payload)["error"]), (413, "too_large"))
        r, _ = self.req("POST", "/api/agents/3/ack", raw=b"{")
        self.assertEqual(r.status, 400)
        r, _ = self.req("POST", "/api/agents/3/ack", raw=b"[]")
        self.assertEqual(r.status, 400)
        r, _ = self.req("POST", "/api/agents/3/ack", {"surprise": 1})
        self.assertEqual(r.status, 400)
        r, payload = self.req("POST", "/api/agents/3/ack", {"a": "b" * 15000})
        self.assertEqual(r.status, 400, "within the limit: an unknown field, not a size error")

    def test_chunked_bodies_are_refused(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            s.sendall((f"POST /api/agents/3/ack HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nAuthorization: Bearer {TOKEN}\r\n"
                       "Content-Type: application/json\r\nX-Hivegrid-Remote: 1\r\nTransfer-Encoding: chunked\r\n\r\n"
                       "2\r\n{}\r\n0\r\n\r\n").encode())
            head = s.recv(4096).decode()
        finally:
            s.close()
        self.assertTrue(head.startswith("HTTP/1.1 411"), head)

    def test_a_missing_length_is_411(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            s.sendall((f"POST /api/agents/3/ack HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nAuthorization: Bearer {TOKEN}\r\n"
                       "Content-Type: application/json\r\nX-Hivegrid-Remote: 1\r\n\r\n").encode())
            head = s.recv(4096).decode()
        finally:
            s.close()
        self.assertTrue(head.startswith("HTTP/1.1 411"), head)

    def test_odd_methods(self):
        for method in ("PUT", "DELETE", "OPTIONS", "PATCH", "TRACE"):
            r, _ = self.req(method, "/api/agents")
            self.assertEqual(r.status, 405, method)
        r, body = self.req("HEAD", "/")
        self.assertEqual((r.status, body), (405, b""))

    def test_duplicate_critical_headers_are_refused(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            s.sendall((f"GET /api/agents HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nAuthorization: Bearer bad\r\n"
                       f"Authorization: Bearer {TOKEN}\r\n\r\n").encode())
            head = s.recv(4096).decode()
        finally:
            s.close()
        self.assertTrue(head.startswith("HTTP/1.1 400"), head)

    def test_the_connection_survives_a_clean_request_and_ends_after_a_refused_body(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            for _ in range(2):
                c.request("GET", "/healthz")
                r = c.getresponse()
                self.assertEqual((r.status, r.read()), (200, b"ok"))
            body = b"x" * 5000
            c.request("POST", "/api/agents/3/ack", body, {"Authorization": "Bearer bad", "Content-Type": "application/json",
                                                           "X-Hivegrid-Remote": "1"})
            r = c.getresponse()
            self.assertEqual(r.status, 401)
            r.read()
            self.assertEqual(r.getheader("Connection"), "close")
        finally:
            c.close()


class TestFlows(HttpCase):
    def test_list_etag_and_tail(self):
        r, payload = self.req("GET", "/api/agents")
        data = json.loads(payload)
        self.assertEqual([a["id"] for a in data["agents"]], [3])
        etag = r.getheader("ETag")
        r2, body = self.req("GET", "/api/agents", headers={"If-None-Match": etag})
        self.assertEqual((r2.status, body), (304, b""))
        self.assertEqual(r2.getheader("ETag"), etag)
        self.fake.replies["get-text"] = "line one\nline two\n"
        r3, data3 = self.jreq("GET", "/api/agents/3/tail?lines=20")
        self.assertEqual((r3.status, data3["lines"]), (200, ["line one", "line two"]))
        r4, body4 = self.req("GET", "/api/agents/3/tail?lines=20", headers={"If-None-Match": r3.getheader("ETag")})
        self.assertEqual((r4.status, body4), (304, b""))

    def test_prompt_keys_plan_ack(self):
        r, data = self.jreq("POST", "/api/agents/3/prompt", {"text": "hello"})
        self.assertEqual((r.status, data["message"]), (200, "sent"))
        self.assertEqual(self.sends(), [("send-text", "--pane-id", "3", "--", "hello"),
                                        ("send-text", "--pane-id", "3", "--no-paste", "\r")])
        self.fake.calls.clear()
        r, data = self.jreq("POST", "/api/agents/3/keys", {"keys": ["esc", "y"]})
        self.assertEqual((r.status, data["sent"]), (200, 2))
        self.assertEqual([c[-1] for c in self.sends()], ["\x1b", "y"])
        r, data = self.jreq("POST", "/api/agents/3/keys", {"keys": ["ctrl-c"]})
        self.assertEqual((r.status, data["error"], data["dangerous"]), (409, "confirm_required", ["ctrl-c"]))
        self.write_state(3, state="attention", pending={"kind": "plan_review", "reviewId": "r", "title": "t", "choices": []})
        r, _ = self.jreq("POST", "/api/agents/3/plan", {"choice": "execute"})
        self.assertEqual(r.status, 200)
        self.assertTrue([f for f in os.listdir(self.dir) if ".cmd." in f])
        self.write_state(3, state="done", since=TR.NOW - 5)
        self.clock.t += 1
        self.assertEqual(self.jreq("POST", "/api/agents/3/ack", {})[0].status, 200)
        with open(os.path.join(self.dir, "acks.json")) as f:
            self.assertEqual(json.load(f), {"3": TR.NOW - 5})

    def test_refused_states(self):
        self.write_state(3, state="attention", pending={"kind": "approval", "tool": "bash", "summary": "x"})
        r, data = self.jreq("POST", "/api/agents/3/prompt", {"text": "x"})
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertEqual(self.jreq("POST", "/api/agents/3/keys", {"keys": ["y"]})[0].status, 200)
        self.assertEqual(self.jreq("POST", "/api/agents/42/keys", {"keys": ["y"]})[0].status, 404)

    def test_no_mux_is_503(self):
        app = R.App(R.Mux(locate=lambda: None, fg=lambda: {}, clock=self.clock), R.TokenStore(self.tokfile), clock=self.clock,
                    log=self.logs.append)
        self.serve(app)
        r, data = self.jreq("GET", "/api/agents")
        self.assertEqual((r.status, data["error"]), (503, "no_mux"))
        self.assertEqual(self.req("GET", "/")[0].status, 200)

    def test_gui_restart_is_survived(self):
        gone = [True]
        located = []

        def locate():
            located.append(1)
            return None if gone[0] else ("/s", "/e")
        mux = R.Mux(locate=locate, make_run=lambda e, s: self.fake, now=lambda: TR.NOW, fg=lambda: {}, clock=self.clock,
                    exists=lambda p: True)
        self.serve(R.App(mux, R.TokenStore(self.tokfile), clock=self.clock, log=self.logs.append))
        self.assertEqual(self.jreq("GET", "/api/agents")[1]["error"], "no_mux")
        gone[0] = False
        r, data = self.jreq("GET", "/api/agents")
        self.assertEqual((r.status, len(data["agents"])), (200, 1))

    def test_read_only_server(self):
        self.serve(self.make_app(readonly=True))
        for path, body in (("/api/agents/3/prompt", {"text": "x"}), ("/api/agents/3/keys", {"keys": ["y"]}), ("/api/agents/3/ack", {})):
            r, data = self.jreq("POST", path, body)
            self.assertEqual((r.status, data["error"]), (403, "readonly"), path)
        self.assertEqual(self.sends(), [])
        r, data = self.jreq("GET", "/api/meta")
        self.assertEqual((r.status, data["readonly"]), (200, True))
        self.assertEqual(self.req("GET", "/api/agents")[0].status, 200)


class TestInertData(HttpCase):
    PAYLOADS = ("<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "\x1b]0;pwned\x07\x1b[2J</title><svg onload=1>")

    def test_hostile_titles_and_tails_stay_json_text(self):
        for payload in self.PAYLOADS:
            self.write_state(3, title=payload, cwd="/x/" + payload, reason=payload)
            self.fake.replies["get-text"] = payload + "\n"
            self.clock.t += 1
            self.fake.panes = [pane(3, payload)]
            self.app.mux.invalidate()
            for path in ("/api/agents", "/api/agents/3/tail"):
                r, body = self.req("GET", path)
                self.assertEqual(r.getheader("Content-Type"), "application/json", path)
                self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
                self.assertNotIn(b"\x1b", body)
                json.loads(body)
        data = self.jreq("GET", "/api/agents/3/tail")[1]
        self.assertIn("<svg onload=1>", data["lines"][0], "markup is data, not removed: the UI sets text only")
        self.assertNotIn("\x1b", json.dumps(data))


class TestConnections(HttpCase):
    def test_a_client_cannot_hold_more_connections_than_the_peer_cap(self):
        self.serve(self.app, peer_cap=1)
        first = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        second = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            first.request("GET", "/healthz", headers={"Host": f"127.0.0.1:{self.port}"})
            r = first.getresponse()
            self.assertEqual((r.status, r.read()), (200, b"ok"))      # keep-alive: this connection now counts
            second.request("GET", "/healthz", headers={"Host": f"127.0.0.1:{self.port}"})
            r2 = second.getresponse()
            self.assertEqual(r2.status, 429)
            self.assertEqual(json.loads(r2.read())["error"], "rate_limited")
            self.assertEqual(r2.getheader("Connection"), "close")
        finally:
            first.close()
            second.close()
        for _ in range(200):            # the slot comes back once the first connection is gone
            r, _ = self.req("GET", "/healthz", token=None)
            if r.status == 200:
                break
            time.sleep(0.02)
        self.assertEqual(r.status, 200)

    def test_clients_behind_the_proxy_have_their_own_allowance(self):
        self.serve(self.app, peer_cap=1)
        first = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        second = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            for conn, who in ((first, "100.64.0.1"), (second, "100.64.0.2")):
                conn.request("GET", "/healthz", headers={"Host": f"127.0.0.1:{self.port}", "X-Forwarded-For": who})
                r = conn.getresponse()
                self.assertEqual((r.status, r.read()), (200, b"ok"), who)
        finally:
            first.close()
            second.close()

    def test_slow_headers_hit_the_absolute_deadline(self):
        with unittest.mock.patch.object(R, "HEADER_DEADLINE", 0.4):
            s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            try:
                start, closed = time.monotonic(), None
                s.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n")
                for _ in range(60):          # one byte at a time, never finishing the headers
                    time.sleep(0.05)
                    try:
                        s.sendall(b"X")
                    except OSError:
                        closed = time.monotonic() - start
                        break
                    if select.select([s], [], [], 0)[0]:
                        s.recv(100)
                        closed = time.monotonic() - start
                        break
            finally:
                s.close()
        self.assertIsNotNone(closed, "the server hung up on the slow client")
        self.assertLess(closed, 1.5)


class TestServerLimits(HttpCase):
    def test_extra_connections_are_dropped_at_the_cap(self):
        server = R.RemoteServer(("127.0.0.1", 0), type("H", (R.Handler,), {"app": self.app}), cap=1)
        t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        t.start()
        self.addCleanup(t.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        hold = socket.create_connection(("127.0.0.1", port), timeout=5)  # occupies the only slot (a silent client)
        try:
            for _ in range(50):
                if not server._slots.acquire(blocking=False):
                    break
                server._slots.release()
                threading.Event().wait(0.02)
            second = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                second.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                self.assertEqual(second.recv(100), b"", "closed without an answer")
            finally:
                second.close()
        finally:
            hold.close()

    def test_a_ipv6_loopback_bind_works_when_available(self):
        if not socket.has_ipv6:
            self.skipTest("no IPv6")
        try:
            server = R.make_server(("::1", 0), self.app)
        except OSError:
            self.skipTest("no IPv6 loopback")
        server.server_close()
        self.assertEqual(server.address_family, socket.AF_INET6)


if __name__ == "__main__":
    unittest.main()
