"""omp-switch: `locate` against a real child process on a pty, `transfer` between two temp profiles.
Runs the CLI with a temp HOME; nothing outside the temp dir is read or written."""
import hashlib
import json
import os
import pty
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
SWITCH = os.path.join(BIN, "omp-switch")

# Stands in for omp: opens the given files (as omp holds its session file open) and waits.
CHILD = "import sys, time\nfs = [open(p, 'a') for p in sys.argv[1:]]\nprint('ready', flush=True)\ntime.sleep(30)\n"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(os.path.realpath(tmp.name))
        self.src = self.agent("work")
        self.dst = self.agent("team")

    def agent(self, profile):
        a = self.home / ".omp" / "profiles" / profile / "agent"
        (a / "sessions").mkdir(parents=True)
        return a

    def run_cli(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("WEZTERM_")}
        env.update(HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, SWITCH, *args], capture_output=True, text=True, env=env, timeout=10)
        out = json.loads(r.stdout)
        self.assertEqual(r.returncode, 1 if out["state"] == "error" else 0, r.stdout + r.stderr)
        return out

    def session(self, agent, name="--proj--/2026_chat.jsonl", body=b'{"title":"t"}\n{"m":1}\n'):
        p = agent / "sessions" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        return p


class Locate(Base):
    def setUp(self):
        super().setUp()
        self.cwd = self.home / "work"
        self.cwd.mkdir()

    def spawn(self, *held, cwd=None):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        proc = subprocess.Popen([sys.executable, "-c", CHILD, *map(str, held)], stdin=slave,
                                stdout=subprocess.PIPE, cwd=cwd or self.cwd, text=True)
        tty = os.ttyname(slave)
        os.close(slave)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        self.pts = tty.rsplit("/", 1)[1]
        return proc

    def record(self, session, cwd=None, fresh=False):
        rec = self.src / "terminal-sessions" / f"pts-{self.pts}"
        rec.parent.mkdir(exist_ok=True)
        rec.write_text("\n".join([str(cwd or self.cwd), str(session)] + (["fresh"] if fresh else [])) + "\n")

    def locate(self, proc):
        return self.run_cli("locate", "--pid", str(proc.pid), "--from", "work")

    def test_terminal_record_finds_the_session(self):
        s = self.session(self.src)
        proc = self.spawn()
        self.record(s)
        self.assertEqual(self.locate(proc), {"state": "ok", "session": str(s), "cwd": str(self.cwd)})

    def test_fresh_record_without_file_means_no_session_yet(self):
        proc = self.spawn()
        self.record(self.src / "sessions" / "--proj--" / "new.jsonl", fresh=True)
        self.assertEqual(self.locate(proc), {"state": "none"})

    def test_missing_recorded_file_without_fresh_is_an_error(self):
        proc = self.spawn()
        self.record(self.src / "sessions" / "--proj--" / "gone.jsonl")
        self.assertEqual(self.locate(proc)["state"], "error")

    def test_no_record_and_nothing_held_is_an_error(self):
        self.session(self.src)
        proc = self.spawn()
        self.assertEqual(self.locate(proc)["state"], "error")

    def test_record_of_another_directory_is_not_trusted(self):
        s = self.session(self.src)
        proc = self.spawn()
        other = self.home / "other"
        other.mkdir()
        self.record(s, cwd=other)
        self.assertEqual(self.locate(proc)["state"], "error")

    def test_record_pointing_outside_the_profile_is_refused(self):
        outside = self.session(self.dst)  # another profile's session
        proc = self.spawn()
        self.record(outside)
        self.assertEqual(self.locate(proc)["state"], "error")
        sneaky = self.src / "sessions" / ".." / ".." / ".." / "team" / "agent" / "sessions" / "--proj--" / "2026_chat.jsonl"
        self.record(sneaky)
        self.assertEqual(self.locate(proc)["state"], "error")

    def test_held_session_file_wins_without_record(self):
        s = self.session(self.src)
        proc = self.spawn(s)
        self.assertEqual(self.locate(proc), {"state": "ok", "session": str(s), "cwd": str(self.cwd)})

    def test_files_held_outside_the_profile_sessions_are_ignored(self):
        s = self.session(self.src)
        foreign = self.session(self.dst, "--x--/other.jsonl")
        proc = self.spawn(foreign)
        self.record(s)
        self.assertEqual(self.locate(proc)["session"], str(s))

    def test_several_open_sessions_need_the_record_to_decide(self):
        a = self.session(self.src, "--proj--/a.jsonl")
        b = self.session(self.src, "--proj--/b.jsonl")
        proc = self.spawn(a, b)
        self.assertEqual(self.locate(proc)["state"], "error")  # no record: refuse rather than guess
        c = self.session(self.src, "--proj--/c.jsonl")
        self.record(c)
        self.assertEqual(self.locate(proc)["state"], "error")  # record names neither open file
        self.record(b)
        self.assertEqual(self.locate(proc)["session"], str(b))

    def test_unknown_profile_and_dead_process_are_errors(self):
        proc = self.spawn()
        self.assertEqual(self.run_cli("locate", "--pid", str(proc.pid), "--from", "nope")["state"], "error")
        self.assertEqual(self.run_cli("locate", "--pid", str(proc.pid), "--from", "../work")["state"], "error")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.assertEqual(self.run_cli("locate", "--pid", str(dead.pid), "--from", "work")["state"], "error")


