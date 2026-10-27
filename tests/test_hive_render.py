import dataclasses
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
# The block-character cells unless a test asks for image cells (images=True): the result must
# not depend on the terminal the tests run in.
os.environ["HIVEGRID_NO_IMAGES"] = "1"
import hivegrid_agents as A  # noqa: E402
import hivegrid_hex as X  # noqa: E402
import hivegrid_hive as HV  # noqa: E402
import hivegrid_ui as U  # noqa: E402
from hivegrid_settings_core import theme_for  # noqa: E402

NOW = 1_000_000.0


@dataclasses.dataclass
class Agent:
    pane: int
    tab: int = 0
    window: int = 0
    cwd: str = "/home/u/work/proj"
    title: str = ""
    state: str = "idle"
    reason: str = ""
    since: float = NOW - 90
    source: str = "ext"
    pid: int = None
    session_id: str = None
    pending: dict = None
    active: bool = False
    profile: str = None
    error: str = None
    num: int = 0
    tab_index: int = 1


class FakeCore:
    """Stands in for hivegrid_agents; records every action."""

    PLAN_CHOICES = ("execute", "compact", "keep", "refine", "save")

    def __init__(self, agents=()):
        self.agents = list(agents)
        self.calls = []
        self.screen = {}
        self.spawn_result = 99
        self.web_result = 98
        self.origin_result = (10, 0, 0)
        self.gather_result = (True, "gathered 3 agents")
        self.send_result = (True, "sent")
        self.jump_result = True

    def collect(self):
        return [dataclasses.replace(a) for a in self.agents]

    def peek(self, pane, lines=12):
        self.calls.append(("peek", pane))
        return self.screen.get(pane, [f"out {pane}.{i}" for i in range(lines)])[-lines:]

    def send_prompt(self, agent, text):
        self.calls.append(("send", agent.pane, text))
        return self.send_result

    def spawn_agent(self, cwd=None, near=None):
        self.calls.append(("spawn", near.pane if near else None))
        return self.spawn_result

    def jump(self, agent):
        self.calls.append(("jump", agent.pane))
        return self.jump_result

    def ack(self, agent):
        self.calls.append(("ack", agent.pane))

    def delete_agent(self, agent):
        self.calls.append(("delete", agent.pane))
        return True, "closed"

    def origin(self):
        return self.origin_result

    def gather(self, tab):
        self.calls.append(("gather", tab))
        return self.gather_result

    def answer_plan_review(self, agent, choice, feedback=None, role=None):
        self.calls.append(("answer", agent.pane, choice, feedback))
        return True, "sent"

    def normalize_url(self, text, search=""):
        return A.normalize_url(text, search)

    web_supported = True

    def web_build(self):
        return self.web_supported

    def spawn_web(self, url, profile=None):
        self.calls.append(("web", url, profile))
        return self.web_result

    def actions(self):
        return [c for c in self.calls if c[0] != "peek"]


class FakeModel:
    dirty = False

    def current_theme(self):
        return theme_for("hivegrid-navy")


class FakeUI(U.UI):
    """The real frame and event routing without a terminal."""

    def __init__(self, page):
        self.model, self.pages, self.active = FakeModel(), [page], 0
        self.modal, self.message, self.message_error = None, "", False
        self.cv, self.dirty, self.running, self.size, self.tickers = None, True, True, (0, 0), []
        self.out = io.StringIO()
        self.hover = self.pointer = None


def key(k, *mods):
    return ("key", frozenset(mods), k)


def click(x, y):
    return ("mouse", "press", "left", x, y)


def text_of(ui):
    return ["".join(c[0] for c in row) for row in ui.cv.cells]


STATES = ["working", "busy", "done", "attention", "idle", "exited", "working"]


def seven():
    out = []
    for i, st in enumerate(STATES):
        out.append(Agent(pane=10 + i, state=st, cwd=f"/home/u/work/proj{i}", since=NOW - 30 * (i + 1),
                         reason="bash: npm test (42s)" if st == "busy" else ""))
    out[3].pending = {"kind": "plan_review", "reviewId": "r1", "title": "Add the hive",
                      "choices": [{"id": "execute", "label": "Execute"}, {"id": "refine", "label": "Refine"},
                                  {"id": "keep", "label": "Keep"}]}
    return out


class Base(unittest.TestCase):
    def make(self, agents=None, size=(100, 30)):
        self.core = FakeCore(seven() if agents is None else agents)
        self.page = HV.HivePage(FakeModel(), core=self.core, now=lambda: NOW, background=lambda f: f())
        self.ui = FakeUI(self.page)
        self.size = size
        self.page.on_show(self.ui)
        self.draw()
        return self.page

    def draw(self, size=None):
        self.size = size or self.size
        self.ui.size = self.size
        self.ui.draw()
        return text_of(self.ui)

    def send(self, *events):
        for ev in events:
            self.ui.handle(ev)
        return self.draw()

    def cell(self, pane):
        return next(c for c in self.page.cells if c["id"] == pane)

    def click_cell(self, pane):
        c = self.cell(pane)
        if c["region"]:
            i = len(c["region"]) // 2
            x = c["x"] + c["region"][i][0] + 1
            y = c["y"] + i
        else:
            x, y = c["x"] + 1, c["y"]
        self.ui.handle(click(x, y))
        self.draw()


