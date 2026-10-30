"""Prompts sent from the phone that omp may still hold in its queue.

omp writes a prompt to its session file only when it accepts it; while the agent is busy the prompt waits
and the phone's chat shows nothing. `Queued` remembers what the phone sent (per pane, in memory, bounded)
so the chat can show it, greyed out, after the last real item until the session file has the real one.

A queued prompt ends when
- a user text item of the session file, written after the send, has the same text (compared after
  trimming trailing blanks and blank edge lines): one item consumes the oldest queued prompt with that
  text, so a prompt sent twice is shown twice and replaced one item at a time;
- an hour has passed (omp may have rewritten it, e.g. expanded a command, or dropped it);
- the agent has been idle or done for ten seconds since the prompt was sent: omp holds a queued prompt only
  while it works, so it took the prompt as a command, dropped it or had it taken back;
- the pane runs another process (`pid` changed): its queue is not ours;
- the agent has exited: the prompt is kept as `failed` (shown as not delivered) for ten minutes.

Texts are untrusted: they pass the sanitizer on the way in, the page shows them as text only."""
import threading

PER_AGENT = 20            # queued prompts kept per pane; the oldest go first
PANES_MAX = 64            # panes with queued prompts; the one touched longest ago goes first
TTL = 3600.0              # seconds a prompt stays queued
FAILED_TTL = 600.0        # seconds a prompt of an exited agent stays visible as not delivered
QUIET = 10.0              # seconds an idle or done agent may sit on a prompt that is not in its session file
SKEW = 2.0                # clock slack, seconds, when the session file has to be compared by time
SCAN = 200                # user items looked at per check


def normalize(text):
    """The text as compared: line ends unified, trailing blanks of each line and blank edge lines dropped."""
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


class Queued:
    def __init__(self, san_text, limit):
        self.san_text, self.limit = san_text, limit
        self._lock = threading.Lock()
        self._panes = {}                 # pane -> list of entries, oldest first
        self._floor = {}                 # pane -> (path, n) of the newest session item that consumed an entry
        self._touched = {}               # pane -> when it last changed

    # -- recording
    def add(self, agent, text, sent, path, mark, now):
        """Remembers a prompt that was just sent to `agent` (`sent`: the wall time before it was typed).
        `path` and `mark` (`ChatCache.mark`) say where the session file was then, None when unknown."""
        shown = self.san_text(text, self.limit)
        if not shown.strip():
            return
        entry = {"text": shown, "norm": normalize(shown), "ts": sent, "pid": agent.pid, "path": path,
                 "mark": mark if path else None, "failed": None}
        with self._lock:
            self._sweep(now)
            queue = [e for e in self._panes.get(agent.pane, []) if e["pid"] == agent.pid]   # another process left its queue behind
            queue.append(entry)
            self._panes[agent.pane] = queue[-PER_AGENT:]
            self._touched[agent.pane] = now
            while len(self._panes) > PANES_MAX:
                self._drop(min(self._touched, key=self._touched.get))

    def _drop(self, pane):
        self._panes.pop(pane, None)
        self._floor.pop(pane, None)
        self._touched.pop(pane, None)

    @staticmethod
    def _live(entry, now):
        if entry["failed"] is not None:
            return now - entry["failed"] <= FAILED_TTL
        return now - entry["ts"] <= TTL

    def _sweep(self, now):
        for pane in list(self._panes):
            self._panes[pane] = [e for e in self._panes[pane] if self._live(e, now)]
            if not self._panes[pane]:
                self._drop(pane)

    def retain(self, panes):
        """Forgets the prompts of panes that are not in `panes` (their agent is gone)."""
        with self._lock:
            for pane in [p for p in self._panes if p not in panes]:
                self._drop(pane)

    # -- reading
    def view(self, agent, path, items, now):
        """The prompts of `agent` that are still queued, oldest first, as [{"text", "ts", "failed"}], after
        the ones that the session file now has were consumed. `path` is the agent's session file (None:
        none), `items(path, min_n, min_ts)` its newest user text items (`ChatCache.user_items`)."""
        with self._lock:
            queue = self._panes.get(agent.pane)
            if not queue:
                return []
            queue = [e for e in queue if self._live(e, now)]
            if agent.state != "exited":
                queue = [e for e in queue if e["pid"] is None or agent.pid is None or e["pid"] == agent.pid]
            waiting = [e for e in queue if e["failed"] is None]
            if waiting and path:
                self._consume(agent.pane, queue, waiting, path, items)
            if agent.state == "exited":
                for e in queue:
                    if e["failed"] is None:
                        e["failed"] = now
            elif agent.state in ("idle", "done") and agent.since:
                # omp holds a queued prompt only while it works: an agent that has been at rest for a while
                # took the prompt as a command, dropped it or had it taken back (Esc), so it will not show up
                queue = [e for e in queue if e["failed"] is not None or now - max(agent.since, e["ts"]) < QUIET]
            if queue:
                self._panes[agent.pane] = queue
            else:
                self._drop(agent.pane)
            return [{"text": e["text"], "ts": e["ts"], "failed": e["failed"] is not None} for e in queue]

    @staticmethod
    def _after(entry, path, item):
        """Whether the session item `item` was written after `entry` was sent."""
        if entry["mark"] is not None and entry["path"] == path:
            return item["n"] >= entry["mark"]
        return item["ts"] is not None and item["ts"] >= entry["ts"] - SKEW

    def _consume(self, pane, queue, waiting, path, items):
        marks = [e["mark"] for e in waiting if e["mark"] is not None and e["path"] == path]
        if len(marks) == len(waiting):
            got = items(path, min(marks), None)
        else:                              # the file was not known when some were sent: compare by time
            got = items(path, None, min(e["ts"] for e in waiting) - SKEW)
        floor = self._floor.get(pane)
        for item in (got or [])[-SCAN:]:
            if floor is not None and floor[0] == path and item["n"] <= floor[1]:
                continue                   # it already stood in for an earlier prompt
            norm = normalize(item["text"])
            for e in waiting:
                if e["norm"] == norm and self._after(e, path, item):
                    waiting.remove(e)
                    queue.remove(e)
                    floor = self._floor[pane] = (path, item["n"])
                    break
