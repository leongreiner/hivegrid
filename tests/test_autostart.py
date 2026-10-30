"""bin/hivegrid-autostart: which files and commands install, uninstall and status produce, whether a second
install is a no-op, and that --dry-run and status change nothing. A temp HOME and a temp stand-in for /
(`root`) hold everything; a fake runner records the commands and emulates the few that change files
(`install`, `rm`, `loginctl enable-linger`, `systemctl --user enable`)."""
import contextlib
import io
import os
import shutil
import pwd
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
sys.dont_write_bytecode = True

import hivegrid_autostart as A  # noqa: E402

GDM_CONF = """# GDM configuration storage

[daemon]
# Enabling automatic login
#  AutomaticLoginEnable = true
#  AutomaticLogin = user1

[security]
"""


class FakeRunner:
    def __init__(self, root, user):
        self.root = root
        self.user = user
        self.calls = []  # (argv, sudo) of every command that changes something
        self.enabled = set()
        self.sys_enabled = set()
        self.active = set()
        self.stdin = []  # (argv, input) of every command that got text on stdin

    def argv(self, argv, sudo):
        return (["sudo"] + list(argv)) if sudo else list(argv)

    def query(self, argv, timeout=10):
        argv = list(argv)
        if argv[:3] == ["systemctl", "--user", "is-enabled"]:
            return (0, "enabled\n") if argv[3] in self.enabled else (1, "disabled\n")
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return (0, "active\n") if argv[3] in self.active else (3, "inactive\n")
        if argv[:2] == ["systemctl", "is-enabled"]:
            return (0, "enabled\n") if argv[2] in self.sys_enabled else (1, "disabled\n")
        return 127, ""

    def run(self, argv, sudo=False, input=None):
        argv = list(argv)
        self.calls.append((argv, sudo))
        if input is not None:
            self.stdin.append((argv, input))
        if argv[0] == "install":
            os.makedirs(os.path.dirname(argv[-1]), exist_ok=True)
            if argv[-2] == "/dev/stdin":
                with open(argv[-1], "w") as fh:
                    fh.write(input)
            else:
                shutil.copyfile(argv[-2], argv[-1])
        elif argv[:2] == ["systemctl", "enable"]:
            self.sys_enabled.update(argv[2:])
        elif argv[0] == "rm":
            os.unlink(argv[-1])
        elif argv[:2] == ["loginctl", "enable-linger"]:
            d = os.path.join(self.root, "var/lib/systemd/linger")
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, argv[2]), "w").close()
        elif argv[:2] == ["loginctl", "disable-linger"]:
            os.unlink(os.path.join(self.root, "var/lib/systemd/linger", argv[2]))
        elif argv[:3] == ["systemctl", "--user", "enable"]:
            names = [a for a in argv[3:] if not a.startswith("--")]
            self.enabled.update(names)
            if "--now" in argv:
                self.active.update(names)
        elif argv[:3] == ["systemctl", "--user", "disable"]:
            self.enabled.discard(argv[3])
        return 0


def slurp(path, mode="r"):
    with open(path, mode) as fh:
        return fh.read()


def tree(path):
    """Every file and directory below path with its content: what a run must not change."""
    found = {}
    for base, dirs, files in os.walk(path):
        for d in dirs:
            found[os.path.join(base, d)] = None
        for f in files:
            full = os.path.join(base, f)
            found[full] = os.readlink(full) if os.path.islink(full) else slurp(full, "rb")
    return found


class AutostartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hg-autostart-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        self.root = os.path.join(self.tmp, "root")
        self.bin = os.path.join(self.home, ".local", "bin")
        os.makedirs(self.bin)
        for name in ("hivegrid-remote", "hivegrid"):
            with open(os.path.join(self.bin, name), "w") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(os.path.join(self.bin, name), 0o755)
        # a token, a GDM system, a kernel with the default values, a machine without LUKS
        self.token = os.path.join(self.home, ".config", "hivegrid", "remote-token")
        os.makedirs(os.path.dirname(self.token))
        with open(self.token, "w") as fh:
            fh.write("x\n")
        os.chmod(self.token, 0o600)
        self.write_root("etc/gdm3/custom.conf", GDM_CONF)
        os.makedirs(os.path.join(self.root, "etc/systemd/system"))
        os.symlink("/lib/systemd/system/gdm3.service", os.path.join(self.root, "etc/systemd/system/display-manager.service"))
        for key, _, _ in A.SYSCTL:
            self.write_root("proc/sys/" + key.replace(".", "/"), "0\n")
        self.write_root("proc/sys/kernel/nmi_watchdog", "1\n")
        self.env = {"HOME": self.home, "USER": "tester", "PATH": ""}
        self.runner = FakeRunner(self.root, "tester")

    def write_root(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def root_path(self, rel):
        return os.path.join(self.root, rel.lstrip("/"))

    def unit(self, name):
        return os.path.join(self.home, ".config", "systemd", "user", name)

    def cli(self, *argv, euid=1000):
        out = io.StringIO()
        rc = A.main(list(argv), env=self.env, out=out, runner=self.runner, euid=euid, root=self.root, user="tester")
        return rc, out.getvalue()

    # ---- rendering

    def test_unit_exec_paths(self):
        self.assertEqual(A._unit_exec("/home/u/.local/bin/x", "/home/u"), "%h/.local/bin/x")
        self.assertEqual(A._unit_exec("/opt/hg/x", "/home/u"), "/opt/hg/x")
        self.assertEqual(A._unit_exec("/opt/a b/x", "/home/u"), '"/opt/a b/x"')
        self.assertEqual(A._unit_exec("/opt/100%/x", "/home/u"), "/opt/100%%/x")
        self.assertEqual(A._unit_exec("/home/ux/bin", "/home/u"), "/home/ux/bin")  # a sibling, not the home

    def test_sysctl_file_active_and_commented_keys(self):
        text = A.render_sysctl()
        active = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(active, [f"-{k} = {v}" for k, v, _ in A.SYSCTL])
        for key, value, _ in A.SYSCTL_OFF:
            self.assertIn(f"#-{key} = {value}", text)
            self.assertNotIn(f"\n-{key} =", text)
        for ln in active:
            self.assertRegex(ln, r"^-[a-z_]+(\.[a-z_]+)+ = \d+$")
        # every setting is explained by the comment right above it
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith(("-kernel.", "#-kernel.")):
                self.assertTrue(lines[i - 1].startswith("# "), ln)

    # ---- GDM custom.conf

    def test_gdm_block_round_trip(self):
        new = A.gdm_set_autologin(GDM_CONF, "tester")
        self.assertIn("[daemon]\n" + A.GDM_BEGIN + "\nAutomaticLoginEnable=true\nAutomaticLogin=tester\n" + A.GDM_END + "\n", new)
        self.assertEqual(A.gdm_autologin(new), ("true", "tester"))
        self.assertEqual(A.gdm_set_autologin(new, "tester"), new)  # idempotent
        self.assertEqual(A.gdm_clear_autologin(new), GDM_CONF)  # exact revert
        self.assertEqual(A.gdm_autologin(GDM_CONF), (None, None))  # commented examples are not settings

    def test_gdm_without_daemon_section_and_with_no_trailing_newline(self):
        for base in ("", "[security]\n", "[security]"):
            new = A.gdm_set_autologin(base, "tester")
            self.assertEqual(A.gdm_autologin(new), ("true", "tester"))
            self.assertEqual(A.gdm_clear_autologin(new).rstrip("\n"), base.rstrip("\n"))

    def test_gdm_respects_a_login_set_by_hand(self):
        other = GDM_CONF.replace("[daemon]\n", "[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=someone\n")
        self.assertIsNone(A.gdm_set_autologin(other, "tester"))
        same = GDM_CONF.replace("[daemon]\n", "[daemon]\nAutomaticLoginEnable = True\nAutomaticLogin = tester\n")
        self.assertEqual(A.gdm_set_autologin(same, "tester"), same)  # nothing of ours added, nothing to undo

    def test_display_manager_detection(self):
        ctx = A.Ctx(self.env, io.StringIO(), self.runner, root=self.root)
        self.assertEqual(A.display_manager(ctx), ("gdm", "gdm3.service"))
        os.unlink(self.root_path("etc/systemd/system/display-manager.service"))
        self.assertEqual(A.display_manager(ctx), (None, None))
        self.write_root("etc/X11/default-display-manager", "/usr/sbin/lightdm\n")
        self.assertEqual(A.display_manager(ctx), ("lightdm", "lightdm"))
        self.write_root("etc/X11/default-display-manager", "/usr/bin/weird\n")
        self.assertEqual(A.display_manager(ctx), (None, "weird"))

    def test_encrypted_volume_detection(self):
        ctx = A.Ctx(self.env, io.StringIO(), self.runner, root=self.root)
        self.assertEqual(A.encrypted_volumes(ctx), [])
        self.write_root("sys/block/dm-0/dm/uuid", "LVM-abc\n")
        self.write_root("sys/block/dm-1/dm/uuid", "CRYPT-LUKS2-abc-cryptroot\n")
        self.write_root("sys/block/dm-1/dm/name", "cryptroot\n")
        self.assertEqual(A.encrypted_volumes(ctx), ["cryptroot"])

    # ---- status and --dry-run change nothing

    def test_status_is_read_only_and_reports_gaps(self):
        before = tree(self.tmp)
        rc, out = self.cli("status", "--bin-dir", self.bin)
        self.assertEqual(rc, 0)
        self.assertEqual(tree(self.tmp), before)
        self.assertEqual(self.runner.calls, [])
        self.assertRegex(out, r"todo +linger for tester: off")
        self.assertRegex(out, r"todo +kernel\.panic = 0 \(wanted 10\)")
        self.assertRegex(out, r"todo +gdm \(gdm3\.service\): no automatic login")
        self.assertIn("install --autologin", out)
        self.assertIn("no hardware watchdog found", out)

    def test_status_sees_what_install_did(self):
        self.cli("install", "--sysctl", "--autologin", "--bin-dir", self.bin)
        for key, value, _ in A.SYSCTL:  # the kernel applied the file
            self.write_root("proc/sys/" + key.replace(".", "/"), value + "\n")
        rc, out = self.cli("status", "--bin-dir", self.bin)
        self.assertRegex(out, r"ok +gdm \(gdm3\.service\) logs tester in at boot")
        self.assertRegex(out, r"ok +kernel\.panic = 10")
        self.assertRegex(out, r"ok +linger for tester: on")
        self.assertRegex(out, r"ok +unit .*hivegrid\.service: current")

    def test_dry_run_prints_everything_and_changes_nothing(self):
        before = tree(self.tmp)
        rc, out = self.cli("install", "--dry-run", "--sysctl", "--autologin", "--bin-dir", self.bin)
        self.assertEqual(rc, 0)
        self.assertEqual(tree(self.tmp), before)
        self.assertEqual(self.runner.calls, [])
        for path in (self.unit("hivegrid-remote.service"), self.unit("hivegrid.service")):
            self.assertIn(f"would write {path}\n", out)
        for path in ("/etc/sysctl.d/90-hivegrid-recovery.conf", "/etc/gdm3/custom.conf"):
            self.assertIn(f"would write {path} (as root)\n", out)
        self.assertIn("would run: systemctl --user daemon-reload", out)
        self.assertIn("would run: systemctl --user enable --now hivegrid-remote.service", out)
        self.assertIn("would run: systemctl --user enable hivegrid.service", out)
        self.assertIn("would run: loginctl enable-linger tester", out)
        self.assertRegex(out, r"would run: sudo install -D -m 0644 /dev/stdin \S*/etc/sysctl\.d/90-hivegrid-recovery\.conf")
        self.assertRegex(out, r"would run: sudo sysctl -p \S*/etc/sysctl\.d/90-hivegrid-recovery\.conf")
        self.assertIn("AutomaticLogin=tester", out)

    def test_dry_run_uninstall_changes_nothing(self):
        self.cli("install", "--sysctl", "--bin-dir", self.bin)
        self.runner.calls.clear()
        before = tree(self.tmp)
        rc, out = self.cli("uninstall", "--dry-run", "--all", "--bin-dir", self.bin)
        self.assertEqual(rc, 0)
        self.assertEqual(tree(self.tmp), before)
        self.assertEqual(self.runner.calls, [])
        self.assertIn("would remove " + self.unit("hivegrid.service"), out)
        self.assertIn("would run: sudo rm -f", out)

    # ---- install, again, uninstall

    def test_install_writes_units_and_enables_them(self):
        rc, out = self.cli("install", "--bin-dir", self.bin)
        self.assertEqual(rc, 0, out)
        self.assertIn("ExecStart=%h/.local/bin/hivegrid-remote serve", slurp(self.unit("hivegrid-remote.service")))
        self.assertIn("ExecStart=%h/.local/bin/hivegrid start", slurp(self.unit("hivegrid.service")))
        cmds = [c for c, _ in self.runner.calls]
        self.assertIn(["systemctl", "--user", "daemon-reload"], cmds)
        self.assertIn(["systemctl", "--user", "enable", "--now", "hivegrid-remote.service"], cmds)
        self.assertIn(["systemctl", "--user", "enable", "hivegrid.service"], cmds)  # not started: it would open a second window
        self.assertIn(["loginctl", "enable-linger", "tester"], cmds)
        self.assertFalse(any(sudo for _, sudo in self.runner.calls), "the user part never needs sudo")
        self.assertFalse(os.path.exists(self.root_path("etc/sysctl.d")), "no flag, no /etc")

    def test_second_install_is_a_no_op(self):
        self.cli("install", "--sysctl", "--autologin", "--bin-dir", self.bin)
        for key, value, _ in A.SYSCTL:
            self.write_root("proc/sys/" + key.replace(".", "/"), value + "\n")
        self.runner.calls.clear()
        before = tree(self.tmp)
        rc, out = self.cli("install", "--sysctl", "--autologin", "--bin-dir", self.bin)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.runner.calls, [], out)
        self.assertEqual(tree(self.tmp), before)
        self.assertNotIn("would write", out)
        self.assertNotIn("would run", out)
        self.assertIn("unchanged", out)

    def test_no_token_enables_but_does_not_start_the_remote_unit(self):
        os.unlink(self.token)
        rc, out = self.cli("install", "--bin-dir", self.bin)
        cmds = [c for c, _ in self.runner.calls]
        self.assertIn(["systemctl", "--user", "enable", "hivegrid-remote.service"], cmds)
        self.assertNotIn("--now", [a for c in cmds for a in c])
        self.assertIn("hivegrid-remote token", out)

    def test_unit_of_your_own_is_kept_and_restored(self):
        os.makedirs(os.path.dirname(self.unit("x")))
        mine = "[Service]\nExecStart=/somewhere/else serve\n"
        with open(self.unit("hivegrid-remote.service"), "w") as fh:
            fh.write(mine)
        self.cli("install", "--bin-dir", self.bin)
        self.assertEqual(slurp(self.unit("hivegrid-remote.service.orig")), mine)
        self.assertIn(A.MARK, slurp(self.unit("hivegrid-remote.service")))
        rc, out = self.cli("uninstall", "--bin-dir", self.bin)
        self.assertEqual(rc, 0, out)
        self.assertEqual(slurp(self.unit("hivegrid-remote.service")), mine)
        self.assertFalse(os.path.exists(self.unit("hivegrid-remote.service.orig")))
        self.assertFalse(os.path.exists(self.unit("hivegrid.service")))

    def test_foreign_unit_and_leftover_orig_stop_the_install(self):
        os.makedirs(os.path.dirname(self.unit("x")))
        for name in ("hivegrid-remote.service", "hivegrid-remote.service.orig"):
            with open(self.unit(name), "w") as fh:
                fh.write("[Service]\n")
        rc, out = self.cli("install", "--bin-dir", self.bin)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out)
        self.assertEqual(slurp(self.unit("hivegrid-remote.service")), "[Service]\n")

    def test_uninstall_reverts_user_parts_and_linger_only_when_ours(self):
        self.cli("install", "--bin-dir", self.bin)
        rc, out = self.cli("uninstall", "--bin-dir", self.bin)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.unit("hivegrid-remote.service")))
        self.assertFalse(os.path.exists(self.root_path("var/lib/systemd/linger/tester")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local/state/hivegrid/autostart.json")))
        self.assertEqual(self.runner.enabled, set())
        # linger that was on before install stays on
        self.write_root("var/lib/systemd/linger/tester", "")
        self.cli("install", "--bin-dir", self.bin)
        self.cli("uninstall", "--bin-dir", self.bin)
        self.assertTrue(os.path.exists(self.root_path("var/lib/systemd/linger/tester")))

    def test_sysctl_install_applies_with_sudo_and_uninstall_restores_old_values(self):
        self.write_root("proc/sys/kernel/panic", "30\n")  # something else set it before
        rc, out = self.cli("install", "--sysctl", "--bin-dir", self.bin)
        self.assertEqual(rc, 0, out)
        dest = self.root_path("etc/sysctl.d/90-hivegrid-recovery.conf")
        self.assertEqual(slurp(dest), A.render_sysctl())
        sudo_cmds = [c for c, sudo in self.runner.calls if sudo]
        self.assertEqual([c[0] for c in sudo_cmds], ["install", "sysctl"])
        self.assertEqual(sudo_cmds[1][:2], ["sysctl", "-p"])
        for key, value, _ in A.SYSCTL:
            self.write_root("proc/sys/" + key.replace(".", "/"), value + "\n")
        self.runner.calls.clear()
        rc, out = self.cli("uninstall", "--sysctl", "--bin-dir", self.bin)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(dest))
        writes = [c for c, sudo in self.runner.calls if sudo and c[0] == "sysctl"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:2], ["sysctl", "-w"])
        self.assertIn("kernel.panic=30", writes[0])
        self.assertIn("kernel.panic_on_oops=0", writes[0])

    def test_autologin_gdm_install_and_exact_uninstall(self):
        rc, out = self.cli("install", "--autologin", "--bin-dir", self.bin)
        self.assertEqual(rc, 0, out)
        conf = self.root_path("etc/gdm3/custom.conf")
        self.assertEqual(A.gdm_autologin(slurp(conf)), ("true", "tester"))
        self.assertIn("without a password", out)  # what the change means is printed first
        self.assertIn("Anyone who powers on this machine", out)
        self.cli("uninstall", "--autologin", "--bin-dir", self.bin)
        self.assertEqual(slurp(conf), GDM_CONF)

    def test_autologin_refuses_to_override_a_login_set_by_hand(self):
        conf = self.root_path("etc/gdm3/custom.conf")
        text = GDM_CONF.replace("[daemon]\n", "[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=someone\n")
        with open(conf, "w") as fh:
            fh.write(text)
        self.runner.calls.clear()
        rc, out = self.cli("install", "--autologin", "--bin-dir", self.bin)
        self.assertEqual(rc, 1)
        self.assertEqual(slurp(conf), text)
        self.assertFalse([c for c, sudo in self.runner.calls if sudo])

    def test_autologin_lightdm_and_sddm_use_their_own_drop_in(self):
        os.unlink(self.root_path("etc/systemd/system/display-manager.service"))
        self.write_root("etc/X11/default-display-manager", "/usr/sbin/lightdm\n")
        self.cli("install", "--autologin", "--bin-dir", self.bin)
        self.assertIn("autologin-user=tester", slurp(self.root_path("etc/lightdm/lightdm.conf.d/50-hivegrid-autologin.conf")))
        self.cli("uninstall", "--autologin", "--bin-dir", self.bin)
        self.assertFalse(os.path.exists(self.root_path("etc/lightdm/lightdm.conf.d/50-hivegrid-autologin.conf")))
        self.write_root("etc/X11/default-display-manager", "/usr/bin/sddm\n")
        self.cli("install", "--autologin", "--bin-dir", self.bin)
        self.assertIn("User=tester", slurp(self.root_path("etc/sddm.conf.d/50-hivegrid-autologin.conf")))

    def test_autologin_with_an_unknown_display_manager_fails(self):
        os.unlink(self.root_path("etc/systemd/system/display-manager.service"))
        rc, out = self.cli("install", "--autologin", "--bin-dir", self.bin)
        self.assertEqual(rc, 1)
        self.assertIn("not supported", out)

    def test_watchdog_needs_hardware_then_loads_the_driver(self):
        rc, out = self.cli("install", "--watchdog", "--bin-dir", self.bin)
        self.assertEqual(rc, 0)
        self.assertIn("no hardware watchdog found", out)
        self.assertFalse(os.path.exists(self.root_path("etc/systemd/system.conf.d")))
        os.makedirs(self.root_path("sys/bus/platform/devices/iTCO_wdt"))
        self.runner.calls.clear()
        rc, out = self.cli("install", "--watchdog", "--bin-dir", self.bin)
        self.assertEqual(rc, 0, out)
        self.assertEqual(slurp(self.root_path("etc/systemd/system/hivegrid-watchdog.service")), A.render_watchdog_unit("iTCO_wdt"))
        self.assertEqual(slurp(self.root_path("etc/systemd/system.conf.d/90-hivegrid-watchdog.conf")), A.render_watchdog())
        sudo_cmds = [c for c, sudo in self.runner.calls if sudo]
        self.assertIn(["modprobe", "iTCO_wdt"], sudo_cmds)
        self.assertIn(["systemctl", "enable", "hivegrid-watchdog.service"], sudo_cmds)
        self.assertIn(["systemctl", "daemon-reexec"], sudo_cmds)
        self.runner.calls.clear()
        self.cli("uninstall", "--watchdog", "--bin-dir", self.bin)
        self.assertFalse(os.path.exists(self.root_path("etc/systemd/system/hivegrid-watchdog.service")))
        self.assertIn(["systemctl", "disable", "hivegrid-watchdog.service"], [c for c, sudo in self.runner.calls if sudo])
        self.assertFalse(os.path.exists(self.root_path("etc/systemd/system.conf.d/90-hivegrid-watchdog.conf")))

    def test_status_wants_the_watchdog_pieces_when_the_hardware_is_there(self):
        os.makedirs(self.root_path("sys/bus/platform/devices/iTCO_wdt"))
        rc, out = self.cli("status", "--bin-dir", self.bin)
        self.assertNotIn("nothing left to do", out)
        self.assertRegex(out, r"todo +\S*90-hivegrid-watchdog\.conf is absent")
        self.assertRegex(out, r"todo +hivegrid-watchdog\.service .*not installed")
        self.assertIn("install --watchdog", out)
        self.cli("install", "--watchdog", "--bin-dir", self.bin)
        self.write_root("etc/systemd/system.conf.d/90-hivegrid-watchdog.conf", "# " + A.MARK + "\n[Manager]\nRuntimeWatchdogSec=1\n")
        rc, out = self.cli("status", "--bin-dir", self.bin)
        self.assertRegex(out, r"todo +\S*90-hivegrid-watchdog\.conf is outdated")
        self.runner.sys_enabled.clear()
        self.cli("install", "--watchdog", "--bin-dir", self.bin)
        self.runner.sys_enabled.clear()
        rc, out = self.cli("status", "--bin-dir", self.bin)
        self.assertRegex(out, r"todo +hivegrid-watchdog\.service .*not enabled")

    def test_a_gdm_block_without_its_end_marker_is_never_rewritten(self):
        conf = self.root_path("etc/gdm3/custom.conf")
        text = GDM_CONF + A.GDM_BEGIN + "\nAutomaticLoginEnable=true\nAutomaticLogin=tester\n\n[security]\nDisallowTCP=true\n"
        with open(conf, "w") as fh:
            fh.write(text)
        self.assertEqual(A.gdm_clear_autologin(text), text)
        self.runner.calls.clear()
        for flag, verb in (("--autologin", "install"), ("--autologin", "uninstall")):
            rc, out = self.cli(verb, flag, "--bin-dir", self.bin)
            self.assertEqual(rc, 1, out)
            self.assertIn("by hand", out)
            self.assertEqual(slurp(conf), text)  # [security] after the block survives
        self.assertFalse([c for c, sudo in self.runner.calls if sudo])

    def test_privileged_content_goes_through_stdin_not_a_temp_file(self):
        rc, out = self.cli("install", "--sysctl", "--bin-dir", self.bin)
        self.assertEqual(rc, 0, out)
        target = self.root_path("etc/sysctl.d/90-hivegrid-recovery.conf")
        self.assertEqual(self.runner.stdin, [(["install", "-D", "-m", "0644", "/dev/stdin", target], A.render_sysctl())])
        self.assertIn(["sysctl", "-p", target], [c for c, sudo in self.runner.calls if sudo])

    def test_user_comes_from_the_password_database_not_the_environment(self):
        env = dict(self.env, USER="mallory", LOGNAME="mallory")
        ctx = A.Ctx(env, io.StringIO(), self.runner, root=self.root)
        self.assertEqual(ctx.user, pwd.getpwuid(os.getuid()).pw_name)

    def test_uninstall_leaves_a_fixed_name_file_that_is_not_ours(self):
        self.write_root("etc/sysctl.d/90-hivegrid-recovery.conf", "kernel.panic = 1\n")  # hand-edited: no mark line
        self.runner.calls.clear()
        rc, out = self.cli("uninstall", "--sysctl", "--bin-dir", self.bin)
        self.assertEqual(rc, 0, out)
        self.assertEqual(slurp(self.root_path("etc/sysctl.d/90-hivegrid-recovery.conf")), "kernel.panic = 1\n")
        self.assertRegex(out, r"warn +\S*90-hivegrid-recovery\.conf is not ours")
        self.assertFalse([c for c, sudo in self.runner.calls if sudo and c[0] == "rm"])

    def test_refuses_to_run_as_root(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc, _ = self.cli("status", euid=0)
        self.assertIn("normal user", err.getvalue())
        self.assertEqual(rc, 2)
        self.assertEqual(self.runner.calls, [])


if __name__ == "__main__":
    unittest.main()