def snapshot(root):
    """{relative path: (sha256, mtime_ns)} of every file below root."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            out[str(p.relative_to(root))] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    return out


H1, H2, H3 = ("a" * 64), ("b" * 64), ("c" * 64)


class Transfer(Base):
    def setUp(self):
        super().setUp()
        body = f'{{"title":"chat"}}\n{{"img":"blob:sha256:{H1}"}}\n{{"img":"blob:sha256:{H2}"}}\n'.encode()
        self.s = self.session(self.src, body=body)
        art = self.s.with_suffix("")
        (art / "sub").mkdir(parents=True)
        (art / "out.txt").write_text("artifact")
        (art / "sub" / "deep.txt").write_text("deep")
        blobs = self.src / "blobs"
        blobs.mkdir()
        (blobs / H1).write_bytes(b"blob1")
        (blobs / f"{H2}.png").write_bytes(b"blob2")
        (blobs / H3).write_bytes(b"unreferenced")
        self.target = self.dst / "sessions" / "--proj--" / "2026_chat.jsonl"
        self.before = snapshot(self.home / ".omp" / "profiles" / "work")

    def tearDown(self):
        self.assertEqual(snapshot(self.home / ".omp" / "profiles" / "work"), self.before,
                         "the source profile was modified")

    def transfer(self, session=None):
        return self.run_cli("transfer", "--session", str(session or self.s), "--from", "work", "--to", "team")

    def test_copies_session_artifacts_and_referenced_blobs_under_the_same_names(self):
        self.assertEqual(self.transfer(), {"state": "ok", "session": str(self.target)})
        self.assertEqual(self.target.read_bytes(), self.s.read_bytes())
        art = self.target.with_suffix("")
        self.assertEqual((art / "out.txt").read_text(), "artifact")
        self.assertEqual((art / "sub" / "deep.txt").read_text(), "deep")
        self.assertEqual(sorted(p.name for p in (self.dst / "blobs").iterdir()), [H1, f"{H2}.png"])
        self.assertEqual((self.dst / "blobs" / H1).read_bytes(), b"blob1")
        leftovers = [p for p in self.dst.rglob(".omp-switch*")]
        self.assertEqual(leftovers, [])

    def test_existing_files_are_never_overwritten(self):
        art = self.target.with_suffix("")
        art.mkdir(parents=True)
        (art / "out.txt").write_text("theirs")
        (self.dst / "blobs").mkdir()
        (self.dst / "blobs" / H1).write_bytes(b"their blob")
        self.assertEqual(self.transfer()["state"], "ok")
        self.assertEqual((art / "out.txt").read_text(), "theirs")
        self.assertEqual((art / "sub" / "deep.txt").read_text(), "deep")  # missing ones are added
        self.assertEqual((self.dst / "blobs" / H1).read_bytes(), b"their blob")

    def test_repeated_transfer_is_idempotent(self):
        self.transfer()
        first = snapshot(self.dst)
        self.assertEqual(self.transfer()["state"], "ok")
        self.assertEqual(snapshot(self.dst), first)

    def test_older_prefix_copy_is_brought_up_to_date(self):
        self.target.parent.mkdir(parents=True)
        lines = self.s.read_bytes().splitlines(keepends=True)
        self.target.write_bytes(b'{"title":"older title"}\n' + lines[1])
        self.assertEqual(self.transfer()["state"], "ok")
        self.assertEqual(self.target.read_bytes(), self.s.read_bytes())

    def test_target_ahead_of_source_is_kept(self):
        self.target.parent.mkdir(parents=True)
        ahead = self.s.read_bytes() + b'{"m":"later"}\n'
        self.target.write_bytes(ahead)
        self.assertEqual(self.transfer()["state"], "ok")
        self.assertEqual(self.target.read_bytes(), ahead)

    def test_different_chat_under_the_same_name_aborts_untouched(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'{"title":"chat"}\n{"m":"something else"}\n')
        before = snapshot(self.dst)
        out = self.transfer()
        self.assertEqual(out["state"], "error")
        self.assertEqual(snapshot(self.dst), before)

    def test_bad_session_arguments_are_refused(self):
        foreign = self.session(self.dst, "--x--/other.jsonl")
        self.assertEqual(self.transfer(foreign)["state"], "error")  # not in the source profile
        self.assertEqual(self.transfer(self.src / "sessions" / "--proj--" / "missing.jsonl")["state"], "error")
        notes = self.src / "sessions" / "--proj--" / "notes.txt"
        notes.write_text("x")
        self.before = snapshot(self.home / ".omp" / "profiles" / "work")
        self.assertEqual(self.transfer(notes)["state"], "error")
        sneaky = self.src / "sessions" / ".." / ".." / ".." / "team" / "agent" / "sessions" / "--x--" / "other.jsonl"
        self.assertEqual(self.transfer(sneaky)["state"], "error")
        out = self.run_cli("transfer", "--session", str(self.s), "--from", "work", "--to", "../evil")
        self.assertEqual(out["state"], "error")
        self.assertFalse((self.home / ".omp" / "profiles" / "evil").exists())
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