class Render(Base):
    def test_cells_show_glyph_number_and_folder_80x24(self):
        self.make(size=(80, 24))
        lines = self.draw()
        body = "\n".join(lines)
        for pane, st in zip(range(10, 17), STATES):
            c = self.cell(pane)
            self.assertEqual(c["kind"], "agent")
            self.assertIn(HV.GLYPH[st], body)
            # the glyph, the number and the folder sit inside the cell's box, in this order
            rows = [lines[c["y"] + i][c["x"]:c["x"] + c["w"]] for i in range(c["h"])]
            joined = "\n".join(rows)
            self.assertIn(str(pane), joined)
            self.assertIn(f"proj{pane - 10}", joined)
            self.assertLess(joined.index(HV.GLYPH[st]), joined.index(str(pane)))
            self.assertLess(joined.index(str(pane)), joined.index(f"proj{pane - 10}"))
        add = [c for c in self.page.cells if c["kind"] == "add"]
        self.assertEqual(len(add), 1)
        self.assertEqual(add[0]["slot"], 7)
        self.assertIn("+", "\n".join(lines[add[0]["y"]:add[0]["y"] + add[0]["h"]]))

    def test_cell_shows_only_status_nothing_else(self):
        self.make(size=(100, 30))
        body = "\n".join(self.draw())
        for secret in ("npm test", "Add the hive", "/home/u"):
            self.assertNotIn(secret, body)

    def test_legend_counts_and_glyphs(self):
        self.make(size=(100, 30))
        legend = self.draw()[2]
        for st, n in (("working", 2), ("busy", 1), ("attention", 1), ("done", 1), ("idle", 1), ("exited", 1)):
            self.assertIn(f"{HV.GLYPH[st]} {HV.WORD[st]} {n}", legend)
        self.assertIn("7 agents", legend)
        self.make(size=(60, 24))
        self.assertIn("↻2", self.draw()[2])

    def test_state_colours_are_distinct_and_stable(self):
        self.make(size=(100, 30))
        p = FakeModel().current_theme()["palette"]
        fills = {}
        for pane, st in zip(range(10, 17), STATES):
            c = self.cell(pane)
            mid = len(c["rows"]) // 2
            x = c["x"] + c["rows"][mid][0] + 1
            fills[st] = self.ui.cv.cells[c["y"] + mid][x][2]
        self.assertEqual(fills["working"], p["accent"])
        self.assertEqual(fills["done"], p["green"])
        self.assertEqual(fills["busy"], p["amber"])
        self.assertEqual(fills["attention"], p["violet"])
        self.assertEqual(len(set(fills.values())), 6)

    def test_bigger_terminal_bigger_hexes(self):
        self.make(size=(80, 24))
        small = self.cell(10)["w"]
        self.make(size=(160, 50))
        self.assertGreater(self.cell(10)["w"], small)
        lines = self.draw()
        self.assertIn("proj6", "\n".join(lines))

    def test_empty_hive_shows_add_cell_and_hint(self):
        self.make(agents=[], size=(80, 24))
        lines = "\n".join(self.draw())
        self.assertIn("No agents yet", lines)
        self.assertEqual([c["kind"] for c in self.page.cells], ["add"])

    def test_cells_never_overlap_and_stay_in_area(self):
        for size in ((80, 24), (160, 50), (60, 20), (120, 36)):
            self.make(size=size)
            self.draw()
            x, y, w, h = self.page.area
            seen = {}
            for c in self.page.cells:
                for i, (a, b) in enumerate(c["region"] or [(0, c["w"])] * c["h"]):
                    for col in range(a, b):
                        pt = (c["x"] + col, c["y"] + i)
                        self.assertNotIn(pt, seen)
                        seen[pt] = c["id"]
                        self.assertTrue(x <= pt[0] < x + w and y <= pt[1] < y + h, (size, pt))

    def test_tile_fallback_when_hexes_are_too_small(self):
        self.make(size=(40, 12))
        self.draw()
        self.assertTrue(self.page.fallback)
        self.assertTrue(all(c["rows"] is None for c in self.page.cells))
        body = "\n".join(self.draw())
        self.assertIn("tiles", body.splitlines()[2])
        for pane in (10, 11):
            self.assertIn(str(pane), body)

    def test_t_toggles_hex_and_tile(self):
        self.make(size=(100, 30))
        self.assertIsNotNone(self.cell(10)["rows"])
        self.send(key("t"))
        self.assertEqual(self.page.mode, "tile")
        self.assertIsNone(self.cell(10)["rows"])
        self.assertIn("tiles", self.draw()[2])
        self.send(key("t"))
        self.assertIsNotNone(self.cell(10)["rows"])

    def test_tile_scrolls_to_the_selection_when_it_overflows(self):
        agents = [Agent(pane=i) for i in range(60)]
        self.make(agents=agents, size=(30, 6))
        self.page.select(59)
        self.draw()
        self.assertIn(59, [c["id"] for c in self.page.cells])
        self.assertGreater(self.page.hidden, 0)


