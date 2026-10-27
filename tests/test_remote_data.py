"""hivegrid-remote's data layer (bin/hivegrid_remote_data.py) and the endpoints built on it: session
file confinement, the chat parser, accounts, system metrics, new-session choices, long polls, model
switching and the static assets. Temp directories and fakes only: no real home, /proc state, GPU or network."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_agents as A  # noqa: E402
import hivegrid_remote as R  # noqa: E402
import hivegrid_remote_data as D  # noqa: E402
import test_remote as TR  # noqa: E402
from test_agents import DEAD_PID, NOW, pane  # noqa: E402

TS = "2026-10-27T10:00:00.000Z"
TS_EPOCH = 1793095200.0


def jl(*objs):
    return "".join((o if isinstance(o, str) else json.dumps(o)) + "\n" for o in objs)


def msg(role, content, **kw):
    return {"type": "message", "id": "x", "timestamp": TS, "message": {"role": role, "content": content, **kw}}


def text(s):
    return [{"type": "text", "text": s}]


def call(cid, name="bash", **args):
    return {"type": "toolCall", "id": cid, "name": name, "arguments": args}


def result(cid, s, error=False):
    return msg("toolResult", text(s), toolCallId=cid, toolName="bash", isError=error)


class HomeCase(unittest.TestCase):
    """A temp HOME with ~/.omp, and a parser that reads the way production does."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.realpath(self.tmp.name)
        self.cache = D.ChatCache(R.sanitize_text, R.sanitize_line)

    def session(self, name="a.jsonl", body="", profile="beta"):
        base = (os.path.join(self.home, ".omp", "profiles", profile, "agent", "sessions", "-proj") if profile
                else os.path.join(self.home, ".omp", "agent", "sessions", "-proj"))
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def append(self, path, body):
        with open(path, "a", encoding="utf-8") as f:
            f.write(body)

    def items(self, path, limit=200):
        return self.cache.window(path, limit)[0]


class TestSessionPaths(HomeCase):
    def test_only_session_jsonl_below_dot_omp(self):
        ok = [self.session(), self.session("b.jsonl", profile=None)]
        for path in ok:
            self.assertEqual(D.session_path_ok(path, self.home), path)
        other = os.path.join(self.home, "elsewhere.jsonl")
        open(other, "w").close()
        for bad in (other, self.session("c.txt"), "relative/x.jsonl", "", None, 5, os.path.join(self.home, ".omp", "nope.jsonl"),
                    ok[0] + "\0", "/etc/passwd", "x" * 5000 + ".jsonl"):
            with self.subTest(bad=bad):
                self.assertIsNone(D.session_path_ok(bad, self.home))
        agent_dir = os.path.join(self.home, ".omp", "profiles", "beta", "agent")
        stray = os.path.join(agent_dir, "stray.jsonl")        # below ~/.omp but not in a sessions directory
        open(stray, "w").close()
        self.assertIsNone(D.session_path_ok(stray, self.home))
        os.makedirs(os.path.join(self.home, ".omp", "profiles", "beta", "sessions"))
        shallow = os.path.join(self.home, ".omp", "profiles", "beta", "sessions", "s.jsonl")
        open(shallow, "w").close()
        self.assertIsNone(D.session_path_ok(shallow, self.home), "profiles/<p>/agent/sessions only")

    def test_directories_and_other_owners_are_refused(self):
        path = self.session()
        self.assertIsNone(D.session_path_ok(os.path.dirname(path) + ".jsonl", self.home))
        os.mkdir(path + ".d.jsonl")
        self.assertIsNone(D.session_path_ok(path + ".d.jsonl", self.home))
        self.assertIsNone(D.session_path_ok(path, self.home, uid=os.getuid() + 1))

    def test_symlinks_may_not_lead_out(self):
        secret = os.path.join(self.home, "secret.jsonl")
        with open(secret, "w") as f:
            f.write("{}\n")
        link = os.path.join(self.home, ".omp", "profiles", "beta", "agent", "sessions", "-proj", "link.jsonl")
        os.makedirs(os.path.dirname(link))
        os.symlink(secret, link)
        self.assertIsNone(D.session_path_ok(link, self.home))
        inside = self.session("real.jsonl")
        os.symlink(inside, os.path.join(os.path.dirname(link), "alias.jsonl"))
        self.assertEqual(D.session_path_ok(os.path.join(os.path.dirname(link), "alias.jsonl"), self.home), inside,
                         "a link that stays inside is resolved to the real file")
        evil_dir = os.path.join(self.home, ".omp", "profiles", "beta", "agent", "sessions", "dirlink")
        outside = os.path.join(self.home, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "x.jsonl"), "w") as f:
            f.write("{}\n")
        os.symlink(outside, evil_dir)
        self.assertIsNone(D.session_path_ok(os.path.join(evil_dir, "x.jsonl"), self.home))

    def agent(self, **kw):
        d = dict(pane=3, tab=0, window=0, cwd="/x", title="t", state="idle", profile="beta")
        d.update(kw)
        return A.Agent(**d)

    def proc(self, pid, fds=None, tty=None):
        root = os.path.join(self.home, "proc")
        os.makedirs(os.path.join(root, str(pid), "fd"), exist_ok=True)
        for n, target in (fds or {}).items():
            os.symlink(target, os.path.join(root, str(pid), "fd", str(n)))
        if tty:
            os.symlink(tty, os.path.join(root, str(pid), "fd", "0"))
        return root

    def test_found_by_state_file_then_open_file_then_terminal_record(self):
        path = self.session()
        self.assertEqual(D.find_session_file(self.agent(session_file=path), self.home, proc="/nonexistent"), path)
        hint_lost = self.agent(session_file=os.path.join(self.home, "etc.jsonl"), pid=77)
        root = self.proc(77, {3: path, 4: "/etc/passwd", 5: os.path.join(self.home, "x.jsonl")})
        self.assertEqual(D.find_session_file(hint_lost, self.home, proc=root), path, "a refused hint falls through to the open files")
        newer = self.session("new.jsonl")
        os.utime(path, (1, 1))
        root = self.proc(78, {3: path, 4: newer})
        self.assertEqual(D.find_session_file(self.agent(pid=78), self.home, proc=root), newer, "the newest held file wins")
        rec_dir = os.path.join(self.home, ".omp", "profiles", "beta", "agent", "terminal-sessions")
        os.makedirs(rec_dir)
        with open(os.path.join(rec_dir, "pts-7"), "w") as f:
            f.write(f"/work\n{path}\nfresh\n")
        root = self.proc(79, tty="/dev/pts/7")
        self.assertEqual(D.find_session_file(self.agent(pid=79), self.home, proc=root), path)
        self.assertIsNone(D.find_session_file(self.agent(pid=79, profile=None), self.home, proc=root), "the default profile has no record")
        with open(os.path.join(rec_dir, "pts-7"), "w") as f:
            f.write("/work\n/etc/passwd\n")
        self.assertIsNone(D.find_session_file(self.agent(pid=79), self.home, proc=root), "a record pointing out is ignored")

    def test_no_pid_no_file(self):
        for pid in (None, 0, -1, True):
            self.assertIsNone(D.find_session_file(self.agent(pid=pid), self.home, proc="/nonexistent"))

    def test_a_hostile_profile_name_cannot_walk_out_of_the_record_directory(self):
        root = self.proc(80, tty="/dev/pts/7")
        self.assertIsNone(D.find_session_file(self.agent(pid=80, profile="../../x"), self.home, proc=root))

    def test_among_several_held_files_the_session_itself_wins(self):
        main = self.session("2026-10-27T10-00-00-000Z_sid12345.jsonl")
        stem = os.path.splitext(main)[0]
        os.makedirs(stem)
        sub = os.path.join(stem, "Sub.jsonl")
        open(sub, "w").close()
        os.utime(main, (1, 1))                                   # the subagent's file is the newest
        root = self.proc(90, {3: main, 4: sub})
        self.assertEqual(D.find_session_file(self.agent(pid=90), self.home, proc=root), main,
                         "a subagent's file below the artifact directory is not the chat")
        other = self.session("2026-10-27T11-00-00-000Z_sid99999.jsonl")
        root = self.proc(91, {3: main, 4: other})
        self.assertEqual(D.find_session_file(self.agent(pid=91), self.home, proc=root), other, "else the newest top-level file")
        self.assertEqual(D.find_session_file(self.agent(pid=91, session_id="sid12345"), self.home, proc=root), main,
                         "the state file's session id names the file")
        rec_dir = os.path.join(self.home, ".omp", "profiles", "beta", "agent", "terminal-sessions")
        os.makedirs(rec_dir)
        with open(os.path.join(rec_dir, "pts-8"), "w") as f:
            f.write(f"/work\n{main}\n")
        root = self.proc(92, {3: main, 4: other}, tty="/dev/pts/8")
        self.assertEqual(D.find_session_file(self.agent(pid=92), self.home, proc=root), main, "the terminal's record beats the clock")


