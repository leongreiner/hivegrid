"""Phone notifications for hivegrid-remote: a POST to an ntfy server when an agent needs you, finishes or hits its limit.

The server is the user's own ntfy on the tailnet (settings.json `remote.notify_url`, the topic URL such as
http://<tailscale-ip>:2586/hivegrid); the ntfy app on the phone subscribes to it. Nothing here talks to any other
host. The Notifier watches the agent list, remembers each agent's last state and sends one message when an agent
changes into a state the user asked for (`remote.notify_events`). It never sends on its first look at the list
(a restart must not replay every `done`), never logs the URL (on an open ntfy server the topic is the secret) and
never retries: a message that did not arrive is dropped, a stale one is worse than none.
Docs: docs/remote.md, "Notifications".
"""
import re
import threading
import urllib.error
import urllib.request

EVENTS = ("attention", "done", "limit")

# state -> (what the message says, ntfy priority, ntfy tag). Titles stay ASCII: they travel as an HTTP header.
MESSAGES = {
    "attention": ("needs you", "4", "bell"),
    "done": ("is done", "3", "white_check_mark"),
    "limit": ("hit its usage limit", "3", "hourglass_flowing_sand"),
}
POST_TIMEOUT = 5.0
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


def line(text, limit):
    """One printable line: control characters become a space, the rest is cut at `limit`."""
    text = _CONTROL.sub(" ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit - 1] + "\u2026"


def http_post(url, data, headers):
    """POST `data` to `url`; raises OSError (URLError, timeout, refused) or ValueError for a bad URL."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=POST_TIMEOUT) as resp:  # noqa: S310 - the scheme is checked by remote_settings
        resp.read(256)


def send(url, title, body, priority="3", tag=None, post=http_post):
    """One ntfy message. The text goes in the body (UTF-8), the short ASCII title and the priority in headers."""
    headers = {"Title": line(title, 120).encode("ascii", "replace").decode("ascii"), "Priority": priority}
    if tag:
        headers["Tags"] = tag
    post(url, body.encode("utf-8"), headers)


class Notifier:
    """`settings_fn()` -> the effective `remote` settings; `agents_fn()` -> the agents (raises when there is no window);
    `describe_fn(agent, agents)` -> the text after the verb, such as the agent's number and title."""

    def __init__(self, settings_fn, agents_fn, describe_fn, post=http_post, log=None):
        self.settings_fn, self.agents_fn, self.describe_fn = settings_fn, agents_fn, describe_fn
        self.post = post
        self.log = log or (lambda msg: None)
        self._seen = None          # pane -> last state; None until the first look after (re)enabling
        self._stop = threading.Event()

    def tick(self):
        """One look at the agents. Returns the (pane, state) pairs a message was tried for."""
        cfg = self.settings_fn()
        url = cfg.get("notify_url")
        if not url:
            self._seen = None
            return []
        try:
            agents = list(self.agents_fn())
        except Exception:  # noqa: BLE001 - no window or a failed `wezterm cli`: look again next time, keep what was seen
            return []
        current = {a.pane: a.state for a in agents}
        previous, self._seen = self._seen, current
        if previous is None:
            return []
        events = cfg.get("notify_events") or EVENTS
        sent = []
        for a in agents:
            before = previous.get(a.pane)
            if a.state == before or a.state not in events or a.state not in MESSAGES:
                continue
            if before is None and a.state != "attention":
                continue           # a pane that appeared already finished or limited: nothing happened that you missed
            verb, priority, tag = MESSAGES[a.state]
            label = line(self.describe_fn(a, agents), 200)
            try:
                send(url, f"Hivegrid: agent {verb}", label, priority, tag, self.post)
            except (OSError, ValueError) as exc:
                self.log(f"notify failed: {type(exc).__name__}")   # never the URL: the topic is the secret
            sent.append((a.pane, a.state))
        return sent

    def start(self, every=5.0):
        def run():
            while not self._stop.wait(every):
                try:
                    self.tick()
                except Exception as exc:  # noqa: BLE001 - the watcher never dies of one bad round
                    self.log(f"notify round failed: {type(exc).__name__}")
        threading.Thread(target=run, name="notifier", daemon=True).start()

    def stop(self):
        self._stop.set()