class Popover(Base):
    def test_popover_stays_inside_the_canvas_for_every_agent_and_size(self):
        for size in ((80, 24), (160, 50), (60, 20), (100, 30), (44, 16)):
            self.make(size=size)
            for pane in range(10, 17):
                self.page.select(pane)
                self.draw()
                x, y, w, h = self.page.pop_rect
                ax, ay, aw, ah = self.page.area
                self.assertTrue(x >= ax and y >= ay and x + w <= ax + aw and y + h <= ay + ah, (size, pane, self.page.pop_rect, self.page.area))
                self.assertTrue(x + w <= self.ui.cv.w and y + h <= self.ui.cv.h)
                self.assertGreater(w, self.cell(pane)["w"] * 2 if size[0] >= 100 else 20, "larger than the hex")

    def test_popover_content(self):
        self.make(size=(100, 30))
        self.core.screen[11] = ["building…", "all green", "$ ready"]
        self.page.select(11)
        body = "\n".join(self.draw())
        self.assertIn("◔ busy", body)
        self.assertIn("bash: npm test (42s)", body)
        self.assertIn("1m", body)  # since = 60 s before NOW
        self.assertIn("~/" if os.path.expanduser("~") == "/home/u" else "/home/u/work/proj1", body)
        for line in self.core.screen[11]:
            self.assertIn(line, body)
        self.assertIn("message may be queued", body)
        for label in ("Jump", "Ack", "Delete"):
            self.assertIn(label, body)

    def test_popover_next_to_the_hex_without_covering_it_when_room(self):
        self.make(size=(160, 50))
        self.page.select(10)
        self.draw()
        x, y, w, h = self.page.pop_rect
        for c in self.page.cells:
            self.assertTrue(x >= c["x"] + c["w"] or x + w <= c["x"], ("covers a cell", c["id"]))

    def test_cells_show_the_dock_digit_tab_qualified_across_tabs(self):
        a = Agent(pane=7, num=2, cwd="/w/alpha")
        b = Agent(pane=9, num=1, cwd="/w/beta")
        self.make(agents=[a, b], size=(100, 30))
        self.assertEqual([self.page.number(x) for x in self.page.agents], ["2", "1"])
        body = "\n".join(self.draw())
        self.assertNotIn("7", body.split("\n", 2)[2].replace("alpha", ""))
        c = Agent(pane=11, num=1, tab=5, tab_index=2, cwd="/w/gamma")
        self.make(agents=[a, b, c], size=(100, 30))
        self.assertEqual(self.page.number(self.page.by_pane[11]), "2·1")
        self.assertEqual(self.page.number(self.page.by_pane[7]), "1·2")

    def test_attention_popover_shows_pending_and_disables_prompt(self):
        self.make(size=(100, 30))
        self.page.select(13)
        body = "\n".join(self.draw())
        self.assertIn("Plan review: Add the hive", body)
        for label in ("Execute", "Refine", "Keep"):
            self.assertIn(label, body)
        self.assertIn("Prompt off", body)
        self.assertIn("prompt disabled", body)

    def test_approval_and_ask_are_display_only(self):
        a = Agent(pane=1, state="attention", pending={"kind": "approval", "tool": "bash", "summary": "rm -rf build/"})
        b = Agent(pane=2, state="attention", pending={"kind": "ask", "question": "Which db?", "options": ["pg", "sqlite"]})
        self.make(agents=[a, b], size=(100, 30))
        self.page.select(1)
        body = "\n".join(self.draw())
        self.assertIn("Approval needed: bash", body)
        self.assertIn("rm -rf build/", body)
        self.page.select(2)
        body = "\n".join(self.draw())
        self.assertIn("Which db?", body)
        self.assertIn("· pg", body)
        self.assertIn("j jumps there", body)

    def test_error_line_and_exited_disable(self):
        a = Agent(pane=1, state="exited", error="boom")
        self.make(agents=[a])
        self.page.select(1)
        body = "\n".join(self.draw())
        self.assertIn("! boom", body)
        self.assertIn("Prompt off: the agent has exited", body)
        self.assertFalse(self.page.focus)

    def test_place_popover(self):
        area = (0, 2, 100, 20)
        # right of the anchor
        self.assertEqual(HV.place_popover(area, (10, 5, 10, 6), [40], 10)[:2], (21, 3))
        # left when the right is full
        x, y, w, h = HV.place_popover(area, (80, 5, 10, 6), [40], 10)
        self.assertEqual((x, w), (39, 40))
        # a narrower width is tried before covering the anchor
        x, y, w, h = HV.place_popover((0, 0, 80, 24), (28, 5, 14, 8), [44, 36], 10)
        self.assertEqual((x, w), (43, 36))
        # pinned (covering) when nothing fits, always inside
        x, y, w, h = HV.place_popover((0, 0, 50, 24), (18, 5, 14, 8), [44], 10)
        self.assertEqual((x, w), (6, 44))
        # vertical clamp
        x, y, w, h = HV.place_popover(area, (10, 19, 10, 4), [30], 18)
        self.assertEqual(y + h, 22)