class TestChatParser(HomeCase):
    def test_roles_kinds_notes_and_skips(self):
        head = [
            {"type": "session", "id": "s", "cwd": "/x", "timestamp": TS},
            {"type": "model_change", "timestamp": TS, "model": "anthropic/claude-x"},
            {"type": "thinking_level_change", "timestamp": TS, "thinkingLevel": "high"},
            {"type": "title", "title": "T"}, {"type": "custom", "customType": "x", "data": {}}, {"type": "credential_pin", "hash": "h"},
            {"type": "model_usage", "usage": {}}, {"type": "reset_boundary"}, {"type": "custom_message", "content": "digest"},
        ]
        lines = [msg("user", text("hello")),
                 msg("assistant", [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "answer"},
                                   call("c1", command="ls -la")]),
                 result("c1", "file1\nfile2")]
        path = self.session(body=jl(*head, *lines))
        got = self.items(path)
        self.assertEqual([(i["role"], i["kind"], i["text"]) for i in got], [
            ("note", "note", "model: anthropic/claude-x"), ("note", "note", "thinking: high"), ("user", "text", "hello"),
            ("assistant", "thinking", "hmm"), ("assistant", "text", "answer"), ("tool", "tool", "ls -la")])
        tool = got[-1]["tool"]
        self.assertEqual(tool, {"name": "bash", "summary": "ls -la", "status": "ok", "output": "file1\nfile2"})
        self.assertEqual(got[2]["ts"], TS_EPOCH)
        ns = [i["n"] for i in got]
        self.assertEqual(ns, sorted(set(ns)))
        offset = len(jl(*head, lines[0]).encode())              # the assistant line starts here
        self.assertEqual([i["n"] for i in got[3:]], [offset * 16, offset * 16 + 1, offset * 16 + 2])

    def test_a_result_arriving_later_updates_the_tool_item_in_place(self):
        path = self.session(body=jl(msg("assistant", [call("c1", command="sleep 9")])))
        first = self.cache.window(path, 50)
        self.assertEqual(first[0][0]["tool"]["status"], "running")
        self.append(path, jl(result("c1", "done", error=True)))
        second = self.cache.window(path, 50)
        self.assertEqual((second[0][0]["tool"]["status"], second[0][0]["tool"]["output"]), ("error", "done"))
        self.assertEqual(second[0][0]["n"], first[0][0]["n"], "numbers do not move")
        self.assertNotEqual(first[2], second[2], "and the stamp says something changed")

    def test_incremental_reads_only_the_tail_and_waits_for_a_complete_line(self):
        path = self.session(body=jl(msg("user", text("one"))))
        stamp = self.cache.stamp(path)
        self.assertEqual(self.cache.stamp(path), stamp, "nothing appended, same stamp")
        line = json.dumps(msg("user", text("two")))
        self.append(path, line[:20])
        self.assertEqual(self.cache.stamp(path), stamp, "a half written line is not parsed")
        self.assertEqual(len(self.items(path)), 1)
        self.append(path, line[20:] + "\n")
        self.assertNotEqual(self.cache.stamp(path), stamp)
        self.assertEqual([i["text"] for i in self.items(path)], ["one", "two"])
        with unittest.mock.patch.object(D.ChatCache, "_first", side_effect=AssertionError("re-read the whole file")):
            self.append(path, jl(msg("user", text("three"))))
            self.assertEqual(len(self.items(path)), 3)

    def test_bad_lines_are_skipped(self):
        path = self.session(body=jl("{not json", "[1,2]", "null", "5", {"type": "message", "message": "x"},
                                   {"type": "message", "message": {"role": "user", "content": 5}}, msg("user", text("ok"))))
        self.assertEqual([i["text"] for i in self.items(path)], ["ok"])

    def test_text_is_sanitized_and_capped(self):
        long = "x" * 20000
        path = self.session(body=jl(
            msg("user", text("a\x1b[31mb\x1b]0;title\x07c\u202ed\x00e")),
            msg("assistant", [{"type": "text", "text": long}, call("c1", command="echo\x1b[1m hi\n there")]), result("c1", "o" * 3000)))
        got = self.items(path)
        self.assertEqual(got[0]["text"], "abcde")
        self.assertEqual((len(got[1]["text"]), got[1]["text"][-1]), (12000, "…"))
        self.assertEqual(got[2]["tool"]["summary"], "echo hi there")
        self.assertEqual((len(got[2]["tool"]["output"]), got[2]["tool"]["output"][-1]), (1500, "…"))

    def test_images_become_placeholders_and_empty_text_is_dropped(self):
        path = self.session(body=jl(
            msg("user", [{"type": "text", "text": "look"}, {"type": "image", "data": "AAAA"}, {"type": "text", "text": "   "}]),
            msg("assistant", [call("c1", "read", path="/x.png")]),
            msg("toolResult", [{"type": "image", "data": "BBBB"}, {"type": "text", "text": "shown"}], toolCallId="c1", toolName="read")))
        got = self.items(path)
        self.assertEqual([(i["role"], i["kind"], i["text"]) for i in got],
                         [("user", "text", "look"), ("user", "image", "[image]"), ("tool", "tool", "/x.png"), ("tool", "image", "[image]")])
        self.assertEqual(got[2]["tool"]["output"], "shown")

    def test_blocks_beyond_fifteen_are_dropped_and_a_string_content_is_one_block(self):
        path = self.session(body=jl(msg("assistant", [{"type": "text", "text": f"t{i}"} for i in range(20)]), msg("user", "plain")))
        got = self.items(path)
        self.assertEqual([i["text"] for i in got], [f"t{i}" for i in range(16)] + ["plain"])
        self.assertEqual(len({i["n"] for i in got}), len(got))

    def test_tool_summary_keys_and_missing_names(self):
        path = self.session(body=jl(msg("assistant", [
            call("a", "grep", pattern="foo", path="/p"), call("b", "x", intent="why"), {"type": "toolCall", "id": "c", "arguments": 5},
            {"type": "toolCall", "arguments": {}}])))
        got = self.items(path)
        self.assertEqual([(i["tool"]["name"], i["tool"]["summary"]) for i in got],
                         [("grep", "/p"), ("x", "why"), ("tool", ""), ("tool", "")])

    def test_a_replaced_or_truncated_file_is_read_afresh(self):
        path = self.session(body=jl(msg("user", text("old one")), msg("user", text("old two"))))
        stamp = self.cache.stamp(path)
        with open(path, "w") as f:
            f.write(jl(msg("user", text("new"))))
        self.assertNotEqual(self.cache.stamp(path), stamp)
        self.assertEqual([i["text"] for i in self.items(path)], ["new"])
        os.unlink(path)
        self.assertIsNone(self.cache.stamp(path))
        self.assertIsNone(self.cache.window(path, 10))

    def test_big_files_are_read_from_the_end_and_older_history_on_demand(self):
        lines = [msg("assistant", [call("c0", command="first")])] + [msg("user", text(f"m{i:03d}" + "-" * 80)) for i in range(40)]
        lines.append(result("c0", "late result"))
        path = self.session(body=jl(*lines))
        small = D.ChatCache(R.sanitize_text, R.sanitize_line, initial=1200)
        rows, older, _ = small.window(path, 5)
        self.assertEqual(([i["text"][:4] for i in rows], older), (["m035", "m036", "m037", "m038", "m039"], True))
        self.assertEqual(rows[-1]["tool"] if rows[-1]["tool"] else None, None)
        before = rows[0]["n"]
        more, older, _ = small.window(path, 8, before)
        self.assertEqual(len(more), 8)
        self.assertTrue(all(i["n"] < before for i in more))
        self.assertEqual(more[-1]["n"] < before, True)
        everything, older, _ = small.window(path, 1000, None)
        self.assertEqual((len(everything), older), (41, False), "all read, nothing older")
        self.assertEqual(everything[0]["tool"]["status"], "ok", "a result read first finds its call once the call is read")
        self.assertEqual(everything[0]["tool"]["output"], "late result")
        ns = [i["n"] for i in everything]
        self.assertEqual(ns, sorted(set(ns)))

    def test_the_read_window_is_capped(self):
        path = self.session(body=jl(*[msg("user", text(f"{i:02d}" + "z" * 200)) for i in range(60)]))
        small = D.ChatCache(R.sanitize_text, R.sanitize_line, initial=1000, window_max=3000)
        rows, older, _ = small.window(path, 5)
        self.assertTrue(older, "the first look reads only the tail")
        self.assertLess(len(rows), 60)
        seen = len(rows)
        for _ in range(10):                          # walk back: it stops at the cap, not at the start of the file
            got, older, _ = small.window(path, 1000, rows[0]["n"])
            if not got:
                break
            rows = got + rows
        self.assertFalse(older)
        self.assertLess(len(rows), 60, "the window cap held")
        self.assertGreater(len(rows), seen)
        self.assertEqual([r["n"] for r in rows], sorted({r["n"] for r in rows}))

    def test_a_file_that_grew_too_much_is_parsed_afresh_and_the_cache_is_bounded(self):
        path = self.session(body=jl(msg("user", text("a"))))
        c = D.ChatCache(R.sanitize_text, R.sanitize_line, append_max=100, max_files=2)
        self.assertEqual(len(c.window(path, 10)[0]), 1)
        self.append(path, jl(*[msg("user", text("b" * 50)) for _ in range(5)]))
        self.assertEqual(len(c.window(path, 10)[0]), 6)
        others = [self.session(f"o{i}.jsonl", jl(msg("user", text("x")))) for i in range(3)]
        for o in others:
            c.stamp(o)
        self.assertEqual(len(c._files), 2)

    def test_limit_and_has_older(self):
        path = self.session(body=jl(*[msg("user", text(f"m{i}")) for i in range(10)]))
        rows, older, _ = self.cache.window(path, 4)
        self.assertEqual(([i["text"] for i in rows], older), (["m6", "m7", "m8", "m9"], True))
        rows, older, _ = self.cache.window(path, 10)
        self.assertEqual((len(rows), older), (10, False))
        rows, older, _ = self.cache.window(path, 3, rows[3]["n"])
        self.assertEqual(([i["text"] for i in rows], older), (["m0", "m1", "m2"], False))
        self.assertEqual([i["text"] for i in rows], ["m0", "m1", "m2"])

    def test_returned_items_are_copies(self):
        path = self.session(body=jl(msg("assistant", [call("c1", command="x")])))
        rows = self.items(path)
        rows[0]["tool"]["status"] = "hacked"
        self.assertEqual(self.items(path)[0]["tool"]["status"], "running")


