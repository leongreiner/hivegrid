"""Phone notifications for hivegrid-remote: when an agent needs you, finishes or hits its limit, a POST to an ntfy server
and/or a Web Push message (hivegrid_remote_push.py) to the registered phones.

ntfy is the user's own server on the tailnet (settings.json `remote.notify_url`, the topic URL such as
http://<tailscale-ip>:2586/hivegrid); the ntfy app on the phone subscribes to it, and nothing there talks to any other
host. Web Push (`remote.push`) goes through the browser's push service, encrypted. The Notifier watches the agent list,
remembers each agent's last state and tells the phone once when an agent changes into a state the user asked for
(`remote.notify_events`). It never sends on its first look at the list (a restart must not replay every `done`), never
logs the URL (on an open ntfy server the topic is the secret) and never retries: a message that did not arrive is
dropped, a stale one is worse than none.
Docs: docs/remote.md, "Notifications". `bot_event` is the same for a scheduled bot's result (docs/schedules.md); the
scheduler calls it, so the phone's page and the ntfy message agree on the wording and the deep link.
"""
import json
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


def _headers(title, priority, tag=None, click=None):
    headers = {"Title": line(title, 120).encode("ascii", "replace").decode("ascii"), "Priority": priority}
    if tag:
        headers["Tags"] = tag
    if click:
        headers["Click"] = click
    return headers


def send(url, title, body, priority="3", tag=None, post=http_post):
    """One ntfy message. The text goes in the body (UTF-8), the short ASCII title and the priority in headers."""
    post(url, body.encode("utf-8"), _headers(title, priority, tag))


# A bot's run result: status of the run record -> (the word after the name, ntfy priority, ntfy tag).
BOT_MESSAGES = {
    "ok": ("finished", "3", "white_check_mark"),
    "failed": ("failed", "4", "x"),
    "limit": ("hit its usage limit", "3", "hourglass_flowing_sand"),
    "timeout": ("timed out", "4", "alarm_clock"),
    "cost": ("hit its cost cap", "4", "moneybag"),
    "missed": ("missed a run", "3", "warning"),
}
BOT_PAGE = "/#/bots"


def bot_link(base_url):
    """The address of the phone page's Bots menu below `base_url` (`https://<machine>.<tailnet>.ts.net`), None when
    `base_url` is no plain http(s) address (the same rule as the topic URL: no credentials, spaces or query)."""
    from hivegrid_settings_core import notify_url_setting
    ok = notify_url_setting(base_url) if isinstance(base_url, str) else None
    return ok.rstrip("/") + BOT_PAGE if ok else None


def page_url(path=None):
    """`remote.base_url` of settings.json (the address the phone opens the page at); None when missing or invalid.
    A tolerant reader: an unreadable file or a wrong type means None."""
    from hivegrid_settings_core import settings_path
    try:
        with open(settings_path(path), encoding="utf-8") as f:
            doc = json.load(f)
        value = doc["remote"]["base_url"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, str) and bot_link(value) else None


def bot_event(url, name, status, summary, base_url=None, post=http_post):
    """One ntfy message for a finished (or missed) run of a bot: the title `Hivegrid bot: <name> <status words>`, the body
    the first 200 characters of the run's summary on one line, and, when `base_url` (see `page_url`) is a plain http(s)
    address, a Click header that opens `<base_url>/#/menu/bots` when the notification is tapped. Raises OSError or ValueError
    like `send`; the caller decides whether to log it (never the URL: on an open ntfy server the topic is the secret).
    An unknown status reads as itself at normal priority."""
    verb, priority, tag = BOT_MESSAGES.get(status, (line(status, 30) or "ended", "3", None))
    title = f"Hivegrid bot: {line(name, 60)} {verb}"
    post(url, line(summary, 200).encode("utf-8"), _headers(title, priority, tag, bot_link(base_url) if base_url else None))


class Notifier:
    """`settings_fn()` -> the effective `remote` settings; `agents_fn()` -> the agents (raises when there is no window);
    `describe_fn(agent, agents)` -> the text after the verb, such as the agent's number and title. `push_fn(event, urgency)`
    (hivegrid_remote_push.Pusher.send_async) is called too while `remote.push` is true; `event` is {title, body, tag, url}.
    Each way of telling the phone works alone: ntfy needs `notify_url`, Web Push needs `push` and a registered device."""

    def __init__(self, settings_fn, agents_fn, describe_fn, post=http_post, log=None, push_fn=None):
        self.settings_fn, self.agents_fn, self.describe_fn = settings_fn, agents_fn, describe_fn
        self.post, self.push_fn = post, push_fn
        self.log = log or (lambda msg: None)
        self._seen = None          # pane -> last state; None until the first look after (re)enabling
        self._stop = threading.Event()

    def tick(self):
        """One look at the agents. Returns the (pane, state) pairs a message was tried for."""
        cfg = self.settings_fn()
        url = cfg.get("notify_url")
        push = bool(cfg.get("push") and self.push_fn)
        if not url and not push:
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
            title = f"Hivegrid: agent {verb}"
            if url:
                try:
                    send(url, title, label, priority, tag, self.post)
                except (OSError, ValueError) as exc:
                    self.log(f"notify failed: {type(exc).__name__}")   # never the URL: the topic is the secret
            if push:
                try:
                    self.push_fn({"title": title, "body": label, "tag": f"agent-{a.pane}", "url": f"#/a/{a.pane}"},
                                 "high" if a.state == "attention" else "normal")
                except Exception as exc:  # noqa: BLE001 - a broken push sender never stops the watcher or ntfy
                    self.log(f"push failed: {type(exc).__name__}")
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