class Keyboard(Base):
    def test_arrows_select_and_open_popover(self):
        self.make()
        self.assertIsNone(self.page.sel)
        self.send(key("RightArrow"))
        self.assertEqual(self.page.sel, 10)
        self.assertTrue(self.page.open)
        self.send(key("RightArrow"))
        self.assertEqual(self.page.sel, 11)  # slot 1 is right of the centre cell
        self.send(key("Tab"))
        self.assertEqual(self.page.sel, 12)
        self.send(key("Tab", "SHIFT"))
        self.assertEqual(self.page.sel, 11)

    def test_arrow_moves_to_the_geometric_neighbour(self):
        self.make(size=(100, 30))
        self.page.select(10)
        c0 = self.cell(10)
        for k, check in (("RightArrow", lambda c: c["cx"] > c0["cx"] and c["cy"] == c0["cy"]),
                         ("LeftArrow", lambda c: c["cx"] < c0["cx"] and c["cy"] == c0["cy"]),
                         ("DownArrow", lambda c: c["cy"] > c0["cy"]),
                         ("UpArrow", lambda c: c["cy"] < c0["cy"])):
            self.page.select(10)
            self.send(key(k))
            self.assertNotEqual(self.page.sel, 10, k)
            self.assertTrue(check(self.cell(self.page.sel)), k)

    def test_tab_cycles_through_agents_and_the_add_cell(self):
        self.make()
        seen = []
        for _ in range(8):
            self.send(key("Tab"))
            seen.append(self.page.sel)
        self.assertEqual(seen, [10, 11, 12, 13, 14, 15, 16, HV.ADD])
        self.send(key("Tab"))
        self.assertEqual(self.page.sel, 10)
        self.assertFalse(self.page.open and self.page.sel == HV.ADD)

    def test_type_prompt_and_enter_sends(self):
        self.make()
        self.send(key("Tab"), key("Tab"))  # pane 11: busy, prompt enabled
        self.assertEqual(self.page.sel, 11)
        self.assertTrue(self.page.focus)
        for ch in "hi q":
            self.send(key("Space") if ch == " " else key(ch))
        self.send(key("p", "SHIFT"))
        self.assertTrue(self.ui.running, "q typed into the prompt must not close the panel")
        self.assertEqual(self.page.drafts[11], "hi qP")
        self.assertIn("› hi qP▏", "\n".join(self.draw()))
        self.send(key("Backspace"))
        self.send(key("Enter"))
        self.assertEqual(self.core.actions(), [("send", 11, "hi q")])
        self.assertEqual(self.page.drafts[11], "")
        self.assertEqual(self.ui.message, "sent")

    def test_failed_send_keeps_the_text_and_shows_the_reason(self):
        self.make()
        self.core.send_result = (False, "send-text failed")
        self.send(key("Tab"))
        for ch in "go":
            self.send(key(ch))
        self.send(key("Enter"))
        self.assertEqual(self.page.drafts[10], "go")
        self.assertEqual((self.ui.message, self.ui.message_error), ("send-text failed", True))

    def test_empty_prompt_does_not_send(self):
        self.make()
        self.send(key("Tab"), key("Enter"))
        self.assertEqual(self.core.actions(), [])

    def test_attention_disables_the_prompt(self):
        self.make()
        self.page.select(13)
        self.assertFalse(self.page.focus)
        self.send(key("x"), key("Enter"))
        self.assertNotIn(13, self.page.drafts)
        self.assertEqual([c for c in self.core.actions() if c[0] == "send"], [])
        self.assertTrue(self.ui.running)

    def test_drafts_are_kept_per_agent(self):
        self.make()
        self.send(key("Tab"))
        self.send(key("a"))
        self.send(key("Tab"), key("b"))
        self.assertEqual(self.page.sel, 11)
        self.send(key("Tab", "SHIFT"))
        self.assertEqual(self.page.sel, 10)
        self.assertEqual(self.page.drafts, {10: "a", 11: "b"})

    def test_escape_closes_popover_first_then_panel(self):
        self.make()
        self.send(key("Tab"))
        self.assertTrue(self.page.open)
        self.send(key("Escape"))
        self.assertFalse(self.page.open)
        self.assertEqual(self.page.sel, 10, "selection stays")
        self.assertTrue(self.ui.running)
        self.send(key("Escape"))
        self.assertFalse(self.ui.running)

    def test_q_closes_the_panel_only_when_the_prompt_is_not_focused(self):
        self.make()
        self.send(key("Tab"))  # opens the popover with the prompt focused
        self.assertTrue(self.page.focus)
        self.send(key("q"))
        self.assertTrue(self.ui.running)
        self.assertEqual(self.page.drafts[10], "q")
        self.page.focus = False  # e.g. a disabled prompt
        self.send(key("q"))
        self.assertFalse(self.ui.running)

    def test_q_quits_with_nothing_open(self):
        self.make()
        self.send(key("q"))
        self.assertFalse(self.ui.running)

    def test_command_keys_when_prompt_unfocused(self):
        self.make()
        self.page.select(12)  # done
        self.page.focus = False
        self.send(key("a"))
        self.assertIn(("ack", 12), self.core.calls)
        self.send(key("j"))
        self.assertIn(("jump", 12), self.core.calls)
        self.assertFalse(self.ui.running, "jump closes the panel")

    def test_alt_keys_work_while_typing(self):
        self.make()
        self.send(key("Tab"))
        self.assertTrue(self.page.focus)
        self.send(key("j", "ALT"))
        self.assertIn(("jump", 10), self.core.calls)

    def test_failed_jump_keeps_the_panel_open(self):
        self.make()
        self.core.jump_result = False
        self.page.select(12)
        self.page.focus = False
        self.send(key("j"))
        self.assertTrue(self.ui.running)
        self.assertTrue(self.ui.message_error)

    def test_new_agent_key_spawns_near_selection_and_selects_it_when_it_appears(self):
        self.make()
        self.page.select(12)
        self.page.focus = False
        self.send(key("n"))
        self.assertEqual(self.core.actions(), [("spawn", 12)])
        self.core.agents.append(Agent(pane=99, state="idle"))
        self.page.tick(self.ui)
        self.assertEqual(self.page.sel, 99)
        self.assertTrue(self.page.open)

    def test_failed_spawn_reports(self):
        self.make()
        self.core.spawn_result = None
        self.send(key("n"))
        self.assertTrue(self.ui.message_error)
        self.assertIsNone(self.page.want)

    def test_enter_on_add_cell_spawns(self):
        self.make()
        for _ in range(8):
            self.send(key("Tab"))
        self.assertEqual(self.page.sel, HV.ADD)
        self.send(key("Enter"))
        self.assertEqual(self.core.actions(), [("spawn", 10)])  # the agent under the hive (origin)

    def test_delete_asks_when_busy_and_acts_after_yes(self):
        self.make()
        self.page.select(10)  # working
        self.page.focus = False
        self.send(key("d"))
        self.assertIsInstance(self.ui.modal, U.ConfirmModal)
        self.assertEqual(self.core.actions(), [])
        self.send(key("n"))
        self.assertIsNone(self.ui.modal)
        self.assertEqual(self.core.actions(), [])
        self.send(key("d"), key("y"))
        self.assertEqual(self.core.actions(), [("delete", 10)])

    def test_delete_of_done_or_exited_needs_no_confirmation(self):
        self.make()
        for pane in (12, 15):
            self.core.calls.clear()
            self.page.select(pane)
            self.page.focus = False
            self.send(key("d"))
            self.assertIsNone(self.ui.modal)
            self.assertEqual(self.core.actions(), [("delete", pane)])

    def test_compact_reassigns_slots_but_deleting_does_not(self):
        self.make()
        before = {p: (c["x"], c["y"]) for p in range(10, 17) for c in [self.cell(p)]}
        self.core.agents = [a for a in self.core.agents if a.pane != 12]
        self.page.refresh()
        self.draw()
        for p in (10, 11, 13, 14, 15, 16):
            self.assertEqual((self.cell(p)["x"], self.cell(p)["y"]), before[p], "stable slot")
        add = [c for c in self.page.cells if c["kind"] == "add"][0]
        self.assertEqual(add["slot"], 2, "the + cell takes the free slot")
        self.send(key("c"))
        self.assertEqual(sorted(self.page.slots.values()), list(range(6)))
        self.assertEqual(self.page.slots[13], 2)
        # a new agent fills the lowest free slot
        self.core.agents.append(Agent(pane=50))
        self.page.refresh()
        self.assertEqual(self.page.slots[50], 6)

    def test_plan_review_choices_by_key_and_by_refine_prompt(self):
        self.make(size=(100, 30))
        self.page.select(13)
        self.send(key("1"))
        self.assertEqual(self.core.actions(), [("answer", 13, "execute", None)])
        self.send(key("2"))
        self.assertIsInstance(self.ui.modal, U.PromptModal)
        self.send(key("Enter"))  # empty feedback refused
        self.assertIsInstance(self.ui.modal, U.PromptModal)
        for ch in "why":
            self.send(key(ch))
        self.send(key("Enter"))
        self.assertIsNone(self.ui.modal)
        self.assertEqual(self.core.actions()[-1], ("answer", 13, "refine", "why"))

    def test_enter_reopens_the_popover_and_i_refocuses_the_prompt(self):
        self.make()
        self.send(key("Tab"), key("Escape"))
        self.assertFalse(self.page.open)
        self.send(key("Enter"))
        self.assertTrue(self.page.open)
        self.assertTrue(self.page.focus)
        self.page.focus = False
        self.send(key("i"))
        self.assertTrue(self.page.focus)

    def test_alt_n_spawns_while_typing(self):
        self.make()
        self.send(key("Tab"))
        self.send(key("n", "ALT"))
        self.assertEqual(self.core.actions(), [("spawn", 10)])
        self.assertEqual(self.page.drafts.get(10, ""), "")

    def test_f_keys_still_switch_panels_while_typing(self):
        self.make()
        self.send(key("Tab"))
        self.assertTrue(self.page.focus)
        self.assertFalse(self.page.captures(frozenset(), "F2"))
        self.assertTrue(self.page.captures(frozenset(), "q"))

    def test_hints_change_with_context(self):
        self.make()
        idle = self.page.hints()
        self.send(key("Tab"))
        typing = self.page.hints()
        self.page.focus = False
        command = self.page.hints()
        self.assertEqual(len({idle, typing, command}), 3)
        for h in (idle, typing, command):
            self.assertLessEqual(len(h), 78, h)
            self.assertIn("gather", h)


