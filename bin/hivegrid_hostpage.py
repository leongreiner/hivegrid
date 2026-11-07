"""What the remote host page of the Hivegrid window needs: the page of a hivegrid-remote on another machine.

A host is an entry of `~/.config/hivegrid/remote-hosts.json` (`{"hosts": [{"name": "slx-002", "url": "https://slx-002.<tailnet>.ts.net"}]}`;
a file of its own, next to settings.json, so a settings backup never makes a machine list itself as a host).
The page is that machine's own remote page (agents, chats, prompts, approvals, new sessions, Bots) in a web pane of the
fork, so this window drives the agents of the other machine and the other machine stays the only host. `ensure()`
checks that the host answers and writes the status file `config/pages.lua` reads, and the one-shot page that sends
the web pane to the host with its access token in the address fragment (the page stores it and removes it at once),
exactly as `hivegrid_botspage` does for the local server. The token of a host lives in its own 0600 file,
`~/.config/hivegrid/hosts/<name>/remote-token`, stored with `hivegrid-host-page token <name>` (read from the
terminal, never from a command line); it is not part of any backup. See docs/remote.md ("Remote host page").
"""
import argparse
import getpass
import http.client
import ipaddress
import json
import os
import re
import ssl
import sys
import time
import urllib.parse

import hivegrid_botspage as B

STATUS_NAME = "hivegrid-host-page.json"
OPEN_NAME = "hivegrid-host-open.html"
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
MAX_HOSTS = 8


def settings_path(env=None):
    env = os.environ if env is None else env
    return env.get("HIVEGRID_SETTINGS") or os.path.join(env.get("HOME") or os.path.expanduser("~"),
                                                         ".config", "hivegrid", "settings.json")


def hosts_path(env=None):
    return os.path.join(os.path.dirname(settings_path(env)), "remote-hosts.json")


def valid_url(url):
    """https://<machine>.<tailnet>.ts.net[:port] or http(s)://<tailscale ip>[:port]: an address inside the
    tailnet, never a path, query or credentials. A page that asks for a token must not be one anybody can name."""
    if not isinstance(url, str) or len(url) > 200 or re.search(r"[\s\x00-\x1f]", url):
        return None
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
        return None
    try:
        host, port = u.hostname, u.port
    except ValueError:
        return None
    if not host:
        return None
    try:
        ip = ipaddress.ip_address(host)
        inside = ip in ipaddress.ip_network("100.64.0.0/10") or ip in ipaddress.ip_network("fd7a:115c:a1e0::/48")
    except ValueError:
        inside = u.scheme == "https" and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net", host) is not None
    if not inside:
        return None
    return urllib.parse.urlunsplit((u.scheme, u.netloc, "/", "", "")), host, port or (443 if u.scheme == "https" else 80)


def hosts(env=None):
    """The valid entries of remote-hosts.json: [{name, url}], in file order, at most MAX_HOSTS, names unique."""
    try:
        with open(hosts_path(env), "r", encoding="utf-8") as f:
            raw = json.load(f).get("hosts")
    except (OSError, ValueError, AttributeError):
        return []
    out, seen = [], set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name, url = entry.get("name"), valid_url(entry.get("url"))
        if isinstance(name, str) and NAME_RE.fullmatch(name) and url and name not in seen:
            seen.add(name)
            out.append({"name": name, "url": url[0]})
        if len(out) == MAX_HOSTS:
            break
    return out


def status_path(env=None):
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isabs(runtime):
        return os.path.join(runtime, STATUS_NAME)
    return f"/tmp/hivegrid-host-page-{os.getuid()}.json"


def open_path(env=None):
    env = os.environ if env is None else env
    return os.path.join(env.get("XDG_RUNTIME_DIR") or "/", OPEN_NAME)


def token_path(name, env=None):
    env = os.environ if env is None else env
    return os.path.join(os.path.dirname(settings_path(env)), "hosts", name, "remote-token")


def read_token(name, env=None):
    try:
        with open(token_path(name, env), "r", encoding="ascii") as f:
            token = f.read(4096).strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token if B.TOKEN_RE.fullmatch(token) else None


