"""hivegrid_hostpage: the remote hosts file, the token of a host, and the status file and one-shot
page that open another machine's hivegrid-remote in a web pane of the window."""
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True
sys.path.insert(0, BIN)
import hivegrid_hostpage as H  # noqa: E402

TOKEN = "k3J9-x_Q7mZ2vB8nL4tYw6RcD1fHs0Ae"
URL = "https://box-1.example-tail.ts.net"


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(self.run_dir, mode=0o700)
        self.cfg = os.path.join(self.tmp, "cfg")
        os.makedirs(self.cfg)
        self.env = {"HOME": self.tmp, "XDG_RUNTIME_DIR": self.run_dir,
                    "HIVEGRID_SETTINGS": os.path.join(self.cfg, "settings.json")}

    def settings(self, hosts):
        with open(H.hosts_path(self.env), "w") as f:
            json.dump({"hosts": hosts}, f)

    def up(self, answer=True):
        self.probed = []
        return lambda url, timeout=4.0: self.probed.append(url) or answer


class Hosts(Case):
    def test_valid_entries_in_file_order(self):
        self.settings([{"name": "box-1", "url": URL}, {"name": "box-2", "url": "http://100.66.1.2:8787"}])
        self.assertEqual(H.hosts(self.env), [{"name": "box-1", "url": URL + "/"},
                                             {"name": "box-2", "url": "http://100.66.1.2:8787/"}])

    def test_only_addresses_inside_the_tailnet(self):
        for bad in ("https://example.com", "http://box.ts.net", "https://192.168.1.2", "https://100.1.2.3",
                    "https://user:pw@box.ts.net", "https://box.ts.net/x", "https://box.ts.net/?a=1",
                    "https://box.ts.net/#t=1", "ftp://box.ts.net", "https://evil.ts.net.example.com", "",
                    "https://box.ts.net\n", None, 5):
            self.assertIsNone(H.valid_url(bad), bad)
        for good in ("https://a.b.ts.net", "https://a.b.ts.net:8443/", "http://100.64.0.1", "https://[fd7a:115c:a1e0::1]"):
            self.assertTrue(H.valid_url(good), good)

    def test_bad_names_duplicates_and_the_cap_are_dropped(self):
        self.settings([{"name": "Box", "url": URL}, {"name": "a b", "url": URL}, {"name": "", "url": URL},
                       {"name": "ok", "url": URL}, {"name": "ok", "url": URL}, "x", {"name": "no-url"}]
                      + [{"name": f"h{i}", "url": URL} for i in range(20)])
        names = [h["name"] for h in H.hosts(self.env)]
        self.assertEqual(names[0], "ok")
        self.assertEqual(len(names), H.MAX_HOSTS)
        self.assertEqual(len(set(names)), len(names))

    def test_no_file_or_a_broken_one_means_no_hosts(self):
        self.assertEqual(H.hosts(self.env), [])
        with open(H.hosts_path(self.env), "w") as f:
            f.write("{nope")
        self.assertEqual(H.hosts(self.env), [])
        self.settings("not a list")
        self.assertEqual(H.hosts(self.env), [])

    def test_the_list_is_a_file_of_its_own_next_to_settings(self):
        self.assertEqual(H.hosts_path(self.env), os.path.join(self.cfg, "remote-hosts.json"))


class Token(Case):
    def test_stored_private_and_read_back(self):
        self.assertTrue(H.store_token("box-1", f" {TOKEN}\n", self.env))
        path = H.token_path("box-1", self.env)
        self.assertEqual(path, os.path.join(self.cfg, "hosts", "box-1", "remote-token"))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        self.assertEqual(H.read_token("box-1", self.env), TOKEN)

    def test_a_wrong_shape_or_name_is_refused_and_nothing_is_written(self):
        for name, tok in (("box-1", "short"), ("box-1", "has space in it 1234567890"), ("../x", TOKEN), ("Box", TOKEN)):
            self.assertFalse(H.store_token(name, tok, self.env), (name, tok))
        self.assertFalse(os.path.exists(os.path.join(self.cfg, "hosts")))
        self.assertIsNone(H.read_token("box-1", self.env))

    def test_the_file_is_named_so_the_backup_never_takes_it(self):
        # ai-setup's FORBIDDEN_NAME matches the basename `remote-token`
        self.assertEqual(os.path.basename(H.token_path("box-1", self.env)), "remote-token")


class Ensure(Case):
    def setUp(self):
        super().setUp()
        self.settings([{"name": "box-1", "url": URL}])

    def status(self):
        with open(H.status_path(self.env)) as f:
            return json.load(f)

    def test_an_answering_host_with_a_token_gives_the_page(self):
        H.store_token("box-1", TOKEN, self.env)
        doc = H.ensure("box-1", self.env, probe=self.up(), wall=lambda: 5.0)
        self.assertTrue(doc["ok"])
        self.assertEqual((doc["state"], doc["ts"], doc["url"]), ("up", 5.0, URL + "/"))
        self.assertEqual(doc["open"], os.path.join(self.run_dir, H.OPEN_NAME))
        self.assertEqual(stat.S_IMODE(os.stat(doc["open"]).st_mode), 0o600)
        with open(doc["open"]) as f:
            self.assertIn(json.dumps(f"{URL}/#t={TOKEN}&r=%2F"), f.read())

    def test_no_token_says_the_command_and_probes_nothing(self):
        probe = self.up()
        doc = H.ensure("box-1", self.env, probe=probe)
        self.assertEqual((doc["ok"], doc["state"], doc["command"]), (False, "no_token", "hivegrid-host-page token box-1"))
        self.assertEqual(self.probed, [])
        self.assertIsNone(doc["open"])

    def test_a_host_that_does_not_answer_names_tailscale_and_dns(self):
        H.store_token("box-1", TOKEN, self.env)
        doc = H.ensure("box-1", self.env, probe=self.up(False))
        self.assertEqual((doc["ok"], doc["state"], doc["open"]), (False, "down", None))
        self.assertIn("Tailscale", doc["message"])
        self.assertFalse(os.path.exists(H.open_path(self.env)))

    def test_an_unknown_host(self):
        doc = H.ensure("other", self.env, probe=self.up())
        self.assertEqual((doc["ok"], doc["state"]), (False, "unknown_host"))
        self.assertEqual(self.probed, [])

    def test_the_status_file_carries_no_token_and_is_private(self):
        H.store_token("box-1", TOKEN, self.env)
        H.ensure("box-1", self.env, probe=self.up())
        path = H.status_path(self.env)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as f:
            self.assertNotIn(TOKEN, f.read())

    def test_the_token_never_goes_outside_the_runtime_directory(self):
        H.store_token("box-1", TOKEN, self.env)
        for env in ({k: v for k, v in self.env.items() if k != "XDG_RUNTIME_DIR"}, dict(self.env, XDG_RUNTIME_DIR="relative")):
            doc = H.ensure("box-1", env, probe=self.up())
            self.assertFalse(doc["ok"])
            self.assertIsNone(doc["open"])

    def test_an_unwritable_status_directory_does_not_raise(self):
        H.store_token("box-1", TOKEN, self.env)
        env = dict(self.env, XDG_RUNTIME_DIR=os.path.join(self.tmp, "missing"))
        self.assertFalse(H.ensure("box-1", env, probe=self.up())["ok"])


if __name__ == "__main__":
    unittest.main()