class OtherTabs(Base):
    """Agents of tabs other than the one the hive was opened over: marked, and `g` gathers them."""

    def tabs(self):
        agents = seven()
        for a in agents[4:]:
            a.tab, a.tab_index = 3, 2
        return agents

    def test_legend_counts_agents_in_other_tabs(self):
        self.make(self.tabs(), size=(120, 30))
        legend = self.draw()[2]
        self.assertIn("7 agents (3 in other tabs · g gathers)", legend)
        self.make(seven(), size=(120, 30))
        self.assertNotIn("other tabs", self.draw()[2])

    def test_popover_header_marks_the_tab_of_an_agent_elsewhere(self):
        self.make(self.tabs(), size=(100, 30))
        self.page.select(14)
        self.assertIn("⇥ tab 2", "\n".join(self.draw()))
        self.page.select(11)
        self.assertNotIn("⇥ tab", "\n".join(self.draw()))

    def test_g_gathers_into_the_origin_tab_and_refreshes(self):
        self.make(self.tabs())
        self.page.focus = False
        self.send(key("g"))
        self.assertEqual(self.core.actions(), [("gather", 0)])
        self.assertEqual(self.ui.message, "gathered 3 agents")
        self.assertFalse(self.ui.message_error)
        self.assertTrue(self.page._force)

    def test_alt_g_gathers_while_typing(self):
        self.make(self.tabs())
        self.send(key("Tab"))
        self.assertTrue(self.page.focus)
        self.send(key("g", "ALT"))
        self.assertEqual(self.core.actions(), [("gather", 0)])

    def test_g_without_agents_elsewhere_does_nothing(self):
        self.make()
        self.send(key("g"))
        self.assertEqual(self.core.actions(), [])
        self.assertEqual(self.ui.message, "no agents in other tabs")

    def test_g_reports_failures(self):
        self.make(self.tabs())
        self.core.gather_result = (False, "moved 1 of 3 agents")
        self.page.focus = False
        self.send(key("g"))
        self.assertEqual(self.ui.message, "moved 1 of 3 agents")
        self.assertTrue(self.ui.message_error)

    def test_g_needs_a_known_origin(self):
        self.make(self.tabs())
        self.page.origin = None
        self.core.origin_result = None
        self.page.focus = False
        self.send(key("g"))
        self.assertEqual(self.core.actions(), [])
        self.assertTrue(self.ui.message_error)

    def test_core_without_an_origin_marks_nothing(self):
        class Old(FakeCore):
            origin = None
        page = HV.HivePage(FakeModel(), core=Old(self.tabs()), now=lambda: NOW, background=lambda f: f())
        page.refresh()
        self.assertIsNone(page.origin)
        self.assertFalse(any(page.elsewhere(a) for a in page.agents))

    def test_spawn_runs_in_the_background_and_reports(self):
        jobs = []
        self.core = FakeCore(seven())
        self.page = HV.HivePage(FakeModel(), core=self.core, now=lambda: NOW, background=jobs.append)
        self.ui = FakeUI(self.page)
        self.size = (100, 30)
        self.page.on_show(self.ui)
        self.send(key("n"))
        self.assertEqual(self.core.actions(), [])
        self.assertEqual(len(jobs), 1)
        jobs[0]()
        self.assertEqual(self.core.actions(), [("spawn", 10)])
        self.assertEqual(self.page.want, 99)


class Mouse(Base):
    def test_click_selects_and_opens_then_toggles(self):
        self.make()
        self.click_cell(11)
        self.assertEqual((self.page.sel, self.page.open), (11, True))
        self.click_cell(11)
        self.assertFalse(self.page.open)

    def test_click_every_cell_hits_that_cell(self):
        for size in ((100, 30), (160, 50), (80, 24)):
            self.make(size=size)
            for pane in range(10, 17):
                self.page.close_popover()
                self.draw()
                self.click_cell(pane)
                self.assertEqual(self.page.sel, pane, (size, pane))

    def test_click_border_row_of_a_hex_hits_it(self):
        self.make()
        c = self.cell(11)
        for i, (a, b) in enumerate(c["region"]):
            for col in (a, b - 1):
                self.page.close_popover()
                self.draw()
                self.ui.handle(click(c["x"] + col, c["y"] + i))
                self.assertEqual(self.page.sel, 11, (i, col))

    def test_click_on_add_cell_spawns(self):
        self.make()
        self.click_cell(HV.ADD)
        self.assertEqual(self.core.actions(), [("spawn", 10)])

    def find_button(self, label):
        for x, y, w, h, fn in self.ui.cv.hits:
            if "".join(c[0] for c in self.ui.cv.cells[y][x:x + w]) == f" {label} ":
                return x, y
        self.fail(f"no button {label}")

    def test_click_jump_button(self):
        self.make()
        self.page.select(12)
        self.draw()
        self.ui.handle(click(*self.find_button("Jump")))
        self.assertIn(("jump", 12), self.core.calls)
        self.assertFalse(self.ui.running)

    def test_click_ack_button_only_for_done(self):
        self.make()
        self.page.select(12)
        self.draw()
        self.ui.handle(click(*self.find_button("Ack")))
        self.assertIn(("ack", 12), self.core.calls)
        self.page.select(10)
        self.draw()
        x, y = self.find_button("Ack") if False else (0, 0)
        self.assertNotIn(" Ack ", "".join(c[0] for c in self.ui.cv.cells[0]))
        # for a working agent the button is drawn but has no click region
        regions = ["".join(c[0] for c in self.ui.cv.cells[yy][xx:xx + ww]) for xx, yy, ww, hh, fn in self.ui.cv.hits]
        self.assertNotIn(" Ack ", regions)

    def test_click_delete_button_confirms_for_working(self):
        self.make()
        self.page.select(10)
        self.draw()
        self.ui.handle(click(*self.find_button("Delete")))
        self.assertIsInstance(self.ui.modal, U.ConfirmModal)

    def test_click_plan_choice_button(self):
        self.make()
        self.page.select(13)
        self.draw()
        self.ui.handle(click(*self.find_button("3 Keep")))
        self.assertEqual(self.core.actions(), [("answer", 13, "keep", None)])

    def test_click_popover_close_and_background(self):
        self.make()
        self.page.select(12)
        self.draw()
        x, y, w, h = self.page.pop_rect
        self.ui.handle(click(x + w - 3, y))
        self.assertFalse(self.page.open)
        self.page.select(12)
        self.draw()
        self.ui.handle(click(self.page.area[0] + 1, self.page.area[1] + 1))
        self.assertFalse(self.page.open)

    def test_click_inside_popover_does_not_close_it(self):
        self.make()
        self.page.select(12)
        self.draw()
        x, y, w, h = self.page.pop_rect
        self.ui.handle(click(x + 5, y + 6))
        self.assertTrue(self.page.open)

    def test_click_prompt_line_focuses_it(self):
        self.make()
        self.page.select(12)
        self.page.focus = False
        self.draw()
        x, y, w, h = self.page.pop_rect
        self.ui.handle(click(x + 5, y + h - 4))  # the prompt line: above the buttons and the padding row
        self.assertTrue(self.page.focus)


