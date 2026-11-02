"""What the Bots page of the Hivegrid window needs from the machine: a local `hivegrid-remote` to show.

The page is the phone's own Bots page (`#/bots` of `hivegrid-remote`) in a web pane of the fork, reached
over loopback. `ensure()` makes sure a server answers on 127.0.0.1 (the user unit when it is installed,
else a loopback-only `hivegrid-remote serve` of its own, detached) and writes the result where
`config/pages.lua` reads it (stdout of `hivegrid-bots-page ensure` carries the same): `$XDG_RUNTIME_DIR/hivegrid-bots-page.json`. The access token stays in its
0600 file: only its path is reported, and the config hands the token to the page through the web pane's
control channel, never in a command line, a log or a visible address. See docs/schedules.md ("Host page").
"""
import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import time

DEFAULT_PORT = 8787          # hivegrid_remote.DEFAULT_PORT (a test keeps both equal)
UNIT = "hivegrid-remote.service"
STATUS_NAME = "hivegrid-bots-page.json"
OPEN_NAME = "hivegrid-bots-open.html"
HOST = "127.0.0.1"
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")
ROUTE_RE = re.compile(r"/bots(?:/[a-z0-9-]{3,32})?")


def port(env=None):
    """The port the local server is expected on: HIVEGRID_REMOTE_PORT when it is a valid port, else the default."""
    env = os.environ if env is None else env
    try:
        value = int(env.get("HIVEGRID_REMOTE_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT
    return value if 0 < value < 65536 else DEFAULT_PORT


def status_path(env=None):
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isabs(runtime):
        return os.path.join(runtime, STATUS_NAME)
    return f"/tmp/hivegrid-bots-page-{os.getuid()}.json"


def token_path(env=None):
    """The token file the server reads (hivegrid_remote.default_token_path: next to settings.json)."""
    env = os.environ if env is None else env
    if env.get("HIVEGRID_REMOTE_TOKEN_FILE"):
        return env["HIVEGRID_REMOTE_TOKEN_FILE"]
    settings = env.get("HIVEGRID_SETTINGS")
    if settings:
        return os.path.join(os.path.dirname(settings), "remote-token")
    return os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config", "hivegrid", "remote-token")


def unit_installed(env=None, exists=os.path.exists):
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME")
    base = base if base and os.path.isabs(base) else os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return exists(os.path.join(base, "systemd", "user", UNIT))


def start_command(env=None, exists=os.path.exists):
    """(argv, text): `systemctl --user start hivegrid-remote` when the user unit is installed, else the
    hivegrid-remote program next to this file, loopback only."""
    if unit_installed(env, exists):
        return ["systemctl", "--user", "start", UNIT], "systemctl --user start hivegrid-remote"
    here = os.path.join(os.path.dirname(os.path.realpath(__file__)), "hivegrid-remote")
    argv = [here if exists(here) else "hivegrid-remote", "serve", "--bind", HOST, "--port", str(port(env))]
    return argv, "hivegrid-remote serve"


def probe(host=HOST, p=DEFAULT_PORT, timeout=1.0):
    """True when something answers /healthz with 200 "ok" there (what hivegrid-remote does)."""
    try:
        conn = http.client.HTTPConnection(host, p, timeout=timeout)
        try:
            conn.request("GET", "/healthz")
            resp = conn.getresponse()
            return resp.status == 200 and resp.read(16).strip() == b"ok"
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return False


def open_path(env=None):
    """The one-shot page the web pane is pointed at (see write_open)."""
    env = os.environ if env is None else env
    return os.path.join(env.get("XDG_RUNTIME_DIR") or "/", OPEN_NAME)


def read_token(env=None):
    """The token of the token file when it has the shape hivegrid-remote writes, else None."""
    try:
        with open(token_path(env), "r", encoding="ascii") as f:
            token = f.read(4096).strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token if TOKEN_RE.fullmatch(token) else None


def write_open(route, env=None, new=False):
    """Writes `open_path()` (0600): a page that sends the web pane to the local server with the token in the
    address fragment, which the page stores and removes at once, so the token is in no command line, no log and
    no address that stays visible. The pane shows the file's own address, which carries nothing. -> the path, or
    None when there is no usable token or route or no XDG_RUNTIME_DIR."""
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    token = read_token(env)
    if token is None or not ROUTE_RE.fullmatch(route or "") or not runtime or not os.path.isabs(runtime):
        return None  # the token only ever goes into the user's own runtime directory
    target = f"http://{HOST}:{port(env)}/?embed=1#t={token}&r={route.replace('/', '%2F')}" + ("&n=1" if new else "")
    html = ("<!doctype html><meta charset=utf-8><title>Bots</title>"
            f"<body style=\"background:#0b0f16\"><script>location.replace({json.dumps(target)});</script>\n")
    path = open_path(env)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    return path


def write_status(doc, env=None):
    """Atomic, mode 0600; False when the file cannot be written."""
    path = status_path(env)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    return True


def ensure(env=None, wait=8.0, route="/bots", new=False, probe=probe, popen=subprocess.Popen,
           run=subprocess.run, exists=os.path.exists, sleep=time.sleep, clock=time.monotonic, wall=time.time):
    """Makes sure the local server answers, writes the status file and returns it:
    {ts, ok, url, open, token_file, state: up|started|no_token|down, message, command}. `open` is the one-shot
    page to show (write_open) when ok. `ts` is when the check began, so a reader can tell the answer to its own
    request from an older one."""
    env = os.environ if env is None else env
    began = wall()
    p = port(env)
    doc = {"ts": began, "ok": False, "url": f"http://{HOST}:{p}/", "open": None, "token_file": token_path(env),
           "state": "down", "message": "", "command": ""}

    def done():
        if doc["ok"]:
            doc["open"] = write_open(route, env, new)
            if doc["open"] is None:
                doc.update(ok=False, message="The access token or the route is not usable.")
        write_status(doc, env)
        return doc

    if read_token(env) is None:
        doc.update(state="no_token", command="hivegrid-remote token",
                   message="There is no access token for the local page yet. Create one with: hivegrid-remote token")
        return done()
    if probe(HOST, p):
        doc.update(ok=True, state="up")
        return done()
    argv, text = start_command(env, exists)
    doc["command"] = text
    try:
        if argv[0] == "systemctl":
            r = run(argv, capture_output=True, text=True, timeout=15)
            failed = r.returncode != 0
            why = (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()[:1]
        else:
            popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  start_new_session=True)
            failed, why = False, []
    except (OSError, subprocess.SubprocessError) as exc:
        failed, why = True, [getattr(exc, "strerror", None) or str(exc)]
    if failed:
        doc.update(message=f"The local server could not be started (`{text}`): {why[0] if why else 'failed'}")
        return done()
    end = clock() + wait
    while clock() < end:
        if probe(HOST, p):
            doc.update(ok=True, state="started")
            return done()
        sleep(0.25)
    doc.update(message=f"The local server did not answer on port {p}. Start it with: {text}")
    return done()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hivegrid-bots-page")
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ensure", help="start the local server if needed and prepare the page to open")
    e.add_argument("--wait", type=float, default=8.0, help="seconds to wait for a server that was just started")
    e.add_argument("--route", default="/bots", help="/bots or /bots/<id>")
    e.add_argument("--new", action="store_true", help="open the page on the new-bot sheet")
    args = parser.parse_args(argv)
    if not ROUTE_RE.fullmatch(args.route):
        print("hivegrid-bots-page: --route must be /bots or /bots/<id>", file=sys.stderr)
        return 2
    doc = ensure(wait=max(0.0, min(args.wait, 60.0)), route=args.route, new=args.new)
    print(json.dumps({k: v for k, v in doc.items() if k != "token_file"}))
    return 0 if doc["ok"] else 1