def store_token(name, token, env=None):
    """Writes the token file of a host (0600, in a 0700 directory). False for a token of the wrong shape."""
    token = (token or "").strip()
    if not NAME_RE.fullmatch(name) or not B.TOKEN_RE.fullmatch(token):
        return False
    path = token_path(name, env)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(token + "\n")
    os.chmod(path, 0o600)
    return True


def probe(url, timeout=4.0):
    """True when the host answers /healthz with 200 "ok" (certificates are verified for https)."""
    parsed = valid_url(url)
    if not parsed:
        return False
    _, host, port = parsed
    try:
        if url.startswith("https"):
            conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request("GET", "/healthz")
            resp = conn.getresponse()
            return resp.status == 200 and resp.read(16).strip() == b"ok"
        finally:
            conn.close()
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return False


def write_open(name, url, env=None):
    """Writes `open_path()` (0600): a page that sends the web pane to the host with the token in the address
    fragment. -> the path, or None when there is no usable token or XDG_RUNTIME_DIR."""
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    token = read_token(name, env)
    if token is None or not valid_url(url) or not runtime or not os.path.isabs(runtime):
        return None
    html = ("<!doctype html><meta charset=utf-8><title>Remote</title>"
            f"<body style=\"background:#0b0f16\"><script>location.replace({json.dumps(f'{url}#t={token}&r=%2F')});</script>\n")
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


def ensure(name, env=None, probe=probe, wall=time.time):
    """Checks that the host answers, writes the status file and returns it:
    {ts, ok, host, url, open, state: up|unknown_host|no_token|down, message, command}."""
    env = os.environ if env is None else env
    doc = {"ts": wall(), "ok": False, "host": name, "url": None, "open": None, "state": "down", "message": "", "command": ""}
    entry = next((h for h in hosts(env) if h["name"] == name), None)
    if entry is None:
        doc.update(state="unknown_host", message=f"No remote host called {name} in remote-hosts.json.")
    else:
        doc["url"] = entry["url"]
        if read_token(name, env) is None:
            doc.update(state="no_token", command=f"hivegrid-host-page token {name}",
                       message=f"There is no access token for {name} yet. Store it with: hivegrid-host-page token {name}")
        elif not probe(entry["url"]):
            doc.update(message=f"{name} did not answer at {entry['url']}. Is Tailscale up (and its DNS on), "
                               "and hivegrid-remote running there?")
        else:
            doc.update(ok=True, state="up", open=write_open(name, entry["url"], env))
            if doc["open"] is None:
                doc.update(ok=False, message="The access token or the runtime directory is not usable.")
    write_status(doc, env)
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hivegrid-host-page")
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ensure", help="check a remote host and prepare the page to open")
    e.add_argument("--host", required=True)
    t = sub.add_parser("token", help="store the access token of a remote host (asked for on the terminal)")
    t.add_argument("name")
    sub.add_parser("list", help="the configured remote hosts")
    args = parser.parse_args(argv)
    if args.cmd == "list":
        for h in hosts():
            print(h["name"], h["url"], "token" if read_token(h["name"]) else "no token")
        return 0
    if not NAME_RE.fullmatch(args.name if args.cmd == "token" else args.host):
        print("hivegrid-host-page: a host name is lowercase letters, digits and '-'", file=sys.stderr)
        return 2
    if args.cmd == "token":
        if not any(h["name"] == args.name for h in hosts()):
            print(f"hivegrid-host-page: {args.name} is not in remote-hosts.json", file=sys.stderr)
            return 1
        if not store_token(args.name, getpass.getpass(f"Access token of {args.name} (hidden): ")):
            print("hivegrid-host-page: that is not a token (16 to 128 letters, digits, - or _)", file=sys.stderr)
            return 1
        print(f"stored in {token_path(args.name)}")
        return 0
    doc = ensure(args.host)
    print(json.dumps({k: v for k, v in doc.items()}))
    return 0 if doc["ok"] else 1