class Tick(Base):
    def test_tick_refreshes_every_second_and_reports_changes_only(self):
        t = [NOW]
        self.core = FakeCore(seven())
        self.page = HV.HivePage(FakeModel(), core=self.core, now=lambda: t[0], background=lambda f: f())
        self.ui = FakeUI(self.page)
        self.assertTrue(self.page.tick(self.ui))
        n = len(self.core.calls)
        self.assertFalse(self.page.tick(self.ui))
        t[0] += 0.5
        self.assertFalse(self.page.tick(self.ui))
        self.core.agents[0].state = "done"
        self.assertFalse(self.page.tick(self.ui), "not yet a second")
        t[0] += 0.6
        self.assertTrue(self.page.tick(self.ui))
        t[0] += 1.1
        self.assertFalse(self.page.tick(self.ui))

    def test_peek_runs_about_once_a_second_for_the_selected_agent_only(self):
        t = [NOW]
        self.core = FakeCore(seven())
        self.page = HV.HivePage(FakeModel(), core=self.core, now=lambda: t[0], background=lambda f: f())
        self.ui = FakeUI(self.page)
        self.page.tick(self.ui)
        self.assertEqual([c for c in self.core.calls if c[0] == "peek"], [])
        self.page.select(11)
        self.assertEqual([c for c in self.core.calls if c[0] == "peek"], [("peek", 11)])
        t[0] += 0.4
        self.page.tick(self.ui)
        self.assertEqual(len([c for c in self.core.calls if c[0] == "peek"]), 1)
        self.core.screen[11] = ["new output"]
        t[0] += 0.7
        self.assertTrue(self.page.tick(self.ui))
        self.assertEqual(self.page.peeked, (11, ["new output"]))

    def test_popover_age_ticks_each_second(self):
        t = [NOW]
        self.core = FakeCore(seven())
        self.page = HV.HivePage(FakeModel(), core=self.core, now=lambda: t[0], background=lambda f: f())
        self.ui = FakeUI(self.page)
        self.page.tick(self.ui)
        self.page.select(10)
        self.page.tick(self.ui)
        t[0] += 0.3
        self.assertFalse(self.page.tick(self.ui))
        t[0] += 0.8
        self.assertTrue(self.page.tick(self.ui))

    def test_vanished_selection_closes_the_popover(self):
        self.make()
        self.page.select(12)
        self.core.agents = [a for a in self.core.agents if a.pane != 12]
        self.assertTrue(self.page.refresh())
        self.assertFalse(self.page.open)
        self.assertIsNone(self.page.sel)

    def test_core_failure_is_shown_not_raised(self):
        self.make()

        def boom():
            raise RuntimeError("mux gone")

        self.core.collect = boom
        self.assertTrue(self.page.refresh())
        self.assertIn("mux gone", "\n".join(self.draw()))
        self.assertEqual(len(self.page.agents), 7, "keeps the last known agents")

    def test_helpers(self):
        self.assertEqual([HV.human_age(s) for s in (5, 75, 7260, 200000)], ["5s", "1m", "2h01m", "2d"])
        self.assertEqual(HV.folder("/a/b/c/"), "c")
        self.assertEqual(HV.folder("/"), "/")
        self.assertEqual(HV.clip("abcdef", 4), "abc…")
        self.assertEqual(HV.flow([("aaaa", 1), ("bbbb", 2), ("cc", 3)], 14), [[("aaaa", 1), ("bbbb", 2)], [("cc", 3)]])


class RealCore(unittest.TestCase):
    """The page against the real hivegrid_agents with a fake `run` (no mux, no omp)."""

    def setUp(self):
        try:
            import hivegrid_agents as A
        except ImportError:
            self.skipTest("hivegrid_agents not present")
        self.A = A
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self.tmp.name
        d = A.state_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "7.json"), "w") as f:
            json.dump({"v": 1, "pane": 7, "pid": os.getpid(), "sessionId": "s", "cwd": "/home/u/proj", "title": "T",
                       "profile": "alpha", "state": "busy", "reason": "2 background jobs", "since": NOW - 5,
                       "updated": NOW, "pending": None, "error": None}, f)
        panes = [{"pane_id": 7, "tab_id": 1, "window_id": 0, "title": "π ⠋ T", "cwd": "file://h/home/u/proj",
                  "is_active": True, "tty_name": "/dev/pts/7"},
                 {"pane_id": 8, "tab_id": 1, "window_id": 0, "title": "bash", "cwd": "file://h/home/u",
                  "is_active": False, "tty_name": "/dev/pts/8"}]
        self.calls = []

        def run(*args):
            self.calls.append(args)
            if args[0] == "list":
                return json.dumps(panes)
            if args[0] == "get-text":
                return "one\n\ntwo\n"
            return ""

        class Core:
            def __getattr__(s, name):
                fn = getattr(A, name)
                kw = {"collect": {"run": run, "now": lambda: NOW, "fg": {}}}.get(name, {"run": run} if name in (
                    "peek", "send_prompt", "jump", "spawn_agent", "delete_agent", "origin", "gather") else {})
                return lambda *a, **k: fn(*a, **{**kw, **k})

        self.page = HV.HivePage(FakeModel(), core=Core(), now=lambda: NOW, background=lambda f: f())
        self.ui = FakeUI(self.page)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self.old
        self.tmp.cleanup()

    def test_collect_peek_send_through_the_real_core(self):
        self.page.on_show(self.ui)
        self.assertEqual([(a.pane, a.state) for a in self.page.agents], [(7, "busy")])
        self.page.select(7)
        self.ui.size = (100, 30)
        self.ui.draw()
        body = "\n".join(text_of(self.ui))
        self.assertIn("2 background jobs", body)
        self.assertIn("two", body)
        self.ui.handle(key("h"))
        self.ui.handle(key("i"))
        self.ui.handle(key("Enter"))
        sends = [c for c in self.calls if c[0] == "send-text"]
        self.assertEqual(sends[0], ("send-text", "--pane-id", "7", "--", "hi"))
        self.assertEqual(sends[1], ("send-text", "--pane-id", "7", "--no-paste", "\r"))


