"""hivegrid-autostart: make the machine come back by itself after a crash, so the phone can reach it.

Three layers, each its own set of files (docs/autostart.md):

  user (no sudo)        systemd user units for `hivegrid-remote` and for Hivegrid itself (restarted when it
                        crashes, started with the graphical session), and `loginctl enable-linger`;
  --sysctl, --watchdog  a sysctl drop-in that turns kernel faults into a reboot, and the hardware watchdog
                        for a hard hang (files below /etc, written with sudo);
  --autologin           display-manager auto-login, so the session that Hivegrid needs exists after a boot
                        (a file below /etc, written with sudo; it weakens physical security).

The program runs as the normal user and calls `sudo` only for the privileged steps, only when asked with
the flag. `status` reads, `--dry-run` prints every file and command instead of touching anything.
Standard library only."""
import argparse
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import pwd
import textwrap
from pathlib import Path

MARK = "Managed by hivegrid-autostart"
REMOTE_UNIT = "hivegrid-remote.service"
GUI_UNIT = "hivegrid.service"
SYSCTL_FILE = "/etc/sysctl.d/90-hivegrid-recovery.conf"
WATCHDOG_FILE = "/etc/systemd/system.conf.d/90-hivegrid-watchdog.conf"
WATCHDOG_UNIT = "/etc/systemd/system/hivegrid-watchdog.service"
SDDM_FILE = "/etc/sddm.conf.d/50-hivegrid-autologin.conf"
LIGHTDM_FILE = "/etc/lightdm/lightdm.conf.d/50-hivegrid-autologin.conf"
GDM_BEGIN = "# hivegrid-autostart begin (automatic login; removed by `hivegrid-autostart uninstall --autologin`)"
GDM_END = "# hivegrid-autostart end"

# Kernel settings that turn a fault into a reboot: (key, value, why). The order is the file's order.
SYSCTL = [
    ("kernel.panic", "10",
     "Reboot this many seconds after a kernel panic. 0 (the kernel default) waits on the panic screen "
     "forever. Ten seconds leaves the panic message time to reach the console and the log."),
    ("kernel.panic_on_oops", "1",
     "An oops (a bug in a driver: GPU, Wi-Fi, storage) normally kills only the task that hit it, and the "
     "machine limps on, often with a frozen display or a driver that never answers again. Turn it into a "
     "panic so the reboot above happens. The trade: a recoverable oops now costs a reboot."),
    ("kernel.softlockup_panic", "1",
     "A CPU that ran kernel code for 2 x kernel.watchdog_thresh (20 s) without letting any other task run "
     "is stuck; it rarely frees itself. Panic instead of waiting. A false positive needs a kernel path "
     "that really blocks a CPU for 20 s."),
    ("kernel.hardlockup_panic", "1",
     "A CPU that serviced no interrupt for kernel.watchdog_thresh (10 s) is dead to the kernel. Needs the "
     "NMI watchdog (kernel.nmi_watchdog = 1, the default on most laptops); without it this key does "
     "nothing."),
    ("kernel.panic_on_unrecovered_nmi", "1",
     "An NMI the kernel cannot handle: the chipset reports a memory or bus error. The machine is not "
     "trustworthy after one; reboot."),
    ("kernel.panic_on_io_nmi", "1",
     "An NMI from an I/O error (a PCI parity or SERR error). Same reasoning as above."),
]

# Left out on purpose, listed in the file so a reader sees the decision: (key, value, why).
SYSCTL_OFF = [
    ("kernel.hung_task_panic", "1",
     "A task stuck in uninterruptible sleep for kernel.hung_task_timeout_secs (120 s). A slow or hung "
     "disk, USB drive, network file system, FUSE mount or a suspend/resume race trips it without the "
     "machine being dead. Enable it only when your faults are I/O hangs (and raise the timeout)."),
    ("kernel.panic_on_rcu_stall", "1",
     "An RCU stall (60 s) often ends with a warning and the machine recovers. A real one also shows as "
     "a soft lockup, which is already covered."),
    ("kernel.unknown_nmi_panic", "1",
     "An NMI of unknown origin. Some laptops and firmwares raise harmless ones; this would reboot them."),
    ("kernel.panic_on_warn", "1",
     "Every kernel WARN becomes a panic. Graphics and Wi-Fi drivers warn routinely: never on a laptop."),
]

# Watchdog timers the kernel knows, by the platform device that shows the hardware exists:
# (platform device name, kernel module).
WATCHDOG_HW = [
    ("iTCO_wdt", "iTCO_wdt"),  # Intel chipset TCO timer
    ("sp5100-tco", "sp5100_tco"),  # AMD FCH / SP5100 TCO timer
    ("wdat_wdt", "wdat_wdt"),  # ACPI WDAT table (firmware describes the timer)
]
WATCHDOG_TIMEOUT = "60s"
WATCHDOG_REBOOT_TIMEOUT = "2min"


def _wrap(text, prefix="# ", width=100):
    return "\n".join(textwrap.wrap(text, width=width, initial_indent=prefix, subsequent_indent=prefix))


def render_sysctl():
    out = [
        f"# {MARK} (docs/autostart.md). Remove with `hivegrid-autostart uninstall --sysctl`.",
        "#",
        "# Kernel faults that leave a laptop frozen become a reboot, so Hivegrid and hivegrid-remote can",
        "# start again with nobody at the keyboard. A leading \"-\" makes sysctl and systemd-sysctl skip a",
        "# key this kernel does not have instead of failing the whole file.",
        "",
    ]
    for key, value, why in SYSCTL:
        out += [_wrap(why), f"-{key} = {value}", ""]
    out += ["# Not enabled (conservative defaults); remove the \"#\" to opt in.", ""]
    for key, value, why in SYSCTL_OFF:
        out += [_wrap(why), f"#-{key} = {value}", ""]
    return "\n".join(out)


