"""Sign in to a site that refuses the web pane's browser (Google's "This browser or app may not be
secure") in a normal, visible Chrome window that uses the same profile directory as the web pane.

Google refuses sign-in in a browser that is controlled by software, and the web pane's Chrome is
driven over the DevTools pipe. A Chrome started here has no pipe and no debugging port, so the
sign-in works; the cookies and logins it saves are in the profile the web pane uses afterwards.
The password store flag must match the pane's (`--password-store=basic`), or the pane could not
read what this window saved.

Only one Chrome can use a profile directory at a time, so this refuses while a web pane of the
profile has its browser running (close the profile's web panes first)."""
import os
import subprocess
import sys
from pathlib import Path

import hivegrid_settings_core as core

START_URL = "https://accounts.google.com/"


def data_root(environ=None):
    """The web pane's data directory (mux/src/webpane.rs data_root): $XDG_DATA_HOME when absolute."""
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_DATA_HOME", "")
    base = Path(xdg) if os.path.isabs(xdg) else core.home() / ".local" / "share"
    return base / "hivegrid" / "web"


def profile_dir(profile, environ=None):
    """The profile's directory. Valid profile names (core.valid_profile) keep their characters in the
    pane's directory naming, so the name is appended as it is."""
    return data_root(environ) / f"profile-{profile}"


def browser_in_use(pdir, alive=None):
    """The pid of the Chrome that holds the profile (its SingletonLock is a `host-pid` link), or None.
    A lock whose process is gone is stale and does not count."""
    alive = alive or (lambda pid: os.path.exists(f"/proc/{pid}"))
    try:
        target = os.readlink(Path(pdir) / "SingletonLock")
    except OSError:
        return None
    pid = target.rsplit("-", 1)[-1]
    return int(pid) if pid.isdigit() and alive(int(pid)) else None


def choose_browser(settings, environ=None, detect=None):
    """(path, None) or (None, reason): HIVEGRID_CHROME, then the settings' browser, then the first
    installed Chromium-family browser (the pane's own order)."""
    env = os.environ if environ is None else environ
    forced = env.get("HIVEGRID_CHROME", "")
    if forced:
        return (forced, None) if os.access(forced, os.X_OK) else (None, f"HIVEGRID_CHROME={forced} is not executable")
    chosen = settings["browser"]
    if chosen != core.BROWSER_AUTO:
        return (chosen, None) if os.access(chosen, os.X_OK) else (None, f"the browser chosen in Settings, {chosen}, is not executable")
    found, _ = (detect or core.detect_browsers)()
    return (found[0][1], None) if found else (None, "no Chrome or Chromium found")


def command(browser, pdir, url=START_URL):
    """A plain Chrome on the profile: no pipe, no port, no headless. The password store is the pane's."""
    return [browser, f"--user-data-dir={pdir}", "--password-store=basic", "--no-first-run",
            "--no-default-browser-check", url]


def main(argv=None, run=subprocess.call):
    argv = sys.argv[1:] if argv is None else argv
    settings = core.web_settings(core.Settings(core.settings_path()).data.get("web"))
    profile = argv[0] if argv else settings["profile"]
    if core.valid_profile(profile) is not None:
        print(f"invalid profile name {profile!r}")
        return 2
    browser, why = choose_browser(settings)
    if not browser:
        print(why)
        return 1
    pdir = profile_dir(profile)
    pid = browser_in_use(pdir)
    if pid:
        print(f"The profile '{profile}' is in use by the web pane's browser (pid {pid}).")
        print("Close every web pane of it, then run this again.")
        return 1
    pdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    print(f"Opening Chrome on the web profile '{profile}'. Sign in there, then close the window:")
    print("the web panes use the same login afterwards.")
    # Chrome's own stderr (dbus, gcm) is noise in the terminal tab this runs in.
    return run(command(browser, pdir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