@unittest.skipUnless(HV.Image is not None, "PIL not installed")
class ImageCells(Base):
    """The anti-aliased image cells (HivePage images=True) with a fixed cell size."""

    CW, CH = 8, 17

    def make(self, agents=None, size=(160, 50)):
        self.core = FakeCore(seven() if agents is None else agents)
        self.page = HV.HivePage(FakeModel(), core=self.core, now=lambda: NOW, background=lambda f: f(),
                                images=True, cell_px=(self.CW, self.CH))
        self.ui = FakeUI(self.page)
        self.size = size
        self.page.on_show(self.ui)
        self.draw()
        return self.page

    def screen(self):
        """The frame's images composited into one RGBA picture of the whole canvas."""
        from PIL import Image
        cv = self.ui.cv
        img = Image.new("RGBA", (cv.w * self.CW, cv.h * self.CH), (0, 0, 0, 0))
        for x, y, w, h, png in cv.images:
            part = Image.open(io.BytesIO(png)).convert("RGBA")
            self.assertEqual(part.size, (w * self.CW, h * self.CH))  # 1:1, nothing stretched
            img.alpha_composite(part, (x * self.CW, y * self.CH))
        return img

    def text_cells(self):
        """(x, y, bg) of every non-blank character inside a cell's box."""
        out = []
        for c in self.page.cells:
            for yy in range(c["y"], c["y"] + c["h"]):
                for xx in range(c["x"], c["x"] + c["w"]):
                    ch, fg, bg, _ = self.ui.cv.cells[yy][xx]
                    if ch.strip():
                        out.append((xx, yy, bg))
        return out

    def test_cells_are_images_with_text_in_holes_on_the_same_fill(self):
        self.make()
        self.assertEqual(self.page.art_form(), ("hex", self.page.ry))
        self.assertTrue(self.ui.cv.images)
        img = self.screen()
        cells = self.text_cells()
        self.assertTrue(cells)
        for x, y, bg in cells:
            # the image is transparent over the text...
            self.assertEqual(img.getpixel((x * self.CW + self.CW // 2, y * self.CH + self.CH // 2))[3], 0)
            # ...and the fill right beside the text has the text's background colour, opaque
            left = img.getpixel((x * self.CW - 1, y * self.CH + self.CH // 2))
            if left[3]:
                self.assertEqual(left[:3] + (255,), U.rgb(bg) + (255,), (x, y))
        # the glyph, number and folder are still text
        body = "\n".join(text_of(self.ui))
        for st in ("↻", "◔", "✓", "?"):
            self.assertIn(st, body)
        self.assertIn("proj3", body)

    def test_selection_ring_goes_round_the_cell_continuously(self):
        self.make()
        self.page.select(12, open=False)
        self.draw()
        img = self.screen()
        c = self.cell(12)
        poly = X.hex_polygon(self.page.ry, self.CW, self.CH)
        gap, rw, sep, _ = HV.art_metrics(self.CW, self.CH)
        ring = X.inset(poly, gap - sep - rw / 2)  # the middle of the ring
        bright = U.rgb(self.ui.pal["bright"])
        for i in range(6):  # sample along every edge of the ring
            (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % 6]
            for t in (0.25, 0.5, 0.75):
                px = c["x"] * self.CW + x0 + (x1 - x0) * t
                py = c["y"] * self.CH + y0 + (y1 - y0) * t
                r, g, b, a = img.getpixel((int(px), int(py)))
                self.assertGreater(a, 200, (i, t))
                self.assertLess(max(abs(r - bright[0]), abs(g - bright[1]), abs(b - bright[2])), 60, (i, t))

    def test_frames_are_cached_and_a_hover_re_encodes_only_its_strips(self):
        self.make()
        art = self.page.art
        first = art.encoded
        self.draw()
        self.assertEqual(art.encoded, first, "an unchanged frame encodes nothing")
        strips = len(self.ui.cv.images)
        c = self.cell(11)
        self.ui.pointer = (int(c["cx"]), int(c["cy"]))
        self.assertTrue(self.page.tick(self.ui), "the hover asks for a frame")
        self.draw()
        self.assertEqual(self.page.hover, 11)
        touched = [s for s in self.ui.cv.images if s[1] < c["y"] + c["h"] + 1 and s[1] + s[3] > c["y"] - 1]
        self.assertLessEqual(art.encoded - first, len(touched))
        self.assertLess(len(touched), strips)
        self.assertFalse(self.page.tick(self.ui), "a still pointer asks for nothing")
        # leaving and coming back reuses the cached strips
        self.ui.pointer = None
        self.page.tick(self.ui)
        self.draw()
        n = art.encoded
        self.ui.pointer = (int(c["cx"]), int(c["cy"]))
        self.draw()
        self.assertEqual(art.encoded, n)

    def test_hover_lightens_the_fill_and_the_text_background_alike(self):
        self.make()
        c = self.cell(13)
        mine = lambda: {bg for x, y, bg in self.text_cells() if (self.page.cell_at(x, y) or {}).get("id") == 13}
        plain = mine()
        self.ui.pointer = (int(c["cx"]), int(c["cy"]))
        self.draw()
        self.assertEqual(len(plain), 1)
        self.assertEqual(mine(), {U.mix(next(iter(plain)), self.ui.pal["bright"], HV.HOVER_LIGHT)})

    def test_clicks_reach_the_cell_under_the_pointer_and_gaps_close(self):
        self.make()
        for c in list(self.page.cells):
            if c["kind"] != "agent":
                continue
            self.ui.handle(click(int(c["cx"]), int(c["cy"])))
            self.assertEqual(self.page.sel, c["id"])
            self.assertTrue(self.page.open)
            self.draw()
            self.ui.handle(key("Escape"))
            self.draw()
        # a click in the corner of a box that belongs to no cell closes the popover
        self.page.select(10)
        self.draw()
        corner = next((x, y) for c in self.page.cells for y in range(c["y"], c["y"] + c["h"])
                      for x in range(c["x"], c["x"] + c["w"]) if self.page.cell_at(x, y) is None)
        self.ui.handle(click(*corner))
        self.assertFalse(self.page.open)

    def test_popover_is_cut_out_of_the_image(self):
        self.make()
        self.page.select(10)
        self.draw()
        x, y, w, h = self.page.pop_rect
        img = self.screen()
        for yy in range(y, y + h):
            for xx in (x, x + w // 2, x + w - 1):
                self.assertEqual(img.getpixel((xx * self.CW + 3, yy * self.CH + 3))[3], 0)

    def test_big_window_leaves_room_for_the_popover_beside_the_cells(self):
        for size in ((160, 50), (233, 55), (318, 80)):
            self.make(size=size)
            self.page.select(10)
            self.draw()
            x, y, w, h = self.page.pop_rect
            for c in self.page.cells:
                self.assertTrue(x >= c["x"] + c["w"] or x + w <= c["x"], (size, c["id"]))

    def test_smallest_hex_keeps_room_for_glyph_and_number(self):
        """'↻ 12' needs 4 cells: the smallest hex size gives two text rows that wide."""
        for cw, ch in ((8, 17), (10, 21), (7, 15)):
            spans = [s for s in X.text_spans(X.MIN_RY, cw, ch, HV.art_metrics(cw, ch)[3]) if s]
            self.assertGreaterEqual(len(spans), 2, (cw, ch))
            self.assertGreaterEqual(min(b - a for a, b in spans), 4, (cw, ch))

    def test_tiles_use_images_only_when_they_have_gap_rows(self):
        self.make()
        self.send(key("t"))
        self.assertEqual(self.page.art_form()[0], "tile")
        self.assertTrue(self.ui.cv.images)
        many = [Agent(pane=100 + i, cwd=f"/w/p{i}") for i in range(60)]
        self.make(agents=many, size=(60, 20))
        self.send(key("t"))
        self.assertLessEqual(self.page.tile[1], 2)
        self.assertIsNone(self.page.art_form())
        self.assertEqual(self.ui.cv.images, [])

    def test_no_images_means_block_cells(self):
        self.core = FakeCore(seven())
        page = HV.HivePage(FakeModel(), core=self.core, now=lambda: NOW, background=lambda f: f(), images=False)
        self.ui = FakeUI(page)
        self.ui.size = (160, 50)
        page.on_show(self.ui)
        self.ui.draw()
        self.assertIsNone(page.art_form())
        self.assertEqual(self.ui.cv.images, [])


class WebChip(Base):
    """`+ web` (and `w`): a URL prompt, then a web pane in the tab the hive was opened over."""

    def type_url(self, text):
        for ch in text:
            self.send(key("Space") if ch == " " else key(ch))
        self.send(key("Enter"))

    def test_w_asks_for_a_location_and_opens_it(self):
        self.make()
        self.page.focus = False
        self.send(key("w"))
        self.assertIsInstance(self.ui.modal, U.PromptModal)
        self.type_url("example.com")
        self.assertIsNone(self.ui.modal)
        self.assertEqual(self.core.actions(), [("web", "https://example.com", None)])
        self.assertFalse(self.ui.message_error)

    def test_settings_give_the_profile_and_the_search_engine(self):
        self.make()
        self.page.model.web = {"links": "pane", "profile": "work", "search": "https://duck.example/?q=%s"}
        self.page.focus = False
        self.send(key("w"))
        self.type_url("rust ownership")
        self.assertEqual(self.core.actions(), [("web", "https://duck.example/?q=rust%20ownership", "work")])

    def test_a_location_that_is_no_url_stays_in_the_prompt_with_the_reason(self):
        self.make()
        self.page.focus = False
        self.send(key("w"))
        self.type_url("javascript:alert(1)")
        self.assertIsInstance(self.ui.modal, U.PromptModal, "the prompt stays open")
        self.assertIn("javascript", self.ui.modal.error)
        self.assertEqual(self.core.actions(), [])

    def test_a_failed_open_reports(self):
        self.make()
        self.core.web_result = None
        self.page.focus = False
        self.send(key("w"))
        self.type_url("example.com")
        self.assertTrue(self.ui.message_error)
        self.assertIn("web pane", self.ui.message)

    def test_the_chip_is_in_the_legend_and_clicks_open_the_prompt(self):
        self.make(size=(100, 30))
        legend = self.draw()[2]
        self.assertIn("+ web", legend)
        self.assertIn("7 agents", legend, "the counts stay")
        x = legend.index("+ web") + 2
        self.ui.handle(click(x, 2))
        self.assertIsInstance(self.ui.modal, U.PromptModal)

    def test_a_wezterm_without_the_web_pane_has_no_chip_and_w_says_why(self):
        self.make(size=(100, 30))
        self.core.web_supported = False
        self.page._web = None
        self.assertNotIn("+ web", self.draw()[2])
        self.assertIn("7 agents", self.draw()[2])
        self.page.focus = False
        self.send(key("w"))
        self.assertIsNone(self.ui.modal)
        self.assertTrue(self.ui.message_error)
        self.assertEqual(self.core.actions(), [])

    def test_an_error_in_the_prompt_callback_keeps_the_panel_alive(self):
        self.make()
        self.page.focus = False
        self.core.normalize_url = lambda text, search="": 1 / 0
        self.send(key("w"))
        self.type_url("example.com")
        self.assertIsInstance(self.ui.modal, U.PromptModal)
        self.assertTrue(self.ui.modal.error)

    def test_the_chip_gives_way_when_the_legend_is_full(self):
        self.make(size=(60, 24))
        legend = self.draw()[2]
        self.assertIn("+ web", legend, "the compact counts leave room for it")
        self.assertIn("↻2", legend)
        self.make(size=(34, 24))
        legend = self.draw()[2]
        self.assertNotIn("+ web", legend)
        self.assertIn("7 agents", legend, "the chip never pushes the counts out")


if __name__ == "__main__":
    unittest.main()