def render_watchdog():
    return "\n".join([
        f"# {MARK} (docs/autostart.md). Remove with `hivegrid-autostart uninstall --watchdog`.",
        "#",
        "# systemd (PID 1) pings the hardware watchdog at half this interval. When the kernel or PID 1",
        "# hangs so hard that the pings stop, the hardware resets the machine. It does nothing without a",
        "# watchdog device (/dev/watchdog0). Under extreme load that starves PID 1 for this long the machine",
        "# resets too; 60 s is far longer than any normal stall.",
        "[Manager]",
        f"RuntimeWatchdogSec={WATCHDOG_TIMEOUT}",
        "# Safety net for a reboot that hangs while shutting down.",
        f"RebootWatchdogSec={WATCHDOG_REBOOT_TIMEOUT}",
        "",
    ])


def render_watchdog_unit(module):
    return "\n".join([
        f"# {MARK} (docs/autostart.md). Remove with `hivegrid-autostart uninstall --watchdog`.",
        "# Distributions blacklist the hardware watchdog drivers and systemd-modules-load honours the",
        "# blacklist; an explicit modprobe ignores it. Once the device exists, systemd re-executes itself",
        "# so that PID 1 opens it and starts feeding it.",
        "[Unit]",
        "Description=Load the hardware watchdog driver and hand the device to systemd",
        "After=systemd-modules-load.service systemd-udevd.service",
        "",
        "[Service]",
        "Type=oneshot",
        "RemainAfterExit=yes",
        f"ExecStart=/usr/sbin/modprobe {module}",
        "ExecStart=/usr/bin/udevadm settle --timeout=10",
        "ExecStart=/bin/sh -c 'if [ -e /dev/watchdog0 ]; then exec /usr/bin/systemctl daemon-reexec; fi'",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def _unit_exec(path, home):
    """A path for ExecStart=: %h for a file below the home directory, quoted when it has spaces."""
    p = str(path)
    home = str(home).rstrip("/")
    if home and (p == home or p.startswith(home + "/")):
        p = "%h" + p[len(home):].replace("%", "%%")
    else:
        p = p.replace("%", "%%")
    return f'"{p}"' if re.search(r"\s", p) else p


def render_remote_unit(exec_path, home):
    return "\n".join([
        f"# {MARK} (docs/autostart.md): `hivegrid-autostart install` rewrites this file,",
        "# `uninstall` removes it.",
        "[Unit]",
        "Description=Hivegrid remote control (phone UI over Tailscale)",
        "# Never give up: the phone depends on this server being there whatever happened before.",
        "StartLimitIntervalSec=0",
        "",
        "[Service]",
        f"ExecStart={_unit_exec(exec_path, home)} serve",
        "Restart=always",
        "# Wait 3 s after the first crash, 6, 12, ... up to 60 s after repeated ones (systemd 254+).",
        "RestartSec=3",
        "RestartSteps=5",
        "RestartMaxDelaySec=60",
        "# Exit 1: no or unsafe token file, or the port is taken; 2: bad arguments. Looping cannot fix them.",
        "RestartPreventExitStatus=1 2",
        "UMask=0077",
        "NoNewPrivileges=yes",
        "LockPersonality=yes",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def render_gui_unit(exec_path, home):
    return "\n".join([
        f"# {MARK} (docs/autostart.md): `hivegrid-autostart install` rewrites this file,",
        "# `uninstall` removes it.",
        "[Unit]",
        "Description=Hivegrid (WezTerm workspace for coding agents)",
        "After=graphical-session.target",
        "PartOf=graphical-session.target",
        "# At most five restarts in ten minutes: a Hivegrid that cannot start must not flap windows forever.",
        "StartLimitIntervalSec=10min",
        "StartLimitBurst=5",
        "",
        "[Service]",
        "WorkingDirectory=%h",
        f"ExecStart={_unit_exec(exec_path, home)} start --cwd .",
        "# A crash or a kill restarts it (and restores the saved layout); closing the window does not.",
        "Restart=on-failure",
        "RestartSec=5",
        "TimeoutStopSec=15",
        "",
        "[Install]",
        "WantedBy=graphical-session.target",
        "",
    ])


def render_sddm(user):
    return f"# {MARK} (docs/autostart.md)\n[Autologin]\nUser={user}\n"


def render_lightdm(user):
    return f"# {MARK} (docs/autostart.md)\n[Seat:*]\nautologin-user={user}\nautologin-user-timeout=0\n"


# ---------------------------------------------------------------- GDM custom.conf

_GDM_KEY = re.compile(r"^\s*(AutomaticLoginEnable|AutomaticLogin)\s*=\s*(.*?)\s*$")


def gdm_block_unterminated(text):
    """True when our begin marker has no end marker after it (a hand-edited file)."""
    inside = False
    for line in text.splitlines():
        if line.strip() == GDM_BEGIN:
            inside = True
        elif inside and line.strip() == GDM_END:
            inside = False
    return inside


def _strip_gdm_block(text):
    """The text without our block; unchanged when the block has no end marker (never drops the rest)."""
    out, inside = [], False
    for line in text.splitlines(keepends=True):
        if inside:
            inside = line.strip() != GDM_END
            continue
        if line.strip() == GDM_BEGIN:
            inside = True
            continue
        out.append(line)
    return text if inside else "".join(out)


def gdm_autologin(text):
    """(enable, user) of the automatic login a GDM config text sets; None for a key that is absent."""
    values = {}
    in_daemon = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_daemon = s.lower() == "[daemon]"
            continue
        m = _GDM_KEY.match(line)
        if in_daemon and m and not s.startswith(("#", ";")):
            values[m.group(1)] = m.group(2)
    return values.get("AutomaticLoginEnable"), values.get("AutomaticLogin")


def gdm_set_autologin(text, user):
    """The custom.conf text with our automatic-login block, or None when the file already sets a
    different automatic login of its own (the user decides, not this program)."""
    rest = _strip_gdm_block(text)
    enable, who = gdm_autologin(rest)
    if enable is not None or who is not None:
        if (enable or "").lower() in ("true", "1", "yes") and who == user:
            return rest  # already set by hand: nothing of ours to add
        return None
    block = [GDM_BEGIN + "\n", "AutomaticLoginEnable=true\n", f"AutomaticLogin={user}\n", GDM_END + "\n"]
    lines = rest.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip().lower() == "[daemon]":
            if not line.endswith("\n"):
                lines[i] = line + "\n"
            return "".join(lines[: i + 1] + block + lines[i + 1:])
    head = rest if (not rest or rest.endswith("\n")) else rest + "\n"
    return head + "\n".join([GDM_BEGIN, "[daemon]", "AutomaticLoginEnable=true", f"AutomaticLogin={user}", GDM_END, ""])


def gdm_clear_autologin(text):
    return _strip_gdm_block(text)


# ---------------------------------------------------------------- the machine, read-only

def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def login_name(home):
    """The account this process runs as, from the password database (the environment can say anything)."""
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return Path(home).name


class Runner:
    """Runs commands. `query` is for read-only commands and always runs; `run` changes something and
    only prints in a dry run. `sudo=True` prefixes sudo unless already root."""

    def __init__(self, out, dry_run=False, euid=None):
        self.out = out
        self.dry_run = dry_run
        self.euid = os.geteuid() if euid is None else euid

    def argv(self, argv, sudo):
        return (["sudo"] + list(argv)) if sudo and self.euid != 0 else list(argv)

    def query(self, argv, timeout=10):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return 127, ""
        return p.returncode, p.stdout

    def run(self, argv, sudo=False, input=None):
        """`input` (text) goes to the command's stdin, so content never touches a file others can write."""
        full = self.argv(argv, sudo)
        if self.dry_run:
            print(f"would run: {shlex.join(full)}", file=self.out)
            return 0
        print(f"run: {shlex.join(full)}", file=self.out, flush=True)
        try:
            return subprocess.run(full, check=False, input=input, text=True if input is not None else None).returncode
        except OSError as exc:
            print(f"  failed: {exc}", file=self.out)
            return 127


class Ctx:
    def __init__(self, env, out, runner, root="/", home=None, bin_dir=None, dry_run=False, user=None):
        self.env = env
        self.out = out
        self.runner = runner
        self.root = Path(root)
        self.home = Path(home or env.get("HOME") or Path.home())
        self.user = user or login_name(self.home)
        self.dry_run = dry_run
        self.config_home = Path(env.get("XDG_CONFIG_HOME") or self.home / ".config")
        self.state_home = Path(env.get("XDG_STATE_HOME") or self.home / ".local" / "state")
        self.unit_dir = self.config_home / "systemd" / "user"
        self.bin_dir = Path(bin_dir) if bin_dir else self.home / ".local" / "bin"
        self.state_file = self.state_home / "hivegrid" / "autostart.json"
        self.failed = []

    def p(self, absolute):
        """A system path (/etc, /proc, ...) below the root this run looks at."""
        return self.root / str(absolute).lstrip("/")

    def say(self, text=""):
        print(text, file=self.out)

    def line(self, tag, text):
        print(f"  {tag:<5} {text}", file=self.out)

    def fail(self, text):
        self.failed.append(text)
        self.line("FAIL", text)

    def run(self, argv, sudo=False, input=None):
        """Runs a command that changes something (a dry run only prints it)."""
        if self.dry_run:
            print(f"would run: {shlex.join(self.runner.argv(argv, sudo))}", file=self.out)
            return 0
        if input is None:
            return self.runner.run(argv, sudo=sudo)
        return self.runner.run(argv, sudo=sudo, input=input)

    # -- the files and commands every install/uninstall step goes through

    def show_content(self, content):
        for row in content.splitlines():
            self.say(f"      | {row}")

    def write_user(self, dest, content, mode=0o644):
        """Atomically writes a file of the user; in a dry run prints it instead."""
        dest = Path(dest)
        if self.dry_run:
            self.say(f"would write {dest}")
            self.show_content(content)
            return
        self.say(f"write {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.chmod(tmp, mode)
        os.replace(tmp, dest)

    def remove_user(self, path):
        path = Path(path)
        if self.dry_run:
            self.say(f"would remove {path}")
            return
        self.say(f"remove {path}")
        path.unlink(missing_ok=True)

    def rename_user(self, src, dst):
        if self.dry_run:
            self.say(f"would rename {src} -> {dst}")
            return
        self.say(f"rename {src} -> {dst}")
        os.replace(src, dst)

    def write_priv(self, dest, content, mode="0644"):
        """Writes a file below /etc with `sudo install` reading the content from stdin (no temp file
        another user could change while the password prompt is open). True when it changed (or would)."""
        target = self.p(dest)
        if read_text(target) == content:
            self.line("ok", f"unchanged {dest}")
            return False
        argv = ["install", "-D", "-m", mode, "/dev/stdin", str(target)]
        if self.dry_run:
            self.say(f"would write {dest} (as root)")
            self.show_content(content)
            self.say("      (the content above is passed to the command on stdin)")
            self.run(argv, sudo=True)
            return True
        self.say(f"write {dest} (as root)")
        rc = self.run(argv, sudo=True, input=content)
        if rc != 0:
            self.fail(f"could not write {dest} (exit {rc})")
            return False
        return True

    def remove_priv(self, dest):
        target = self.p(dest)
        if not target.exists():
            self.line("ok", f"absent {dest}")
            return False
        text = read_text(target)
        if text is None or MARK not in text:
            self.line("warn", f"{dest} is not ours (no \"{MARK}\" line): left alone, remove it by hand if you want")
            return False
        rc = self.run(["rm", "-f", str(target)], sudo=True)
        if rc != 0:
            self.fail(f"could not remove {dest} (exit {rc})")
        return rc == 0

    # -- state kept between install and uninstall (what was there before)

    def load_state(self):
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_state(self, state):
        if self.dry_run:
            what = f"record in {self.state_file}: {json.dumps(state, sort_keys=True)}" if state else f"remove {self.state_file}"
            self.say(f"would {what}")
            return
        if not state:
            self.state_file.unlink(missing_ok=True)
            return
        self.write_user(self.state_file, json.dumps(state, indent=2, sort_keys=True) + "\n", 0o600)


def display_manager(ctx):
    """(family, unit name) of the display manager: family is gdm, sddm, lightdm or None when unknown."""
    name = None
    try:
        name = os.path.basename(os.readlink(ctx.p("/etc/systemd/system/display-manager.service")))
    except OSError:
        text = read_text(ctx.p("/etc/X11/default-display-manager"))
        if text and text.strip():
            name = os.path.basename(text.strip().splitlines()[0])
    if not name:
        return None, None
    base = name[:-8] if name.endswith(".service") else name
    family = {"gdm": "gdm", "gdm3": "gdm", "sddm": "sddm", "lightdm": "lightdm"}.get(base)
    return family, name


def gdm_conf_path(ctx):
    for cand in ("/etc/gdm3/custom.conf", "/etc/gdm/custom.conf"):
        if ctx.p(cand).exists():
            return cand
    return "/etc/gdm3/custom.conf" if ctx.p("/etc/gdm3").is_dir() or not ctx.p("/etc/gdm").is_dir() else "/etc/gdm/custom.conf"


def autologin_user(ctx):
    """(user, where) of the display manager's automatic login, (None, None) when there is none we can see."""
    family, _ = display_manager(ctx)
    if family == "gdm":
        path = gdm_conf_path(ctx)
        text = read_text(ctx.p(path))
        if text is not None:
            enable, who = gdm_autologin(text)
            if who and (enable or "").lower() in ("true", "1", "yes"):
                return who, path
    elif family == "sddm":
        for path in ["/etc/sddm.conf"] + sorted(
                p[len(str(ctx.root)):] for p in glob.glob(str(ctx.p("/etc/sddm.conf.d/*.conf")))):
            m = re.search(r"^\s*User\s*=\s*(\S+)", read_text(ctx.p(path)) or "", re.M)
            if m and "[Autologin]" in (read_text(ctx.p(path)) or ""):
                return m.group(1), path
    elif family == "lightdm":
        paths = ["/etc/lightdm/lightdm.conf"] + sorted(
            p[len(str(ctx.root)):] for p in glob.glob(str(ctx.p("/etc/lightdm/lightdm.conf.d/*.conf"))))
        for path in paths:
            m = re.search(r"^\s*autologin-user\s*=\s*(\S+)", read_text(ctx.p(path)) or "", re.M)
            if m:
                return m.group(1), path
    return None, None


def encrypted_volumes(ctx):
    """Names of the open dm-crypt volumes (LUKS and plain): the passphrase prompt of a boot that nobody
    attends."""
    names = []
    for uuid_file in sorted(glob.glob(str(ctx.p("/sys/block/dm-*/dm/uuid")))):
        if (read_text(uuid_file) or "").startswith("CRYPT-"):
            dm = Path(uuid_file).parent
            names.append((read_text(dm / "name") or dm.parent.name).strip())
    return names


def watchdog_state(ctx):
    """(device files, [(module, loaded)] of the timers the hardware has)."""
    devices = sorted(os.path.basename(p) for p in glob.glob(str(ctx.p("/dev/watchdog*"))))
    present = []
    for platform_dev, module in WATCHDOG_HW:
        if ctx.p(f"/sys/bus/platform/devices/{platform_dev}").exists():
            present.append((module, ctx.p(f"/sys/module/{module}").exists()))
    return devices, present


def sysctl_values(ctx):
    out = {}
    for key, _, _ in SYSCTL:
        val = read_text(ctx.p("/proc/sys/" + key.replace(".", "/")))
        out[key] = val.strip() if val is not None else None
    return out


def unit_state(ctx, name, want):
    """'missing', 'current' (ours and equal), 'outdated' (ours, differs) or 'foreign' (not ours)."""
    text = read_text(ctx.unit_dir / name)
    if text is None:
        return "missing"
    if MARK not in text:
        return "foreign"
    return "current" if text == want else "outdated"


def token_file(ctx):
    override = ctx.env.get("HIVEGRID_REMOTE_TOKEN_FILE")
    if override:
        return Path(override)
    settings = Path(ctx.env.get("HIVEGRID_SETTINGS") or ctx.home / ".config" / "hivegrid" / "settings.json")
    return settings.parent / "remote-token"


def remote_exec(ctx):
    for cand in (ctx.bin_dir / "hivegrid-remote", Path(__file__).resolve().parent / "hivegrid-remote"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    return None


def gui_exec(ctx):
    cand = ctx.bin_dir / "hivegrid"
    if cand.is_file() and os.access(cand, os.X_OK):
        return cand
    for name in ("hivegrid", "wezterm"):
        found = shutil.which(name, path=ctx.env.get("PATH"))
        if found:
            return Path(found)
    return None


def systemctl_user(ctx, *args):
    return ctx.runner.query(["systemctl", "--user", *args])


def unit_flags(ctx, name):
    """(is-enabled text, is-active text), 'unknown' when the user manager cannot be asked."""
    rc_e, enabled = systemctl_user(ctx, "is-enabled", name)
    rc_a, active = systemctl_user(ctx, "is-active", name)
    return (enabled.strip() or "unknown", active.strip() or "unknown")


def linger_on(ctx):
    return ctx.p(f"/var/lib/systemd/linger/{ctx.user}").exists()


# ---------------------------------------------------------------- status

def cmd_status(ctx):
    ctx.say("hivegrid-autostart status (read-only: nothing is changed)")
    todo = []

    ctx.say("\nhivegrid-remote (the phone server)")
    rexec = remote_exec(ctx)
    if rexec is None:
        ctx.line("todo", f"hivegrid-remote not found in {ctx.bin_dir} or next to this program: run install.sh first")
        todo.append("install Hivegrid (install.sh) so that hivegrid-remote exists")
    else:
        st = unit_state(ctx, REMOTE_UNIT, render_remote_unit(rexec, ctx.home))
        enabled, active = unit_flags(ctx, REMOTE_UNIT)
        ctx.line("ok" if st == "current" else "todo", f"unit {ctx.unit_dir / REMOTE_UNIT}: {st}"
                 + (" (a unit of your own; install replaces it and keeps a .orig copy)" if st == "foreign" else ""))
        ctx.line("ok" if enabled == "enabled" else "todo", f"enabled at login/boot: {enabled}; running now: {active}")
        if st != "current" or enabled != "enabled":
            todo.append("hivegrid-autostart install   (remote unit)")
    tok = token_file(ctx)
    try:
        mode = tok.stat().st_mode & 0o777
        ctx.line("ok" if mode & 0o077 == 0 else "warn", f"token file {tok} exists (mode {mode:04o})")
    except OSError:
        ctx.line("todo", f"no token file {tok}: `hivegrid-remote token` creates it; without it the server exits")
        todo.append("hivegrid-remote token")
    rc, out = ctx.runner.query(["systemctl", "is-active", "tailscaled"])
    ctx.line("ok" if out.strip() == "active" else "todo", f"tailscaled (system service): {out.strip() or 'unknown'}")
    rc, out = ctx.runner.query(["systemctl", "is-enabled", "tailscaled"])
    ctx.line("ok" if out.strip() == "enabled" else "todo", f"tailscaled starts at boot: {out.strip() or 'unknown'}")
    rc, out = ctx.runner.query(["tailscale", "serve", "status"], timeout=8)
    if rc == 127:
        ctx.line("info", "tailscale command not found: `tailscale serve` cannot be checked")
    else:
        first = [x for x in out.splitlines() if x.strip()][:3]
        ctx.line("ok" if first and "No serve config" not in out else "todo",
                 "tailscale serve: " + (" / ".join(s.strip() for s in first) if first else "no output"))
        if not first or "No serve config" in out:
            todo.append("tailscale serve --bg --https=443 http://127.0.0.1:8787   (see docs/remote.md)")

    ctx.say("\nHivegrid window")
    gexec = gui_exec(ctx)
    if gexec is None:
        ctx.line("todo", "no `hivegrid` launcher found: run install.sh first")
        todo.append("install Hivegrid (install.sh) so that the hivegrid launcher exists")
    else:
        st = unit_state(ctx, GUI_UNIT, render_gui_unit(gexec, ctx.home))
        enabled, active = unit_flags(ctx, GUI_UNIT)
        ctx.line("ok" if st == "current" else "todo", f"unit {ctx.unit_dir / GUI_UNIT}: {st}")
        ctx.line("ok" if enabled == "enabled" else "todo", f"started with the graphical session: {enabled}; running now: {active}")
        if st != "current" or enabled != "enabled":
            todo.append("hivegrid-autostart install   (Hivegrid unit)")
    session_json = ctx.state_home / "hivegrid" / "session.json"
    settings = read_text(Path(ctx.env.get("HIVEGRID_SETTINGS") or ctx.home / ".config" / "hivegrid" / "settings.json"))
    disabled = False
    try:
        disabled = (json.loads(settings or "{}").get("session") or {}).get("enabled") is False
    except (ValueError, AttributeError):
        pass
    if disabled:
        ctx.line("warn", "session restore is turned off in settings.json: after a reboot Hivegrid opens empty")
    else:
        ctx.line("ok" if session_json.exists() else "info",
                 "session restore on: layout and omp chats come back after a crash or reboot"
                 + ("" if session_json.exists() else f" (no snapshot at {session_json} yet)"))

    ctx.say("\nlogin without anyone at the keyboard")
    ctx.line("ok" if linger_on(ctx) else "todo", f"linger for {ctx.user}: {'on' if linger_on(ctx) else 'off'}"
             + ("" if linger_on(ctx) else " (user services only run while you are logged in)"))
    if not linger_on(ctx):
        todo.append(f"loginctl enable-linger {ctx.user}   (hivegrid-autostart install does it)")
    family, dm = display_manager(ctx)
    if family is None:
        ctx.line("info", f"display manager: {dm or 'not found'}: automatic login is not supported by this program for it")
    else:
        who, where = autologin_user(ctx)
        if who == ctx.user:
            ctx.line("ok", f"{family} ({dm}) logs {who} in at boot ({where})")
        elif who:
            ctx.line("warn", f"{family} ({dm}) logs {who} in automatically ({where}), not {ctx.user}")
        else:
            ctx.line("todo", f"{family} ({dm}): no automatic login; after a reboot the machine waits at the login screen")
            todo.append("hivegrid-autostart install --autologin   (asks sudo; read docs/autostart.md first)")
    crypt = encrypted_volumes(ctx)
    if crypt:
        ctx.line("warn", "encrypted volume(s) open: " + ", ".join(crypt)
                 + ". When one of them holds / or your home, boot stops at a passphrase prompt; "
                   "unattended boot needs TPM auto-unlock (docs/autostart.md)")
    else:
        ctx.line("ok", "no dm-crypt volume is open: no passphrase prompt at boot (a disk that is not open yet is not seen)")

    ctx.say("\nkernel faults become a reboot")
    cur = sysctl_values(ctx)
    for key, want, _ in SYSCTL:
        have = cur[key]
        if have is None:
            ctx.line("info", f"{key}: not in this kernel")
        else:
            ctx.line("ok" if have == want else "todo", f"{key} = {have} (wanted {want})")
    if any(cur[k] not in (None, w) for k, w, _ in SYSCTL):
        todo.append("hivegrid-autostart install --sysctl   (asks sudo)")
    ctx.line("ok" if ctx.p(SYSCTL_FILE).exists() else "info", f"{SYSCTL_FILE}: {'present' if ctx.p(SYSCTL_FILE).exists() else 'absent'}")
    nmi = read_text(ctx.p("/proc/sys/kernel/nmi_watchdog"))
    if nmi is not None and nmi.strip() != "1":
        ctx.line("warn", "kernel.nmi_watchdog is off: hardlockup_panic cannot fire")
    devices, present = watchdog_state(ctx)
    if devices:
        ctx.line("ok", "watchdog device: " + " ".join(f"/dev/{d}" for d in devices))
    elif present:
        ctx.line("todo", "hardware watchdog found (" + ", ".join(m for m, _ in present)
                 + ") but no /dev/watchdog*: the driver is not loaded (distributions blacklist it); --watchdog installs a boot unit that loads it")
        todo.append("hivegrid-autostart install --watchdog   (asks sudo)")
    else:
        ctx.line("info", "no hardware watchdog found: a hard hang that is neither a panic nor an NMI cannot be recovered by software")
    wtext = read_text(ctx.p(WATCHDOG_FILE))
    ctx.line("ok" if wtext == render_watchdog() else "info", f"{WATCHDOG_FILE}: {'present' if wtext else 'absent'}")
    if devices or present:
        if wtext != render_watchdog():
            ctx.line("todo", f"{WATCHDOG_FILE} is {'outdated' if wtext else 'absent'}: systemd does not feed the watchdog")
            todo.append("hivegrid-autostart install --watchdog   (asks sudo)")
        if present:
            utext = read_text(ctx.p(WATCHDOG_UNIT))
            rc, out = ctx.runner.query(["systemctl", "is-enabled", "hivegrid-watchdog.service"])
            if utext != render_watchdog_unit(present[0][0]) or out.strip() != "enabled":
                ctx.line("todo", f"hivegrid-watchdog.service (loads the driver at boot): "
                                 f"{'not installed' if utext is None else 'outdated' if utext != render_watchdog_unit(present[0][0]) else 'not enabled'}")
                if "hivegrid-autostart install --watchdog   (asks sudo)" not in todo:
                    todo.append("hivegrid-autostart install --watchdog   (asks sudo)")

    ctx.say("\nto do" if todo else "\nnothing left to do")
    for t in todo:
        ctx.line("-", t)
    return 0


# ---------------------------------------------------------------- install

def _enable_unit(ctx, name, now):
    enabled, active = unit_flags(ctx, name)
    if enabled == "enabled" and (not now or active == "active"):
        ctx.line("ok", f"{name} already enabled" + (" and running" if now else ""))
        return
    argv = ["systemctl", "--user", "enable"] + (["--now"] if now else []) + [name]
    rc = ctx.run(argv)
    if rc != 0:
        ctx.fail(f"`{shlex.join(argv)}` failed (exit {rc}); is the user manager running (XDG_RUNTIME_DIR set)?")


def _install_unit(ctx, name, content):
    dest = ctx.unit_dir / name
    cur = read_text(dest)
    if cur == content:
        ctx.line("ok", f"unchanged {dest}")
        return False
    if cur is not None and MARK not in cur:
        orig = dest.with_name(name + ".orig")
        if orig.exists():
            ctx.fail(f"{dest} is not ours and {orig} exists already: move one of them away")
            return False
        ctx.rename_user(dest, orig)
    ctx.write_user(dest, content)
    return True


def install_user(ctx, linger):
    ctx.say("\nuser services (no sudo)")
    changed = False
    rexec = remote_exec(ctx)
    gexec = gui_exec(ctx)
    if rexec is None:
        ctx.fail(f"hivegrid-remote not found in {ctx.bin_dir} or next to this program (run install.sh)")
    else:
        changed |= _install_unit(ctx, REMOTE_UNIT, render_remote_unit(rexec, ctx.home))
    if gexec is None:
        ctx.fail("no `hivegrid` launcher found (run install.sh)")
    else:
        changed |= _install_unit(ctx, GUI_UNIT, render_gui_unit(gexec, ctx.home))
    if changed:
        rc = ctx.run(["systemctl", "--user", "daemon-reload"])
        if rc != 0:
            ctx.fail(f"`systemctl --user daemon-reload` failed (exit {rc})")
    if rexec is not None:
        has_token = token_file(ctx).exists()
        if not has_token:
            ctx.line("warn", f"no token file {token_file(ctx)}: enabling the remote unit, not starting it. "
                             "Run `hivegrid-remote token`, then `systemctl --user start hivegrid-remote`.")
        _enable_unit(ctx, REMOTE_UNIT, has_token)
    if gexec is not None:
        _enable_unit(ctx, GUI_UNIT, False)
        ctx.line("info", "Hivegrid is not started now (that would open a second window next to yours): "
                         "it starts with the next graphical session, or `systemctl --user start hivegrid`")
    if linger:
        if linger_on(ctx):
            ctx.line("ok", f"linger already on for {ctx.user}")
        else:
            rc = ctx.run(["loginctl", "enable-linger", ctx.user])
            if rc != 0:
                ctx.fail(f"`loginctl enable-linger {ctx.user}` failed (exit {rc}); run `sudo loginctl enable-linger {ctx.user}`")
            else:
                state = ctx.load_state()
                state["linger_by_us"] = True
                ctx.save_state(state)


def install_sysctl(ctx):
    ctx.say("\nkernel faults become a reboot (sudo)")
    ctx.say("  Changes: panic reboot after 10 s, oops/soft lockup/hard lockup/hardware NMI become a panic."
            " A recoverable kernel bug now costs a reboot. See docs/autostart.md.")
    cur = sysctl_values(ctx)
    state = ctx.load_state()
    priors = state.setdefault("sysctl_priors", {})
    for key, want, _ in SYSCTL:
        if cur[key] is not None and cur[key] != want and key not in priors:
            priors[key] = cur[key]
    before = len(ctx.failed)
    changed = ctx.write_priv(SYSCTL_FILE, render_sysctl())
    if len(ctx.failed) > before:
        return
    if changed or any(cur[k] not in (None, w) for k, w, _ in SYSCTL):
        if any(v for v in priors.values()):
            ctx.save_state(state)
        rc = ctx.run(["sysctl", "-p", str(ctx.p(SYSCTL_FILE))], sudo=True)
        if rc != 0:
            ctx.fail(f"`sysctl -p {SYSCTL_FILE}` failed (exit {rc}); the file is in place and applies at boot")


def install_watchdog(ctx):
    ctx.say("\nhardware watchdog (sudo)")
    devices, present = watchdog_state(ctx)
    if not devices and not present:
        ctx.line("info", "no hardware watchdog found (no iTCO, SP5100 TCO or WDAT timer): nothing to configure. "
                         "softdog is not offered: it dies with the kernel it is supposed to watch.")
        return
    ctx.say("  Changes: systemd pings /dev/watchdog0 and the hardware resets the machine when the pings stop for "
            f"{WATCHDOG_TIMEOUT}.")
    if present:
        module = present[0][0]
        unit_changed = ctx.write_priv(WATCHDOG_UNIT, render_watchdog_unit(module))
        if unit_changed:
            ctx.run(["systemctl", "daemon-reload"], sudo=True)
            ctx.run(["systemctl", "enable", "hivegrid-watchdog.service"], sudo=True)
        if not devices:
            ctx.run(["modprobe", module], sudo=True)
    changed = ctx.write_priv(WATCHDOG_FILE, render_watchdog())
    if changed or not devices:
        ctx.run(["systemctl", "daemon-reexec"], sudo=True)
    ctx.line("info", "check afterwards: `ls /dev/watchdog*` and `sudo journalctl -k -b | grep -i -e wdt -e watchdog`; "
                     "a BIOS that locks the timer shows 'reboot disabled by hardware/BIOS' and then no device appears. "
                     "Suspend and resume the laptop once to confirm it does not reboot.")


def install_autologin(ctx):
    ctx.say("\nautomatic login (sudo)")
    family, dm = display_manager(ctx)
    crypt = encrypted_volumes(ctx)
    ctx.say(f"  Changes: {ctx.user} is logged in at every boot without a password ({dm or 'display manager unknown'}).")
    ctx.say("  Anyone who powers on this machine gets your unlocked desktop, your agents and their API accounts;")
    ctx.say("  the login keyring stays locked. Screen lock applies only after the idle timeout.")
    if crypt:
        ctx.say("  Encrypted volume(s) open: " + ", ".join(crypt) + ". A disk passphrase at boot still needs a person.")
    if family == "gdm":
        path = gdm_conf_path(ctx)
        text = read_text(ctx.p(path))
        if text is None:
            ctx.fail(f"cannot read {path}; create it by hand (docs/autostart.md)")
            return
        if gdm_block_unterminated(text):
            ctx.fail(f"{path} has \"{GDM_BEGIN}\" without \"{GDM_END}\": remove or repair that block by hand (docs/autostart.md)")
            return
        new = gdm_set_autologin(text, ctx.user)
        if new is None:
            ctx.fail(f"{path} sets another automatic login already: edit it by hand (docs/autostart.md)")
            return
        if new == text:
            ctx.line("ok", f"unchanged {path}")
        else:
            ctx.write_priv(path, new)
    elif family == "sddm":
        ctx.write_priv(SDDM_FILE, render_sddm(ctx.user))
    elif family == "lightdm":
        ctx.write_priv(LIGHTDM_FILE, render_lightdm(ctx.user))
    else:
        ctx.fail(f"display manager {dm or 'not found'}: automatic login is not supported here; set it by hand (docs/autostart.md)")
        return
    ctx.line("info", "the display manager is not restarted (that would end your session): it applies at the next boot")


def cmd_install(ctx, args):
    ctx.say("hivegrid-autostart install" + (" (dry run: nothing is changed)" if ctx.dry_run else ""))
    install_user(ctx, linger=not args.no_linger)
    if args.sysctl:
        install_sysctl(ctx)
    if args.watchdog:
        install_watchdog(ctx)
    if args.autologin:
        install_autologin(ctx)
    ctx.say("\nnot done by this program:")
    if not (args.sysctl or args.watchdog or args.autologin):
        ctx.line("-", "kernel fault reboot: install --sysctl [--watchdog] (sudo); unattended login: install --autologin (sudo)")
    ctx.line("-", "token and `tailscale serve`: see `hivegrid-autostart status` and docs/remote.md")
    ctx.line("-", "verify: hivegrid-autostart status")
    return 1 if ctx.failed else 0


# ---------------------------------------------------------------- uninstall

def _uninstall_unit(ctx, name):
    dest = ctx.unit_dir / name
    text = read_text(dest)
    orig = dest.with_name(name + ".orig")
    if text is None:
        ctx.line("ok", f"absent {dest}")
        return False
    if MARK not in text:
        ctx.line("ok", f"{dest} is not ours: left alone")
        return False
    enabled, _ = unit_flags(ctx, name)
    if enabled in ("enabled", "enabled-runtime", "linked"):
        ctx.run(["systemctl", "--user", "disable", name])
    ctx.remove_user(dest)
    if orig.exists():
        ctx.rename_user(orig, dest)
        ctx.line("info", f"restored your earlier {name}")
    return True


def uninstall_user(ctx):
    ctx.say("\nuser services")
    changed = False
    for name in (REMOTE_UNIT, GUI_UNIT):
        changed |= _uninstall_unit(ctx, name)
    if changed:
        ctx.run(["systemctl", "--user", "daemon-reload"])
        ctx.line("info", "a running Hivegrid or hivegrid-remote is not stopped; "
                         "stop it with `systemctl --user stop ...` if you want that")
    state = ctx.load_state()
    if state.get("linger_by_us"):
        if linger_on(ctx):
            rc = ctx.run(["loginctl", "disable-linger", ctx.user])
            if rc != 0:
                ctx.fail(f"`loginctl disable-linger {ctx.user}` failed (exit {rc})")
                return
        state.pop("linger_by_us")
        ctx.save_state(state)
    elif linger_on(ctx):
        ctx.line("info", f"linger for {ctx.user} stays on: it was on before install (or was never recorded)")


def uninstall_sysctl(ctx):
    ctx.say("\nkernel fault reboot (sudo)")
    ctx.remove_priv(SYSCTL_FILE)
    state = ctx.load_state()
    priors = state.get("sysctl_priors") or {}
    cur = sysctl_values(ctx)
    back = [f"{k}={v}" for k, v in priors.items() if cur.get(k) not in (None, v)]
    if back:
        rc = ctx.run(["sysctl", "-w", *back], sudo=True)
        if rc != 0:
            ctx.fail(f"`sysctl -w` of the earlier values failed (exit {rc}); a reboot restores them")
            return
    elif any(cur[k] == w and k not in priors for k, w, _ in SYSCTL):
        ctx.line("info", "the running kernel keeps the values until the next reboot")
    if "sysctl_priors" in state:
        state.pop("sysctl_priors")
        ctx.save_state(state)


def uninstall_watchdog(ctx):
    ctx.say("\nhardware watchdog (sudo)")
    a = ctx.remove_priv(WATCHDOG_FILE)
    c = False
    if ctx.p(WATCHDOG_UNIT).exists():
        ctx.run(["systemctl", "disable", "hivegrid-watchdog.service"], sudo=True)
        c = ctx.remove_priv(WATCHDOG_UNIT)
        ctx.run(["systemctl", "daemon-reload"], sudo=True)
    if a or c:
        ctx.run(["systemctl", "daemon-reexec"], sudo=True)
        ctx.line("info", "the driver stays loaded until the next reboot; systemd stops pinging it after the re-exec")


def uninstall_autologin(ctx):
    ctx.say("\nautomatic login (sudo)")
    family, _ = display_manager(ctx)
    removed = False
    if family == "gdm":
        path = gdm_conf_path(ctx)
        text = read_text(ctx.p(path))
        if text is not None and GDM_BEGIN in text:
            if gdm_block_unterminated(text):
                ctx.fail(f"{path} has \"{GDM_BEGIN}\" without \"{GDM_END}\": remove that block by hand (docs/autostart.md)")
                return
            ctx.write_priv(path, gdm_clear_autologin(text))
            removed = True
    removed |= ctx.remove_priv(SDDM_FILE)
    removed |= ctx.remove_priv(LIGHTDM_FILE)
    if not removed:
        ctx.line("ok", "no automatic login of ours found")


def cmd_uninstall(ctx, args):
    ctx.say("hivegrid-autostart uninstall" + (" (dry run: nothing is changed)" if ctx.dry_run else ""))
    uninstall_user(ctx)
    if args.sysctl or args.all:
        uninstall_sysctl(ctx)
    if args.watchdog or args.all:
        uninstall_watchdog(ctx)
    if args.autologin or args.all:
        uninstall_autologin(ctx)
    return 1 if ctx.failed else 0


# ---------------------------------------------------------------- render (for systemd-analyze verify)

def cmd_render(ctx, args):
    """Writes every file this program would install into one directory, so the units can be checked
    with `systemd-analyze verify --user DIR/*.service` before anything is installed."""
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    rexec = remote_exec(ctx) or ctx.bin_dir / "hivegrid-remote"
    gexec = gui_exec(ctx) or ctx.bin_dir / "hivegrid"
    files = {
        REMOTE_UNIT: render_remote_unit(rexec, ctx.home),
        GUI_UNIT: render_gui_unit(gexec, ctx.home),
        os.path.basename(SYSCTL_FILE): render_sysctl(),
        os.path.basename(WATCHDOG_FILE): render_watchdog(),
        os.path.basename(WATCHDOG_UNIT): render_watchdog_unit("iTCO_wdt"),
    }
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
        ctx.say(f"wrote {out / name}")
    return 0


# ---------------------------------------------------------------- entry

def build_parser():
    ap = argparse.ArgumentParser(
        prog="hivegrid-autostart",
        description="Make this machine come back by itself after a crash or reboot. See docs/autostart.md.")
    sub = ap.add_subparsers(dest="command", required=True)

    st = sub.add_parser("status", help="show what is set up and what is missing (read-only)")
    st.add_argument("--bin-dir", help="where hivegrid-remote and the hivegrid launcher live (default ~/.local/bin)")

    def common(p):
        p.add_argument("--dry-run", action="store_true", help="print every file and command, change nothing")
        p.add_argument("--sysctl", action="store_true", help="kernel faults reboot the machine (sudo)")
        p.add_argument("--watchdog", action="store_true", help="hardware watchdog for a hard hang (sudo)")
        p.add_argument("--autologin", action="store_true", help="automatic login at boot (sudo; weakens physical security)")
        p.add_argument("--bin-dir", help="where hivegrid-remote and the hivegrid launcher live (default ~/.local/bin)")

    inst = sub.add_parser("install", help="install the user services; flags add the privileged parts")
    common(inst)
    inst.add_argument("--no-linger", action="store_true", help="do not run `loginctl enable-linger`")
    un = sub.add_parser("uninstall", help="undo what install did")
    common(un)
    un.add_argument("--all", action="store_true", help="also remove the sysctl, watchdog and automatic-login parts")
    ren = sub.add_parser("render", help="write the files to a directory (to lint them), install nothing")
    ren.add_argument("dir")
    ren.add_argument("--bin-dir")
    return ap


def main(argv=None, *, env=None, out=None, runner=None, euid=None, root="/", user=None):
    args = build_parser().parse_args(argv)
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    dry = bool(getattr(args, "dry_run", False))
    if (os.geteuid() if euid is None else euid) == 0:
        print("hivegrid-autostart: run it as your normal user; it calls sudo itself for the privileged parts", file=sys.stderr)
        return 2
    runner = runner or Runner(out, dry_run=dry, euid=euid)
    ctx = Ctx(env, out, runner, root=root, bin_dir=getattr(args, "bin_dir", None), dry_run=dry, user=user)
    if args.command == "status":
        return cmd_status(ctx)
    if args.command == "install":
        return cmd_install(ctx, args)
    if args.command == "uninstall":
        return cmd_uninstall(ctx, args)
    return cmd_render(ctx, args)