class TestChatBounds(HomeCase):
    """What keeps the parser bounded and honest: a line longer than a read chunk, the item cap, a
    session file that is a tree, a file rewritten in place."""

    def window(self, cache, path, limit=100, before=None):
        """cache.window in a thread, so a loop that never ends fails the test instead of hanging it."""
        out = []
        t = threading.Thread(target=lambda: out.append(cache.window(path, limit, before)), daemon=True)
        t.start()
        t.join(10)
        self.assertFalse(t.is_alive(), "window() did not return")
        return out[0]

    @staticmethod
    def lines(where, giant):
        small = lambda t: msg("user", text(t))
        return {"head": [giant, small("a"), small("b")], "middle": [small("a"), giant, small("b")],
                "tail": [small("a"), small("b"), giant]}[where]

    ORDER = {"head": ["G", "a", "b"], "middle": ["a", "G", "b"], "tail": ["a", "b", "G"]}

    def test_a_line_longer_than_the_chunk_is_parsed_at_the_head_the_middle_and_the_tail(self):
        for where in ("head", "middle", "tail"):
            with self.subTest(where=where):
                path = self.session(f"{where}.jsonl", jl(*self.lines(where, msg("user", text("G" * 5000)))))
                c = D.ChatCache(R.sanitize_text, R.sanitize_line, initial=1000)
                rows, older, _ = self.window(c, path)
                self.assertEqual([i["text"][:1] for i in rows], self.ORDER[where])
                self.assertEqual(sorted(len(i["text"]) for i in rows), [1, 1, 5000])
                self.assertFalse(older)

    def test_walking_back_over_a_long_line_always_makes_progress(self):
        for where in ("head", "middle", "tail"):
            with self.subTest(where=where):
                path = self.session(f"{where}.jsonl", jl(*self.lines(where, msg("user", text("G" * 5000)))))
                c = D.ChatCache(R.sanitize_text, R.sanitize_line, initial=1000)
                got, before, older = [], None, True
                for _ in range(10):
                    rows, older, _ = self.window(c, path, 1, before)
                    got = rows + got
                    if not older:
                        break
                    before = got[0]["n"]
                self.assertEqual([i["text"][:1] for i in got], self.ORDER[where])
                self.assertFalse(older)

    def test_a_line_over_the_cap_is_skipped_with_a_note_and_the_rest_still_reads(self):
        for where in ("head", "middle", "tail"):
            with self.subTest(where=where):
                path = self.session(f"{where}.jsonl", jl(*self.lines(where, msg("user", text("G" * 5000)))))
                c = D.ChatCache(R.sanitize_text, R.sanitize_line, initial=1000, line_max=2000)
                rows, older, _ = self.window(c, path)
                want = [("[line too large]" if t == "G" else t) for t in self.ORDER[where]]
                self.assertEqual([i["text"] for i in rows], want)
                (note,) = [i for i in rows if i["kind"] == "note"]
                self.assertEqual((note["role"], note["ts"], note["tool"]), ("note", None, None))
                ns = [i["n"] for i in rows]
                self.assertEqual(ns, sorted(set(ns)))
                self.assertGreater(note["n"], 0, "n=0 is not a valid `before`")
                self.assertFalse(older)

    def test_items_are_capped_and_the_oldest_go_first(self):
        path = self.session(body=jl(*[msg("user", text(f"m{i:02d}")) for i in range(30)]))
        c = D.ChatCache(R.sanitize_text, R.sanitize_line, items_max=10)
        rows, older, _ = c.window(path, 100)
        self.assertEqual([r["text"] for r in rows], [f"m{i:02d}" for i in range(20, 30)])
        self.assertFalse(older, "the cache is full: nothing older is served")
        self.append(path, jl(msg("user", text("m30"))))
        rows, older, _ = c.window(path, 100)
        self.assertEqual((rows[0]["text"], rows[-1]["text"], len(rows)), ("m21", "m30", 10))
        got, older, _ = c.window(path, 2, rows[3]["n"])
        self.assertEqual(([r["text"] for r in got], older), (["m22", "m23"], True))
        self.assertEqual(c.window(path, 5, rows[0]["n"])[:2], ([], False))

    def test_eviction_keeps_whole_lines(self):
        three = lambda i: msg("assistant", [{"type": "text", "text": f"{i}{c}"} for c in "abc"])
        path = self.session(body=jl(*[three(i) for i in range(4)]))
        c = D.ChatCache(R.sanitize_text, R.sanitize_line, items_max=7)
        self.assertEqual([r["text"] for r in c.window(path, 100)[0]], ["2a", "2b", "2c", "3a", "3b", "3c"])

    def test_a_cache_without_room_for_a_full_line_reports_no_older(self):
        three = lambda i: msg("assistant", [{"type": "text", "text": f"{i}{c}"} for c in "abc"])
        path = self.session(body=jl(*[three(i) for i in range(12)]))
        c = D.ChatCache(R.sanitize_text, R.sanitize_line, items_max=D.BLOCKS_PER_LINE + 2)
        rows, older, _ = c.window(path, 1000)
        self.assertFalse(older)
        self.assertEqual(c.window(path, 1000, rows[0]["n"])[:2], ([], False))

    def test_deeply_nested_lines_are_skipped(self):
        path = self.session(body=jl(msg("user", text("a"))) + "[" * 100000 + "\n" + jl(msg("user", text("b"))))
        rows, _, _ = self.cache.window(path, 10)
        self.assertEqual([r["text"] for r in rows], ["a", "b"])

    def test_reading_back_stops_at_the_cap_without_a_gap(self):
        path = self.session(body=jl(*[msg("user", text(f"m{i:02d}" + "-" * 80)) for i in range(40)]))
        c = D.ChatCache(R.sanitize_text, R.sanitize_line, initial=1000, items_max=D.BLOCKS_PER_LINE + 12)
        rows, older, _ = c.window(path, 5)
        self.assertTrue(older)
        more, older, _ = c.window(path, 100, rows[0]["n"])
        got = [r["text"][:3] for r in more + rows]
        self.assertEqual(got, [f"m{i:02d}" for i in range(40 - len(got), 40)], "no gap")
        self.assertFalse(older)
        self.assertLessEqual(len(c._files[path].items), D.BLOCKS_PER_LINE + 12)
        self.assertLess(len(got), 40)

    def test_before_zero_reads_nothing(self):
        path = self.session(body=jl(*[msg("user", text(f"m{i:02d}" + "-" * 80)) for i in range(40)]))
        c = D.ChatCache(R.sanitize_text, R.sanitize_line, initial=1000)
        c.window(path, 5)
        start = c._files[path].start
        self.assertEqual(c.window(path, 5, 0)[:2], ([], False))
        self.assertEqual(c._files[path].start, start, "nothing older was read for it")

    def test_a_rewrite_in_place_at_the_same_size_is_noticed(self):
        path = self.session(body=jl(*[msg("user", text(t)) for t in ("aaa", "bbb", "ccc")]))
        self.assertEqual([i["text"] for i in self.items(path)], ["aaa", "bbb", "ccc"])
        size = os.path.getsize(path)
        with open(path, "w") as f:                               # same inode, same length, other words
            f.write(jl(*[msg("user", text(t)) for t in ("xxx", "yyy", "zzz")]))
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
        self.assertEqual(os.path.getsize(path), size)
        self.assertEqual([i["text"] for i in self.items(path)], ["xxx", "yyy", "zzz"])

    @staticmethod
    def entry(eid, parent, role, s):
        return {"type": "message", "id": eid, "parentId": parent, "timestamp": TS, "message": {"role": role, "content": text(s)}}

    def test_the_chat_follows_the_branch_of_the_last_message(self):
        e = self.entry
        legacy = {"type": "message", "timestamp": TS, "message": {"role": "user", "content": text("no id")}}
        path = self.session(body=jl(
            legacy, {"type": "session", "id": "root0001", "timestamp": TS},
            e("a", None, "user", "q1"), e("b", "a", "assistant", "A1"),
            e("c", "b", "user", "abandoned question"), e("d", "c", "assistant", "abandoned answer"),
            e("e", "b", "user", "q2"), e("f", "e", "assistant", "A2"),
            {"type": "model_usage", "id": "g", "parentId": "d", "usage": {}}))     # metadata that hangs on the old branch
        texts = lambda: [i["text"] for i in self.items(path)]
        self.assertEqual(texts(), ["no id", "q1", "A1", "q2", "A2"])
        self.append(path, jl(e("h", "f", "user", "q3")))
        self.assertEqual(texts(), ["no id", "q1", "A1", "q2", "A2", "q3"])
        self.append(path, jl(e("i", "b", "user", "q2 again")))                     # back to A1 and on
        self.assertEqual(texts(), ["no id", "q1", "A1", "q2 again"])


class TestAccountsData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.q = self.tmp.name

    def quota(self, label, doc):
        with open(os.path.join(self.q, f"{label}.json"), "w") as f:
            f.write(doc if isinstance(doc, str) else json.dumps(doc))

    def agent(self, pane_id, profile, tokens=None, cost=None, kind=None):
        usage = A.Usage(tokens, cost, kind) if tokens is not None or cost is not None else None
        return A.Agent(pane=pane_id, tab=0, window=0, cwd="/x", title="t", state="idle", profile=profile, usage=usage)

    def test_numbers_only_from_the_cache_and_windows_that_reset_read_zero(self):
        self.quota("acct", {"label": "acct", "state": "ok", "updated_at": NOW - 5, "access_token": "SECRET", "hist": {"a": 1},
                            "five_hour": {"pct": 2.0, "reset_at": NOW + 100, "extra": "x"}, "seven_day": {"pct": 30, "reset_at": NOW - 1}})
        got = D.read_quota("acct", NOW, self.q)
        self.assertEqual(got, {"state": "ok", "five_hour": {"pct": 2.0, "reset_at": NOW + 100},
                               "seven_day": {"pct": 0.0, "reset_at": None}, "updated_at": NOW - 5})
        self.assertNotIn("SECRET", json.dumps(got))

    def test_missing_garbled_and_odd_labels_read_unknown(self):
        self.quota("bad", "{nope")
        self.quota("arr", "[1]")
        self.quota("weird", {"state": "who knows", "five_hour": {"pct": "x"}, "updated_at": "now"})
        unknown = {"state": "unknown", "five_hour": None, "seven_day": None, "updated_at": None}
        for label in ("absent", "bad", "arr", "../x", "", None, "a" * 40):
            with self.subTest(label=label):
                self.assertEqual(D.read_quota(label, NOW, self.q), unknown)
        self.assertEqual(D.read_quota("weird", NOW, self.q), unknown)

    def test_accounts_sum_the_usage_of_their_agents_without_exposing_dirs(self):
        self.quota("acct", {"state": "login", "five_hour": {"pct": 1.0, "reset_at": None}})
        accounts = [{"label": "acct", "profile": "acct", "dir": "~/.claude-acct", "hidden": False},
                    {"label": "gone", "profile": "gone", "dir": "~/.claude-gone", "hidden": True},
                    {"label": "main", "profile": None, "dir": "~/.claude"}, "junk", {"profile": "x"}]
        agents = [self.agent(1, "acct", 100, 1.0, "reported"), self.agent(2, "acct", 50, 0.5, "estimated"),
                  self.agent(3, "other", 10), self.agent(4, None, 7), self.agent(5, "acct")]
        data = D.accounts_json(accounts, agents, NOW, R.sanitize_line, self.q)
        self.assertEqual([a["label"] for a in data["accounts"]], ["acct", "main"], "hidden accounts stay hidden")
        acct, main = data["accounts"]
        self.assertEqual((acct["state"], acct["agents"], acct["tokens"], acct["cost"], acct["cost_kind"]), ("login", 3, 150.0, 1.5, "mixed"))
        self.assertEqual((main["state"], main["agents"], main["tokens"], main["cost"], main["cost_kind"]), ("unknown", 0, None, None, None))
        self.assertEqual(data["totals"]["agents"], 4)
        self.assertEqual(data["totals"]["tokens"], 167.0)
        self.assertEqual(data["now"], NOW)
        self.assertNotIn(".claude", json.dumps(data), "no directories")
        self.assertEqual(set(acct), {"label", "profile", "state", "five_hour", "seven_day", "updated_at", "agents", "tokens", "cost", "cost_kind"})

    def test_cost_kind_follows_what_was_reported(self):
        acc = [{"label": "a", "profile": "a"}]
        for usages, want in (([(1, 2.0, "reported")], "reported"), ([(1, 2.0, "estimated")], "estimated"), ([(1, None, None)], None)):
            agents = [self.agent(i, "a", *u) for i, u in enumerate(usages)]
            row = D.accounts_json(acc, agents, NOW, R.sanitize_line, self.q)["accounts"][0]
            self.assertEqual(row["cost_kind"], want)


class TestSysStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = os.path.join(self.tmp.name, "proc")
        os.makedirs(self.proc)
        self.t = 100.0
        self.runs = []
        self.busy, self.idle = 500, 500
        self.stat("cpu  500 0 0 500 0 0 0 0 0 0\n")
        self.meminfo = "MemTotal: 1000 kB\nMemFree: 1 kB\nMemAvailable: 250 kB\n"
        for name, body in (("meminfo", self.meminfo), ("uptime", "123.45 99.0\n")):
            with open(os.path.join(self.proc, name), "w") as f:
                f.write(body)
        self.out = "NVIDIA Test GPU, 3, 14, 16303\n"
        self.log = os.path.join(self.tmp.name, "sysstats.jsonl")

    def stat(self, body):
        with open(os.path.join(self.proc, "stat"), "w") as f:
            f.write(body)

    def sleep(self, s):
        """The pause of a fresh measurement: the CPU does 50 busy and 50 idle jiffies meanwhile."""
        self.t += s
        self.busy, self.idle = self.busy + 50, self.idle + 50
        self.stat(f"cpu  {self.busy} 0 0 {self.idle} 0 0 0 0 0 0\n")

    def smi(self, cmd, **kw):
        self.runs.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, self.out, "")

    def make(self, **kw):
        kw.setdefault("which", lambda n: "/usr/bin/" + n)
        return D.SysStats(proc=self.proc, clock=lambda: self.t, sleep=self.sleep, wall=lambda: NOW, run=self.smi,
                          disk_path=self.tmp.name, log_path=self.log, **kw)

    def test_cpu_is_a_delta_and_never_waits_long(self):
        s = self.make()
        self.assertEqual(s.cpu(), 50.0, "the first call measures over a short pause")
        self.assertAlmostEqual(self.t, 100.15)
        self.assertEqual(s.cpu(), 50.0, "a call right after reuses the reading")
        self.assertAlmostEqual(self.t, 100.15)
        self.t += 1
        self.busy, self.idle = self.busy + 10, self.idle + 90
        self.stat(f"cpu  {self.busy} 0 0 {self.idle} 0 0 0 0 0 0\n")
        self.assertEqual(s.cpu(), 10.0, "a later call is the delta against the previous call, without a pause")
        self.assertAlmostEqual(self.t, 101.15)
        self.t += 120
        self.assertEqual(s.cpu(), 50.0, "after a long gap it measures afresh instead of averaging history")

    def test_unreadable_proc(self):
        s = D.SysStats(proc=os.path.join(self.tmp.name, "none"), clock=lambda: 0.0, sleep=lambda s: None, which=lambda n: None,
                       disk_path=self.tmp.name, log_path=self.log)
        snap = s.snapshot()
        self.assertEqual((snap["cpu"], snap["mem"], snap["uptime"], snap["gpu"], snap["history"]), (None, None, None, None, []))

    def test_snapshot_shape(self):
        snap = self.make().snapshot()
        self.assertEqual(set(snap), {"ts", "cpu", "cores", "load", "mem", "disk", "gpu", "uptime", "history"})
        self.assertEqual(snap["mem"], {"used": 750 * 1024, "total": 1000 * 1024, "pct": 75.0})
        self.assertEqual(snap["uptime"], 123.45)
        self.assertEqual(set(snap["disk"]), {"pct", "free"})
        self.assertTrue(0 <= snap["disk"]["pct"] <= 100)
        self.assertEqual(snap["gpu"], {"name": "NVIDIA Test GPU", "util": 3.0, "mem_used": 14 * 1048576.0, "mem_total": 16303 * 1048576.0})
        self.assertEqual(len(snap["load"]), 3)

    def test_gpu_is_cached_failures_are_null_and_slots_are_respected(self):
        s = self.make()
        s.gpu()
        self.t += 2
        s.gpu()
        self.assertEqual(len(self.runs), 1, "cached for 3 s")
        self.t += 2
        s.gpu()
        self.assertEqual(len(self.runs), 2)
        none = D.SysStats(proc=self.proc, clock=lambda: self.t, run=self.smi, which=lambda n: None)
        self.assertIsNone(none.gpu())
        self.assertEqual(len(self.runs), 2, "no nvidia-smi: not run")
        slots = threading.BoundedSemaphore(1)
        slots.acquire()
        busy = self.make(slots=slots)
        self.assertIsNone(busy.gpu())
        self.assertEqual(len(self.runs), 2, "no free subprocess slot: not run")
        slots.release()
        ok = self.make(slots=slots)
        self.assertIsNotNone(ok.gpu())
        self.assertTrue(slots.acquire(blocking=False), "the slot is given back")
        slots.release()
        self.out = "garbage\n"
        self.t += 10
        self.assertIsNone(ok.gpu())

        def boom(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 2)
        self.smi = boom
        self.t += 100
        self.assertIsNone(self.make().gpu())

    def test_a_busy_slot_is_retried_on_the_next_call_not_in_a_minute(self):
        slots = threading.BoundedSemaphore(1)
        slots.acquire()
        s = self.make(slots=slots)
        self.assertIsNone(s.gpu())
        slots.release()
        self.assertIsNotNone(s.gpu(), "the very next call looks again")
        self.assertEqual(len(self.runs), 1)

    def test_history_is_the_log_tail_of_the_last_six_hours_downsampled(self):
        rows = [{"ts": NOW - 7 * 3600, "cpu": 1, "ram": 1, "dsk": 1, "gpu": 1}] + [
            {"ts": NOW - 3600 + i * 10, "cpu": float(i % 100), "ram": 50, "dsk": 60, "gpu": None} for i in range(300)]
        with open(self.log, "w") as f:
            f.write("not json\n" + "\n".join(json.dumps(r) for r in rows) + '\n{"ts": "x"}\n[1]\n{"ts":' + str(NOW) + ',"cpu":"x"}\n')
        got = self.make().history(NOW)
        self.assertLessEqual(len(got), 120)
        self.assertGreater(len(got), 100)
        self.assertEqual(got[-1]["ts"], NOW)
        self.assertTrue(all(g["ts"] >= NOW - 6 * 3600 for g in got), "older than six hours is dropped")
        self.assertEqual(got[-1]["cpu"], None, "a non-number is null")
        self.assertEqual(sorted(got[0]), ["cpu", "dsk", "gpu", "ram", "ts"])
        self.assertEqual(self.make().history(NOW + 10 * 3600), [], "stale log: nothing in the window")

    def test_history_reads_only_the_tail_of_a_big_log(self):
        line = json.dumps({"ts": NOW, "cpu": 1, "ram": 1, "dsk": 1, "gpu": 1}) + "\n"
        with open(self.log, "w") as f:
            f.write(line * (D.HISTORY_TAIL // len(line) * 4))
        with unittest.mock.patch("builtins.open", wraps=open) as op:
            got = self.make().history(NOW)
        self.assertTrue(got)
        self.assertEqual(op.call_count, 1)
        self.assertEqual(D.history_points("", NOW), [])

    def test_missing_log(self):
        self.assertEqual(self.make().history(NOW), [])

    def test_parsers(self):
        self.assertEqual(D.parse_cpu("cpu  1 2 3 4 5 6 7 8 9 10\ncpu0 1"), (36, 9))
        for junk in ("", None, "cpu  1 2", "intr 1"):
            self.assertIsNone(D.parse_cpu(junk))
        self.assertIsNone(D.parse_meminfo("MemTotal: 0 kB\nMemAvailable: 0 kB"))
        self.assertIsNone(D.parse_meminfo(None))
        self.assertIsNone(D.parse_gpu(""))
        self.assertIsNone(D.parse_gpu("a, b, c"))
        self.assertEqual(D.parse_gpu("GeForce, Laptop, 5, 1, 2")["name"], "GeForce, Laptop")


class TestSessionOptions(HomeCase):
    def acc(self):
        return [{"label": "acct", "profile": "acct"}, {"label": "main", "profile": None}, {"label": "hid", "profile": "hid", "hidden": True},
                {"label": "acct", "profile": "dup"}, {"label": "evil", "profile": "a b; rm"}]

    def agent(self, pane_id, cwd):
        return A.Agent(pane=pane_id, tab=0, window=0, cwd=cwd, title="t", state="idle")

    def test_profiles_and_dirs(self):
        projects = os.path.join(self.home, "Projects")
        for name in ("zeta", "Alpha", ".hidden", "bad\nname"):
            os.makedirs(os.path.join(projects, name))
        open(os.path.join(projects, "file"), "w").close()
        live = os.path.join(self.home, "work")
        os.makedirs(live)
        profiles, dirs, real = D.session_options(self.acc(), [self.agent(1, live), self.agent(2, "/does/not/exist"),
                                                              self.agent(3, live + "/"), self.agent(4, "rel")], R.sanitize_line, self.home)
        self.assertEqual(profiles, [{"label": "acct", "profile": "acct"},
                                    {"label": "main", "profile": None, "default": True, "note": "omp's default configuration"}])
        self.assertEqual([d["path"] for d in dirs], ["~", "~/work", "~/Projects/Alpha", "~/Projects/zeta"])
        self.assertEqual([d["label"] for d in dirs], ["~", "work", "Alpha", "zeta"])
        self.assertEqual(real["~"], self.home)
        self.assertEqual(real["~/Projects/zeta"], os.path.join(projects, "zeta"))

    def test_project_directories_are_capped(self):
        for i in range(75):
            os.makedirs(os.path.join(self.home, "Projects", f"p{i:02d}"))
        _p, dirs, real = D.session_options([], [], R.sanitize_line, self.home)
        self.assertEqual(len(dirs), 1 + D.PROJECT_DIRS_MAX)
        self.assertEqual(len(real), len(dirs))

    def test_no_projects_directory(self):
        _p, dirs, _r = D.session_options([], [], R.sanitize_line, self.home)
        self.assertEqual([d["path"] for d in dirs], ["~"])

    def test_accounts_without_a_profile_collapse_into_one_default_entry(self):
        main, two, three, acct = ({"label": "main", "profile": None}, {"label": "2", "profile": None},
                                  {"label": "3", "profile": None}, {"label": "acct", "profile": "acct"})
        prof = lambda accounts: D.session_options(accounts, [], R.sanitize_line, self.home)[0]
        self.assertEqual(prof([two, main, acct]), [{"label": "main", "profile": None, "default": True, "note": "omp's default configuration"},
                                                   {"label": "acct", "profile": "acct"}])
        (only,) = prof([two, three])
        self.assertEqual((only["label"], only["profile"], only["default"]), ("2", None, True))
        self.assertIn("no paired omp profile", only["note"])
        self.assertEqual(prof([acct]), [{"label": "acct", "profile": "acct"}], "a real profile carries no default flag")
        self.assertEqual(prof([]), [])


# ---------------------------------------------------------------- the endpoints

class RichCase(TR.AppCase):
    """AppCase plus a session file for pane 3."""

    def make_session(self, body="", name="s.jsonl", profile="alpha"):
        base = os.path.join(self.home, ".omp", "profiles", profile, "agent", "sessions", "-p")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return os.path.realpath(path)


class TestLongPoll(RichCase):
    def test_agents_without_a_matching_etag_do_not_wait(self):
        r, data = self.call("GET", "/api/agents?wait=20")
        self.assertEqual((r.status, self.slept), (200, []))
        self.assertIn("rev", data)

    def test_a_held_list_answers_304_at_the_timeout(self):
        r, data = self.call("GET", "/api/agents")
        r2, _ = self.call("GET", "/api/agents?wait=5", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual((r2.status, r2.body), (304, b""))
        self.assertEqual(sum(self.slept), 5)
        self.assertEqual(self.slept, [1.0] * 5, "looks about once a second")

    def test_a_held_list_answers_as_soon_as_something_changes(self):
        r, _ = self.call("GET", "/api/agents")
        self.on_sleep = lambda n: self.write_state(3, state="working") if n == 2 else None
        r2, data = self.call("GET", "/api/agents?wait=20", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual((r2.status, data["agents"][0]["state"]), (200, "working"))
        self.assertLessEqual(len(self.slept), 3, "the list cache lives 1.5 s, so a change shows within two looks")

    def test_ages_alone_never_end_a_hold(self):
        self.write_state(3, since=NOW - 3)
        r, _ = self.call("GET", "/api/agents")
        r2, _ = self.call("GET", "/api/agents?wait=20", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual(r2.status, 304, "the age text moved on 20 s, the revision did not")

    def test_a_hold_costs_one_read_token_not_one_per_look(self):
        self.tick = 0
        r, _ = self.call("GET", "/api/agents")
        left = self.app.read_bucket.tokens
        self.call("GET", "/api/agents?wait=20", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual(len(self.slept), 20)
        self.assertAlmostEqual(left - self.app.read_bucket.tokens, 1.0, delta=0.1 + 20 * 15.0 / 1000)

    def test_wait_is_validated_and_capped(self):
        for bad in ("abc", "-1", "1e3", "9999999999999", "1.2.3"):
            r, data = self.call("GET", f"/api/agents?wait={bad}")
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), bad)
        r, _ = self.call("GET", "/api/agents")
        self.call("GET", "/api/agents?wait=500", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual(sum(self.slept), 20)

    def test_the_server_closing_ends_a_hold_at_once(self):
        app = self.make_app(sleep=None)
        r, _ = self.call("GET", "/api/agents", app=app)
        app.close()
        r2, _ = self.call("GET", "/api/agents?wait=20", headers={"if-none-match": r.headers["ETag"]}, app=app)
        self.assertEqual(r2.status, 304)

    def test_tail_holds_too(self):
        self.fake.replies["get-text"] = "one\n"
        r, _ = self.call("GET", "/api/agents/3/tail")
        self.on_sleep = lambda n: self.fake.replies.update({"get-text": "one\ntwo\n"}) if n == 3 else None
        r2, data = self.call("GET", "/api/agents/3/tail?wait=10", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual((r2.status, data["lines"]), (200, ["one", "two"]))
        self.assertEqual(len(self.slept), 3)
        r3, _ = self.call("GET", "/api/agents/3/tail?wait=2", headers={"if-none-match": r2.headers["ETag"]})
        self.assertEqual(r3.status, 304)

    def test_a_hold_ends_with_404_when_the_agent_goes_away(self):
        r, _ = self.call("GET", "/api/agents/3/tail")
        self.on_sleep = lambda n: setattr(self.fake, "panes", [])
        r2, data = self.call("GET", "/api/agents/3/tail?wait=10", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual((r2.status, data["error"]), (404, "not_found"))


class TestChatApi(RichCase):
    def setUp(self):
        super().setUp()
        self.path = self.make_session(jl(msg("user", text("hello")), msg("assistant", [{"type": "text", "text": "hi"}, call("c1", command="ls")])))
        self.write_state(3, state="working", reason="bash (2s)", sessionFile=self.path)

    def chat(self, query="", **kw):
        return self.call("GET", "/api/agents/3/chat" + query, **kw)

    def test_shape(self):
        r, data = self.chat()
        self.assertEqual(r.status, 200)
        self.assertEqual({k: data[k] for k in ("id", "label", "source", "state", "state_label", "reason", "has_older", "older")},
                         {"id": 3, "label": "1", "source": "session", "state": "working", "state_label": "working", "reason": "bash (2s)",
                          "has_older": False, "older": False})
        self.assertRegex(data["rev"], r"^[0-9a-f]{12}$")
        self.assertEqual([(i["role"], i["kind"]) for i in data["items"]], [("user", "text"), ("assistant", "text"), ("tool", "tool")])
        self.assertEqual(data["items"][2]["tool"]["status"], "running")
        self.assertEqual(set(data["items"][0]), {"n", "ts", "role", "kind", "text", "tool"})
        self.assertEqual(r.headers.get("ETag"), None, "windows are replaced whole: no ETag")

    def test_limit_before_and_older(self):
        self.append(self.path, jl(*[msg("user", text(f"m{i}")) for i in range(5)]))
        _, data = self.chat("?limit=3")
        self.assertEqual(([i["text"] for i in data["items"]], data["has_older"]), (["m2", "m3", "m4"], True))
        _, older = self.chat(f"?limit=2&before={data['items'][0]['n']}")
        self.assertEqual(([i["text"] for i in older["items"]], older["older"], older["has_older"]), (["m0", "m1"], True, True))
        _, oldest = self.chat(f"?limit=50&before={older['items'][0]['n']}")
        self.assertEqual([i["role"] for i in oldest["items"]], ["user", "assistant", "tool"])
        self.assertFalse(oldest["has_older"])
        for bad in ("limit=0x", "limit=-1", "before=x", "before=-3", "rev=ZZ", "rev=" + "a" * 40):
            r, d = self.chat("?" + bad)
            self.assertEqual((r.status, d["error"]), (400, "bad_request"), bad)
        self.chat("?limit=0")
        self.assertEqual(len(self.chat("?limit=9999")[1]["items"]), 8, "the limit is clamped to 1..200")
        self.assertEqual(len(self.chat("?limit=0")[1]["items"]), 1)

    def append(self, path, body):
        with open(path, "a", encoding="utf-8") as f:
            f.write(body)

    def test_rev_and_unchanged(self):
        _, first = self.chat()
        r, same = self.chat(f"?rev={first['rev']}")
        self.assertEqual((r.status, same), (200, {"unchanged": True, "rev": first["rev"]}))
        self.assertEqual(self.slept, [], "no wait asked, no wait taken")
        self.write_state(3, state="working", reason="bash (3s)", sessionFile=self.path)
        self.clock.t += 2
        _, moved = self.chat(f"?rev={first['rev']}")
        self.assertNotEqual(moved["rev"], first["rev"], "the reason is part of the revision")
        self.assertIn("items", moved)

    def test_a_held_chat_wakes_on_a_new_line_and_times_out_otherwise(self):
        _, first = self.chat()
        _, quiet = self.chat(f"?rev={first['rev']}&wait=2")
        self.assertEqual((quiet, self.slept), ({"unchanged": True, "rev": first["rev"]}, [0.5] * 4))
        self.slept.clear()
        self.on_sleep = lambda n: self.append(self.path, jl(result("c1", "out"))) if n == 3 else None
        _, woke = self.chat(f"?rev={first['rev']}&wait=20")
        self.assertEqual(len(self.slept), 3)
        self.assertEqual(woke["items"][2]["tool"]["status"], "ok")
        self.assertNotEqual(woke["rev"], first["rev"])
        self.on_sleep = None
        _, again = self.chat(f"?rev={woke['rev']}&wait=1")
        self.assertEqual(again, {"unchanged": True, "rev": woke["rev"]}, "the rev of a response matches the next look")

    def test_a_held_chat_wakes_on_a_state_change(self):
        _, first = self.chat()
        self.on_sleep = lambda n: self.write_state(3, state="idle", reason=None, sessionFile=self.path) if n == 1 else None
        self.clock.t += 5
        _, woke = self.chat(f"?rev={first['rev']}&wait=20")
        self.assertEqual(woke["state"], "idle")

    def test_without_a_session_file_the_page_is_told_to_use_the_screen(self):
        self.write_state(3, state="idle")
        r, data = self.chat()
        self.assertEqual((r.status, data["source"], data["items"], data["has_older"]), (200, "screen", [], False))
        rev = data["rev"]
        self.write_state(3, state="working")
        self.clock.t += 2
        self.assertNotEqual(self.chat()[1]["rev"], rev)

    def test_a_state_file_path_outside_omp_is_not_read(self):
        secret = os.path.join(self.home, "secret.jsonl")
        with open(secret, "w") as f:
            f.write(jl(msg("user", text("TOPSECRET"))))
        self.write_state(3, sessionFile=secret)
        r, data = self.chat()
        self.assertEqual(data["source"], "screen")
        self.assertNotIn(b"TOPSECRET", r.body)
        self.write_state(3, sessionFile="/etc/passwd")
        self.assertEqual(self.chat()[1]["source"], "screen")

    def test_a_vanished_file_falls_back_to_the_screen(self):
        os.unlink(self.path)
        self.assertEqual(self.chat()[1]["source"], "screen")

    def test_session_text_is_data(self):
        self.append(self.path, jl(msg("user", text("<img src=x onerror=alert(1)>\x1b[31m"))))
        r, data = self.chat()
        self.assertEqual(data["items"][-1]["text"], "<img src=x onerror=alert(1)>")
        self.assertNotIn(b"\\u001b", r.body)

    def test_agent_ids_and_methods(self):
        self.assertEqual(self.call("GET", "/api/agents/9/chat")[0].status, 404)
        self.assertEqual(self.call("POST", "/api/agents/3/chat", {})[0].status, 405)

    def test_the_list_says_whether_a_chat_exists(self):
        _, data = self.call("GET", "/api/agents")
        self.assertTrue(data["agents"][0]["session"])
        self.write_state(3, sessionFile=None)
        self.clock.t += 10
        _, data = self.call("GET", "/api/agents")
        self.assertFalse(data["agents"][0]["session"])

    def test_the_log_has_no_chat_text_or_paths(self):
        self.chat()
        self.assertNotIn("hello", " ".join(self.logs))
        self.assertNotIn(self.home, " ".join(self.logs))


class TestAccountsAndSystemApi(RichCase):
    def test_accounts(self):
        Path = TR.Path
        Path(self.settings).write_text(json.dumps({"accounts": [
            {"label": "alpha", "profile": "alpha", "dir": "~/.claude-alpha", "auto": False, "hidden": False},
            {"label": "old", "profile": "old", "dir": "~/.claude-old", "auto": False, "hidden": True}]}))
        quota = os.environ["CLAUDE_QUOTA_CACHE_DIR"]
        os.makedirs(quota)
        with open(os.path.join(quota, "alpha.json"), "w") as f:
            json.dump({"state": "ok", "five_hour": {"pct": 12.0, "reset_at": NOW + 60}, "seven_day": {"pct": 3, "reset_at": NOW + 99},
                       "updated_at": NOW - 1, "token": "SECRETTOKEN"}, f)
        self.write_state(3, usage={"input": 10, "output": 5, "total": 15, "cost": 0.25, "costKind": "estimated"})
        r, data = self.call("GET", "/api/accounts")
        self.assertEqual(r.status, 200)
        (row,) = data["accounts"]
        self.assertEqual((row["label"], row["profile"], row["state"], row["five_hour"]["pct"], row["agents"], row["tokens"], row["cost"]),
                         ("alpha", "alpha", "ok", 12.0, 1, 15.0, 0.25))
        self.assertEqual(data["totals"]["tokens"], 15.0)
        self.assertIn("tok", data["totals"]["text"])
        for secret in (b"SECRETTOKEN", b".claude-alpha", self.home.encode()):
            self.assertNotIn(secret, r.body)
        self.assertEqual(self.call("GET", "/api/accounts", token=None)[0].status, 401)
        self.assertEqual(self.call("POST", "/api/accounts", {})[0].status, 405)

    def test_system(self):
        r, data = self.call("GET", "/api/system")
        self.assertEqual((r.status, data["cpu"], data["mem"]["pct"]), (200, 12.5, 25.0))
        self.assertEqual(self.call("GET", "/api/system", token=None)[0].status, 401)
        self.assertEqual(r.headers.get("ETag"), None)


class TestSessionsApi(RichCase):
    def setUp(self):
        super().setUp()
        Path = TR.Path
        Path(self.settings).write_text(json.dumps({"accounts": [
            {"label": "alpha", "profile": "alpha", "dir": "~/.claude-alpha", "auto": False, "hidden": False},
            {"label": "plain", "profile": None, "dir": "~/.claude", "auto": False, "hidden": False}]}))
        os.makedirs(os.path.join(self.home, "Projects", "proj"))
        self.spawned = []
        patcher = unittest.mock.patch.object(R.A, "spawn_agent", side_effect=self.fake_spawn)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_spawn(self, cwd=None, near=None, run=None, profile=None):
        self.spawned.append((cwd, profile))
        return 41

    def test_options(self):
        r, data = self.call("GET", "/api/sessions/options")
        self.assertEqual(r.status, 200)
        self.assertEqual(data["profiles"], [
            {"label": "alpha", "profile": "alpha"},
            {"label": "plain", "profile": None, "default": True, "note": "omp's default configuration: this account has no paired omp profile"}])
        self.assertEqual([d["path"] for d in data["dirs"]], ["~", "~/Projects/proj"])
        self.assertNotIn(self.home.encode(), r.body, "directories are shown as ~/...")

    def test_start(self):
        r, data = self.call("POST", "/api/sessions", {"profile": "alpha", "dir": "~/Projects/proj"})
        self.assertEqual((r.status, data), (200, {"ok": True, "id": 41}))
        self.assertEqual(self.spawned, [(os.path.join(self.home, "Projects", "proj"), "alpha")])
        r, data = self.call("POST", "/api/sessions", {"profile": None, "dir": "~"})
        self.assertEqual((r.status, self.spawned[-1]), (200, (self.home, None)))
        self.assertEqual(self.logs[-1], "write session account=default", "no paths in the log")
        self.assertIn("write session account=alpha", self.logs)

    def test_only_offered_values_are_accepted(self):
        for body in ({"profile": "alpha", "dir": "/etc"}, {"profile": "alpha", "dir": "~/Projects/../.ssh"}, {"profile": "alpha", "dir": self.home},
                     {"profile": "alpha", "dir": "~/Projects/nope"}, {"profile": "nope", "dir": "~"}, {"profile": "", "dir": "~"},
                     {"profile": "alpha; rm -rf /", "dir": "~"}, {"profile": 5, "dir": "~"}, {"profile": "alpha", "dir": 5},
                     {"profile": "alpha"}, {"dir": ""}, {"profile": "alpha", "dir": "~", "extra": 1}, {"profile": "alpha", "dir": "x" * 400}):
            r, data = self.call("POST", "/api/sessions", body)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), body)
        self.assertEqual(self.spawned, [])

    def test_a_dir_of_a_live_agent_is_offered(self):
        live = os.path.join(self.home, "elsewhere")
        os.makedirs(live)
        self.write_state(3, cwd=live)
        _, data = self.call("GET", "/api/sessions/options")
        self.assertIn("~/elsewhere", [d["path"] for d in data["dirs"]])
        self.assertEqual(self.call("POST", "/api/sessions", {"profile": None, "dir": "~/elsewhere"})[0].status, 200)

    def test_read_only_and_methods(self):
        app = self.make_app(readonly=True)
        r, data = self.call("POST", "/api/sessions", {"profile": None, "dir": "~"}, app=app)
        self.assertEqual((r.status, data["error"]), (403, "readonly"))
        self.assertEqual(self.call("GET", "/api/sessions", app=app)[0].status, 405)
        self.assertEqual(self.call("POST", "/api/sessions/options", {})[0].status, 405)
        self.assertEqual(self.call("GET", "/api/sessions/options", app=app)[0].status, 200)
        self.assertEqual(self.spawned, [])

    def test_failures(self):
        with unittest.mock.patch.object(R.A, "spawn_agent", return_value=None):
            r, data = self.call("POST", "/api/sessions", {"profile": None, "dir": "~"})
        self.assertEqual((r.status, data["error"]), (502, "mux_failed"))
        with unittest.mock.patch.object(R.A, "spawn_agent", side_effect=ValueError("a profile needs the plain omp command")):
            r, data = self.call("POST", "/api/sessions", {"profile": "alpha", "dir": "~"})
        self.assertEqual((r.status, data["error"]), (400, "bad_request"))

    def test_the_same_directory_and_account_is_not_started_twice_in_a_row(self):
        self.tick = 0
        body = {"profile": "alpha", "dir": "~/Projects/proj"}
        self.assertEqual(self.call("POST", "/api/sessions", body)[0].status, 200)
        r, data = self.call("POST", "/api/sessions", body)
        self.assertEqual((r.status, data["error"]), (409, "conflict"))
        self.assertIn("just started", data["message"])
        self.assertGreater(int(r.headers["Retry-After"]), 0)
        self.assertEqual(len(self.spawned), 1, "the second tap did not start another omp")
        self.assertEqual(self.call("POST", "/api/sessions", {"profile": None, "dir": "~/Projects/proj"})[0].status, 200,
                         "another account is another session")
        self.clock.t += R.SESSION_REPEAT
        self.assertEqual(self.call("POST", "/api/sessions", body)[0].status, 200, "and after a while it is allowed again")

    def test_a_failed_start_may_be_tried_again_at_once(self):
        self.tick = 0
        with unittest.mock.patch.object(R.A, "spawn_agent", return_value=None):
            self.assertEqual(self.call("POST", "/api/sessions", {"profile": None, "dir": "~"})[0].status, 502)
        self.assertEqual(self.call("POST", "/api/sessions", {"profile": None, "dir": "~"})[0].status, 200)

    def test_starting_sessions_has_its_own_slow_bucket(self):
        self.tick = 0
        combos = [("alpha", "~"), (None, "~"), ("alpha", "~/Projects/proj"), (None, "~/Projects/proj")]
        post = lambda c: self.call("POST", "/api/sessions", {"profile": c[0], "dir": c[1]})
        self.assertEqual([post(c)[0].status for c in combos], [200, 200, 200, 429], "three at once, then it waits")
        r, data = post(combos[3])
        self.assertEqual((r.status, data["error"]), (429, "rate_limited"))
        self.assertGreater(int(r.headers["Retry-After"]), 0)
        self.clock.t += R.SESSION_EVERY
        self.assertEqual(post(combos[3])[0].status, 200, "one more after the wait")

    def test_spawning_runs_under_the_collect_lock(self):
        held = []
        self.fake_spawn = lambda *a, **k: held.append(self.app.mux._lock.locked()) or 41
        with unittest.mock.patch.object(R.A, "spawn_agent", side_effect=self.fake_spawn):
            self.call("POST", "/api/sessions", {"profile": None, "dir": "~"})
        self.assertEqual(held, [True])


class TestModelApi(RichCase):
    MODELS = {"v": 1, "thinking_levels": ["off", "low", "high"], "models": [
        {"id": "a/one", "name": "One", "provider": "a", "reasoning": True}, {"id": "b/two", "name": "Two", "provider": "b", "reasoning": False}]}

    def setUp(self):
        super().setUp()
        self.write_state(3, state="idle", model={"id": "a/one", "name": "One", "provider": "a"}, thinking="low")
        with open(os.path.join(self.dir, "3.models.json"), "w") as f:
            json.dump(self.MODELS, f)

    def cmds(self):
        out = []
        for n in sorted(os.listdir(self.dir)):
            if ".cmd." in n:
                with open(os.path.join(self.dir, n)) as f:
                    out.append(json.load(f))
        return out

    def extension_obeys(self, **change):
        """What the extension does a moment after a command file appears: the state file shows the change."""
        def hook(n):
            if self.cmds() and n == 2:
                self.write_state(3, state="idle", **{"model": {"id": "a/one", "name": "One", "provider": "a"}, "thinking": "low", **change})
        self.on_sleep = hook

    def test_list(self):
        r, data = self.call("GET", "/api/agents/3/models")
        self.assertEqual(r.status, 200)
        self.assertEqual(data, {"current": "a/one", "thinking": "low", "levels": ["off", "low", "high"], "models": self.MODELS["models"]})

    def test_list_for_an_older_extension_is_empty(self):
        self.write_state(3, state="idle")
        os.unlink(os.path.join(self.dir, "3.models.json"))
        self.assertEqual(self.call("GET", "/api/agents/3/models")[1], {"current": None, "thinking": None, "levels": [], "models": []})

    def test_the_list_is_sanitized_like_the_agent_list(self):
        self.write_state(3, state="idle", model={"id": "a/o\x1b[0mne", "name": "N\x1b[31m\u202eame", "provider": "a"}, thinking="low")
        with open(os.path.join(self.dir, "3.models.json"), "w") as f:
            json.dump({"v": 1, "thinking_levels": ["low"], "models": [
                {"id": "a/o\x1b[0mne", "name": "bad id", "provider": "a"},
                {"id": "b/two", "name": "T\u202ew\x1b[31mo", "provider": "p\x07q", "reasoning": True}]}, f)
        _, data = self.call("GET", "/api/agents/3/models")
        self.assertEqual(data["current"], "a/one")
        self.assertEqual(data["models"], [{"id": "b/two", "name": "Two", "provider": "pq", "reasoning": True}],
                         "an id that needs cleaning is not offered: it could not be switched to")

    def test_switch_model_writes_a_command_and_confirms(self):
        self.extension_obeys(model={"id": "b/two", "name": "Two", "provider": "b"})
        r, data = self.call("POST", "/api/agents/3/model", {"model": "b/two"})
        self.assertEqual((r.status, data), (200, {"ok": True, "confirmed": True}))
        self.assertEqual(self.cmds(), [{"type": "set_model", "model": "b/two"}])
        self.assertEqual(self.logs[-1], "write model label=1 bytes=5")

    def test_not_confirmed_after_three_seconds(self):
        r, data = self.call("POST", "/api/agents/3/model", {"model": "b/two"})
        self.assertEqual((r.status, data), (200, {"ok": True, "confirmed": False}))
        self.assertAlmostEqual(sum(self.slept), R.SWITCH_WAIT, delta=0.2)

    def test_switch_thinking(self):
        self.extension_obeys(thinking="high")
        r, data = self.call("POST", "/api/agents/3/thinking", {"level": "high"})
        self.assertEqual((r.status, data), (200, {"ok": True, "confirmed": True}))
        self.assertEqual(self.cmds(), [{"type": "set_thinking", "level": "high"}])

    def test_the_current_value_needs_no_command(self):
        self.assertEqual(self.call("POST", "/api/agents/3/model", {"model": "a/one"})[1], {"ok": True, "confirmed": True})
        self.assertEqual(self.call("POST", "/api/agents/3/thinking", {"level": "low"})[1], {"ok": True, "confirmed": True})
        self.assertEqual(self.cmds(), [])

    def test_unknown_values_are_refused(self):
        for path, body in (("model", {"model": "c/three"}), ("model", {"model": "../x"}), ("model", {"model": ""}), ("model", {"model": 5}),
                           ("model", {}), ("model", {"model": "b/two", "x": 1}), ("thinking", {"level": "turbo"}), ("thinking", {"level": 3}),
                           ("thinking", {"level": "max"}), ("thinking", {})):
            r, data = self.call("POST", f"/api/agents/3/{path}", body)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), (path, body))
        self.assertEqual(self.cmds(), [])

    def test_busy_agents_must_wait(self):
        for state in ("working", "busy", "attention", "exited"):
            self.write_state(3, state=state, model={"id": "a/one", "name": "One", "provider": "a"}, **({"pid": DEAD_PID} if state == "exited" else {}))
            self.clock.t += 5
            for path, body in (("model", {"model": "b/two"}), ("thinking", {"level": "high"})):
                r, data = self.call("POST", f"/api/agents/3/{path}", body)
                self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"), (state, path))
        self.assertEqual(self.cmds(), [])
        self.write_state(3, state="working")
        self.clock.t += 5
        self.assertIn("idle", self.call("POST", "/api/agents/3/model", {"model": "b/two"})[1]["message"])

    def test_an_agent_with_an_old_extension_cannot_switch(self):
        self.write_state(3, state="idle")
        self.clock.t += 5
        r, data = self.call("POST", "/api/agents/3/model", {"model": "a/one"})
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertEqual(self.cmds(), [])

    def test_read_only(self):
        app = self.make_app(readonly=True)
        r, data = self.call("POST", "/api/agents/3/model", {"model": "b/two"}, app=app)
        self.assertEqual((r.status, data["error"]), (403, "readonly"))
        self.assertFalse(self.call("GET", "/api/agents", app=app)[1]["agents"][0]["can"]["model"])
        self.assertEqual(self.cmds(), [])

    def test_unknown_agent_and_methods(self):
        self.assertEqual(self.call("POST", "/api/agents/9/model", {"model": "a/one"})[0].status, 404)
        self.assertEqual(self.call("GET", "/api/agents/3/model")[0].status, 405)
        self.assertEqual(self.call("POST", "/api/agents/3/models", {})[0].status, 405)

    def test_the_list_offers_the_switch(self):
        _, data = self.call("GET", "/api/agents")
        row = data["agents"][0]
        self.assertEqual((row["model"]["id"], row["thinking"], row["can"]["model"]), ("a/one", "low", True))


class TestAssets(RichCase):
    def test_logo_and_icon_come_from_the_ui_constants(self):
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
        with unittest.mock.patch.object(R.UI, "LOGO_SVG", "<svg xmlns='http://www.w3.org/2000/svg'/>", create=True), \
                unittest.mock.patch.object(R.UI, "ICON_PNG_B64", base64.b64encode(png).decode(), create=True):
            r, _ = self.call("GET", "/logo.svg", token=None)
            self.assertEqual((r.status, r.content_type, r.body), (200, "image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>"))
            self.assertEqual(r.headers["Cache-Control"], "no-cache")
            self.assertEqual(self.call("GET", "/logo.svg", token=None, headers={"if-none-match": r.headers["ETag"]})[0].status, 304)
            r, _ = self.call("GET", "/icon.png", token=None)
            self.assertEqual((r.status, r.content_type, r.body), (200, "image/png", png))
            self.assertEqual(self.call("GET", "/icon.png", token=None, headers={"if-none-match": r.headers["ETag"]})[0].status, 304)
            self.assertEqual(self.call("POST", "/icon.png", {}, token=None)[0].status, 405)

    def test_missing_or_broken_constants_are_404_not_500(self):
        with unittest.mock.patch.object(R.UI, "LOGO_SVG", None, create=True), unittest.mock.patch.object(R.UI, "ICON_PNG_B64", "!!!not base64", create=True):
            self.assertEqual(self.call("GET", "/logo.svg", token=None)[0].status, 404)
            self.assertEqual(self.call("GET", "/icon.png", token=None)[0].status, 404)
        with unittest.mock.patch.object(R.UI, "ICON_PNG_B64", base64.b64encode(b"GIF89a").decode(), create=True):
            self.assertEqual(self.call("GET", "/icon.png", token=None)[0].status, 404, "only a PNG is served")

    def test_fonts_from_the_server_machine_without_a_token(self):
        self.assertEqual(self.call("GET", "/fonts/mono.ttf", token=None)[0].status, 404, "no font installed: the CSS falls back")
        regular, bold = self.fonts["/fonts/mono.ttf"][0], self.fonts["/fonts/mono-bold.ttf"][0]
        for path, body in ((regular, b"REGULAR"), (bold, b"BOLD")):
            with open(path, "wb") as f:
                f.write(body)
        r, _ = self.call("GET", "/fonts/mono.ttf", token=None)
        self.assertEqual((r.status, r.content_type, r.body), (200, "font/ttf", b"REGULAR"))
        self.assertEqual(r.headers["Cache-Control"], "public, max-age=86400")
        self.assertEqual(self.call("GET", "/fonts/mono-bold.ttf", token=None)[0].body, b"BOLD")
        with open(regular, "wb") as f:
            f.write(b"CHANGED!")
        self.assertEqual(self.call("GET", "/fonts/mono.ttf", token=None)[0].body, b"CHANGED!", "a replaced font is read again")

    def test_fonts_keep_the_host_check_and_the_method_rule(self):
        with open(self.fonts["/fonts/mono.ttf"][0], "wb") as f:
            f.write(b"F")
        r, data = self.call("GET", "/fonts/mono.ttf", token=None, host="evil.example")
        self.assertEqual((r.status, data["error"]), (403, "forbidden_host"))
        self.assertEqual(self.call("POST", "/fonts/mono.ttf", {}, token=None)[0].status, 405)

    def test_only_the_two_fixed_font_routes_exist(self):
        for path in ("/fonts/", "/fonts/other.ttf", "/fonts/../etc/passwd", "/fonts/mono.ttf/x"):
            self.assertEqual(self.call("GET", path, token=None)[0].status, 404, path)

    def test_the_first_existing_candidate_wins_and_oversized_or_odd_files_are_skipped(self):
        first, second = os.path.join(self.tmp.name, "f1"), os.path.join(self.tmp.name, "f2")
        app = self.make_app(fonts={"/fonts/mono.ttf": (first, second)})
        os.mkdir(first)                                     # a directory is not a font
        with open(second, "wb") as f:
            f.write(b"SECOND")
        self.assertEqual(self.call("GET", "/fonts/mono.ttf", token=None, app=app)[1:], (None,))
        with unittest.mock.patch.object(R, "MAX_FONT", 3):
            self.assertEqual(self.call("GET", "/fonts/mono.ttf", token=None, app=app)[0].status, 404)

    def test_default_candidates_are_the_terminals_font_files(self):
        self.assertEqual(R.App.FONTS["/fonts/mono.ttf"][0], "~/.local/share/fonts/DejaVuSansMNerdFont/DejaVuSansMNerdFontMono-Regular.ttf")
        self.assertEqual(R.App.FONTS["/fonts/mono-bold.ttf"][1], "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf")

    def test_csp_allows_fonts_and_images_from_self_only(self):
        csp = R.SECURITY_HEADERS["Content-Security-Policy"]
        self.assertIn("font-src 'self'", csp)
        self.assertIn("img-src 'self' data:", csp)
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("unsafe", csp)


class TestHardening(RichCase):
    """Findings of the security review of the remote backend that need the whole App."""

    def test_before_must_be_a_positive_item_number(self):
        path = self.make_session(jl(msg("user", text("hello"))))
        self.write_state(3, sessionFile=path)
        for bad in ("0", "00"):
            r, data = self.call("GET", f"/api/agents/3/chat?before={bad}")
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), bad)
        self.assertEqual(self.call("GET", "/api/agents/3/chat?before=1")[0].status, 200)

    def test_accounts_and_session_options_need_no_window(self):
        TR.Path(self.settings).write_text(json.dumps({"accounts": [
            {"label": "alpha", "profile": "alpha", "dir": "~/.claude-alpha", "auto": False, "hidden": False}]}))
        app = R.App(R.Mux(locate=lambda: None, fg=lambda: {}, clock=self.clock), R.TokenStore(self.tokfile), clock=self.clock,
                    wall=lambda: NOW, log=self.logs.append, theme_watch=TR.ThemeWatch(self.settings), home=self.home,
                    sysstats=self.sysstats, fonts=self.fonts, sleep=self.sleep)
        r, data = self.call("GET", "/api/accounts", app=app)
        self.assertEqual((r.status, [a["label"] for a in data["accounts"]], data["totals"]["agents"]), (200, ["alpha"], 0))
        r, data = self.call("GET", "/api/sessions/options", app=app)
        self.assertEqual((r.status, [p["label"] for p in data["profiles"]], data["dirs"][0]["path"]), (200, ["alpha"], "~"))
        r, data = self.call("POST", "/api/sessions", {"profile": "alpha", "dir": "~"}, app=app)
        self.assertEqual((r.status, data["error"]), (503, "no_mux"), "starting one still needs the window")
        self.assertEqual(self.call("GET", "/api/agents", app=app)[0].status, 503)

    def test_a_held_request_stops_when_its_client_is_gone(self):
        r, _ = self.call("GET", "/api/agents")
        headers = {"host": "127.0.0.1:8787", "authorization": "Bearer " + TR.TOKEN, "if-none-match": r.headers["ETag"]}
        req = R.Request("GET", "/api/agents?wait=20", headers, "127.0.0.1", alive=lambda: len(self.slept) < 3)
        with self.assertRaises(R.ClientGone):
            self.app.dispatch(req)
        self.assertEqual(self.slept, [1.0, 1.0, 1.0], "it noticed at the third look, not after 20 seconds")

    def test_holders_of_one_pane_share_its_screen_reads(self):
        self.fake.replies["get-text"] = "one\n"
        gets = lambda: len([c for c in self.fake.calls if c[0] == "get-text"])
        r, _ = self.call("GET", "/api/agents/3/tail")
        self.assertEqual(gets(), 1)
        with unittest.mock.patch.object(R, "LIST_POLL", 0.5):
            r2, _ = self.call("GET", "/api/agents/3/tail?wait=2", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual(r2.status, 304)
        self.assertEqual(len(self.slept), 4, "four looks in two seconds")
        self.assertEqual(gets(), 1 + 1 + 2, "a first look reads, then one read per second serves the looks in between")

    def test_the_screen_cache_serves_equal_or_smaller_requests_only(self):
        self.fake.replies["get-text"] = "x\n"
        gets = lambda: len([c for c in self.fake.calls if c[0] == "get-text"])
        self.app._tail_text(3, 60, 1.0)
        self.app._tail_text(3, 20, 1.0)
        self.assertEqual(gets(), 1)
        self.app._tail_text(3, 120, 1.0)
        self.assertEqual(gets(), 2, "a larger request needs a larger read")
        self.clock.t += 1.0
        self.app._tail_text(3, 120, 1.0)
        self.assertEqual(gets(), 3, "and nothing is older than a second")
        self.app._tail_text(3, 120, 0.0)
        self.assertEqual(gets(), 4, "a first look always reads")


if __name__ == "__main__":
    unittest.main()
