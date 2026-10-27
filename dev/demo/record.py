#!/usr/bin/env python3
"""Records the demo video of Hivegrid (the one in the README) from the QA sandbox.

It prepares and starts QA slot SLOT (dev/demo/prepare.sh: fixture repositories, fictional accounts and
usage, no real names or chats), drives the real window with synthetic keys and clicks, records the
Xvfb display with ffmpeg and burns the captions in below the picture. Real omp sessions run in the
panes, so it needs a logged-in omp profile (prepare.sh: DEMO_OMP_SRC) and uses a few trivial prompts of
that account's quota. Chrome is needed for the web pane scene and for the phone remote scene, which
starts `hivegrid-remote serve` in the sandbox (loopback, port 18720) and shows its page in a phone-shaped
Chrome window on the same display, beside the Hivegrid window (dev/demo/hgdemo.py: Phone).

  dev/demo/record.py [--slot 20] [--wezterm-dir DIR] [--out assets/demo/hivegrid-demo.mp4]

--wezterm-dir is the Hivegrid WezTerm build (default ~/.local/opt/hivegrid-wezterm). Output: the mp4,
and next to it a short animated preview (hivegrid-demo.gif) for the README.
"""
import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from hgdemo import Demo, Phone  # noqa: E402

BAR = "0x070d19"  # the strip below the picture that carries the captions
PICTURE_H, VIDEO_H = 1080, 1144
PILL = {"work": (760, 23), "team": (1000, 23), "home": (1250, 23)}  # header chips; the context pill left of them changes width
DOCK_X, DOCK_TOP = 1670, 63  # the dock: a column of its pane and the pixel row of its first line
EQUALIZE = (1886, 72)
GEAR = (22, 1009)
BAR_EXPLORER, BAR_GIT = (22, 106), (22, 157)
PHONE_X, PHONE_PORT, PHONE_SCALE = 1412, 18720, 1.25  # the phone window: left edge on the 1920 px display, loopback port, device scale
SHOTS = os.path.join(REPO, "assets", "screenshots")  # stills for the README, taken during the recording
TEAM_ROW = (1500, 156)  # the team row of the usage panel's account table


def dock_row(d, i):
    """Pixel position of the dock's row for dock number i. The rows have different heights (a working
    agent has a task line), so it is read from the dock's own text: 17 px per line from the pane top."""
    for p in d.panes():
        if p["is_web"] or p["title"].startswith("π") or p["size"]["cols"] < 30:
            continue
        lines = d.text(p["pane_id"]).splitlines()
        if not lines or "Overview" not in lines[0]:
            continue
        for n, line in enumerate(lines):
            m = re.match(r"^\s*▎?\s*\S\s+(\d+)\s+\S", line)
            if m and int(m.group(1)) == i:
                return DOCK_X, DOCK_TOP + 17 * n + 8
    raise RuntimeError(f"no dock row {i}")


def still(d, name):
    os.makedirs(SHOTS, exist_ok=True)
    d.shot(os.path.join(SHOTS, f"hivegrid-{name}.png"))


def agent_panes(d):
    return [p["pane_id"] for p in d.panes() if p["title"].startswith("π")]


def scene_intro(d):
    d.caption("Hivegrid: many AI coding agents in one WezTerm window", 4.5)
    d.move(900, 480, 0.8)
    time.sleep(1.2)
    d.type("cd Projects\n")
    d.caption("The header: the project, each account's Claude usage, the machine's load", 5)
    d.move(PILL["work"][0], 40, 1.0)
    d.move(1380, 40, 1.6)
    time.sleep(1.0)
    d.move(900, 480, 0.8)
    d.caption("omp-grid starts one agent per pane, each in its own directory", 5)
    d.type("omp-grid 2 2 --dirs hivegrid,omp-notes,site,scripts\n")
    for _ in range(60):
        ids = agent_panes(d)
        if len(ids) >= 4 and all("Sonnet" in d.text(i) for i in ids):
            break
        time.sleep(1)
    time.sleep(1.5)


def scene_tasks(d):
    d.caption("The overview dock lists every pane: state, task, git branch, tokens and cost", 5)
    d.move(*dock_row(d, 2), 0.8)
    time.sleep(1.5)
    prompts = [
        "How many lines of Python are in bin/? Reply with just the number.",
        'Run `sleep 40` in the shell, then reply "done".',
        "Summarise this repo in one short sentence.",
        "What does run.sh do? One short sentence.",
    ]
    d.caption("Give every agent a task", 3.5)
    for i, text in enumerate(prompts, 1):
        d.click(*dock_row(d, i))
        time.sleep(0.5)
        d.type(text, delay=0.03)
        time.sleep(0.3)
    d.caption("Four agents work at once; the dock shows who works, waits or is done", 7)
    for i in range(1, 5):
        d.click(*dock_row(d, i), dur=0.35, settle=0.1)
        d.key("Return", gap=0.2)
    d.move(*dock_row(d, 2), 0.6)
    time.sleep(8)
    still(d, "agents")


def scene_grid(d):
    d.caption("Split a pane: it stays where the split puts it; nothing re-tiles by itself", 5)
    d.click(*dock_row(d, 4))
    time.sleep(0.6)
    d.key("shift+Right", gap=2.5)
    time.sleep(1.2)
    d.caption("Press Ctrl+Shift+A to arrange the grid on demand", 4)
    d.key("ctrl+shift+a", gap=2.5)
    time.sleep(1.2)
    d.caption("Drag a divider out of shape, then press ▦: every divider is even again", 6)
    d.drag(500, 549, 500, 790)
    time.sleep(1.8)
    d.click(*EQUALIZE)
    time.sleep(3.0)


def scene_switch(d):
    d.caption("Switch account mid-session: the same chat continues under another profile", 6.5)
    d.click(*dock_row(d, 3))
    time.sleep(1.0)
    d.click(*PILL["team"])
    for _ in range(40):
        if any("π-team" in d.text(i) for i in agent_panes(d)):
            break
        time.sleep(0.5)
    time.sleep(4.0)


def scene_panels(d):
    # a short burst of load so that the monitor has something to show
    shell = next(p["pane_id"] for p in d.panes() if p["title"] in ("bash", "Projects"))
    d.cli("send-text", "--pane-id", str(shell), "--no-paste",
          "timeout 14 sh -c 'for i in 1 2 3 4 5 6 7 8; do while :; do :; done & done; wait'\n")
    d.caption("Usage history of every account: 6 hours up to 30 days", 5)
    d.click(*PILL["work"], button=3)
    time.sleep(2.5)
    for x in (188, 232, 276):  # 24h, 7d, 30d
        d.click(x, 106)
        time.sleep(1.8)
        if x == 232:
            still(d, "usage")
    d.click(*TEAM_ROW)
    time.sleep(1.5)
    d.caption("Compute history: CPU, RAM, disk and GPU over minutes to days", 5)
    d.click(362, 72)
    time.sleep(2.5)
    d.click(307, 106)  # 15m
    time.sleep(3.5)
    d.caption("Every pane of the window, and the agent hive as hexes or tiles", 6)
    d.click(288, 72)
    time.sleep(2.5)
    d.click(431, 72)
    time.sleep(3.0)
    d.key("t", gap=2.5)  # hex cells or tiles
    d.key("t", gap=1.5)
    d.key("q", gap=1.5)


def scene_explorer(d):
    d.caption("File explorer: browse, preview, open a terminal or an editor right there", 6)
    d.click(*dock_row(d, 5))
    time.sleep(0.8)
    d.type("cd .. && ls\n")
    time.sleep(0.8)
    d.click(*BAR_EXPLORER)
    time.sleep(3.0)
    d.click(150, 300)
    d.key("j", "j", gap=0.7)
    d.key("l", gap=1.6)
    d.key("j", gap=1.0)
    d.key("h", gap=1.2)
    d.key("k", gap=0.8)
    d.caption("Double-click a folder to open a terminal there", 4)
    d.click(130, 123, count=2)
    time.sleep(3.0)


def scene_git(d):
    d.caption("Git view: every repository with branch, changes, ahead and behind, and GitHub CI", 6.5)
    d.click(*dock_row(d, 5))
    time.sleep(0.8)
    d.click(*BAR_GIT)
    time.sleep(6.5)
    still(d, "git")
    d.move(150, 160, 0.8)
    time.sleep(1.0)
    d.move(150, 310, 1.0)
    time.sleep(1.0)
    d.caption("Open a repository in lazygit, in Hivegrid colours: pull what is new", 5)
    d.click(130, 210)
    time.sleep(4.0)
    d.key("p", gap=3.0)
    d.key("3", gap=0.3)
    d.key("4", gap=2.5)
    d.key("q", gap=1.5)


def scene_web(d):
    d.caption("Web pane: a real browser page as a pane of the grid", 5)
    d.click(*dock_row(d, 5))
    time.sleep(0.8)
    d.leader("Tab", gap=1.5)  # hide the sidebar: the page gets the room
    d.leader("w", "n", gap=1.5)
    time.sleep(1.0)
    d.type("github.com/can1357/oh-my-pi", delay=0.06)
    time.sleep(0.6)
    d.key("Return", gap=1.0)
    time.sleep(5.5)
    d.caption("Zoom it like any pane, scroll, click; the dock lists it too", 5)
    d.key("alt+z", gap=3.0)
    still(d, "web")
    d.scroll(900, 600, "down", 4)
    time.sleep(1.5)
    d.scroll(900, 600, "up", 4)
    time.sleep(1.2)
    d.key("alt+z", gap=2.5)


def scene_settings(d):
    d.caption("Settings: five colour themes with live preview, key bindings, accounts, web", 5)
    d.phone = Phone(d, PHONE_PORT)  # the phone scene's server starts here, out of the picture
    d.phone.start_server()
    d.click(*GEAR)
    time.sleep(3.0)
    still(d, "settings")
    d.click(640, 411)  # Forest
    time.sleep(1.2)
    d.key("s", gap=2.5)
    d.click(640, 343)  # Slate
    time.sleep(1.0)
    d.key("s", gap=2.0)
    d.click(640, 139)  # back to Graphite, the default
    time.sleep(1.0)
    d.key("s", gap=1.5)
    for x, y in ((373, 156), (386, 173), (363, 190)):  # Keys, Accounts, Web
        d.click(x, y)
        time.sleep(2.0)
    d.key("q", gap=1.5)


def phone_tap(d, x, y, **kw):
    """Click at (x, y) of the phone window's own pixels (the window sits at PHONE_X, 0)."""
    d.click(PHONE_X - 12 + x, y, **kw)


def phone_list_row(ph, label):
    """Page y of the row of the agent with dock number `label` (the list order follows the agents' states)."""
    shown = sorted(ph.agents(), key=lambda a: (a["rank"], a["tab"] or 0, a["num"] or 0))  # as the page sorts them
    order = [a["label"] for a in shown]
    return 180 + 72 * order.index(label)


def scene_phone(d):
    """The phone remote: its server and a phone-shaped Chrome window beside a narrowed Hivegrid window."""
    ph = getattr(d, "phone", None) or Phone(d, PHONE_PORT)
    if not ph.server:
        ph.start_server()
    d.phone = ph
    d.caption("Phone remote: every agent on your phone, in Hivegrid's own look", 6)
    d.hgq("resize", str(d.slot), str(PHONE_X - 12), "1080")  # make room: the Hivegrid window narrows
    d.sh("xsetroot", "-solid", "#070d19")
    time.sleep(0.8)
    ph.open(PHONE_X, 40, 390, 800, PHONE_SCALE)
    ph.focus()
    try:
        d.move(PHONE_X + 250, 600, 0.8)
        time.sleep(2.0)
        row = phone_list_row(ph, "1")
        d.caption("Open an agent: its chat, live from the omp session file", 4.5)
        phone_tap(d, 250, row)
        time.sleep(4.0)
        d.caption("Send a prompt from the phone: the answer shows up in the chat and in the pane", 9)
        phone_tap(d, 240, 1002)
        time.sleep(0.3)
        d.type("What is 2+2? Reply with just the number.", delay=0.035)
        time.sleep(0.6)
        phone_tap(d, 450, 1000)
        time.sleep(3.0)
        for _ in range(40):  # until the agent has answered
            if ph.agents_by_label().get("1", {}).get("state") not in ("working", "busy"):
                break
            time.sleep(1)
        time.sleep(2.5)
        still(d, "remote")
        d.caption("Switch the model or the thinking level of a running agent", 8)
        phone_tap(d, 50, 1000)  # the actions sheet
        time.sleep(1.5)
        phone_tap(d, 130, 558)  # change model
        time.sleep(1.8)
        phone_tap(d, 160, 662)  # Claude Haiku 5.5
        time.sleep(2.0)
        phone_tap(d, 50, 1000)
        time.sleep(1.2)
        phone_tap(d, 192, 660)  # thinking: low
        time.sleep(2.0)
        d.caption("Menu: the quota of every account, the machine's load", 7)
        phone_tap(d, 42, 66)  # back to the list
        time.sleep(1.0)
        phone_tap(d, 468, 69)  # menu
        time.sleep(3.5)
        phone_tap(d, 320, 126)  # system
        time.sleep(3.0)
        d.caption("Start a new omp session on any account, in any directory", 6)
        phone_tap(d, 192, 126)
        time.sleep(1.2)
        phone_tap(d, 226, 235)  # home
        time.sleep(0.6)
        phone_tap(d, 100, 460)  # site
        time.sleep(0.8)
        phone_tap(d, 256, 742)  # start
        time.sleep(4.0)
    finally:
        ph.close()
        d.phone = None
        d.hgq("resize", str(d.slot), "1920", "1080")
        d.sh("xsetroot", "-solid", "black")
        d.focus()
        time.sleep(2.0)


def scene_outro(d):
    d.click(*dock_row(d, 1))
    d.caption("Hivegrid is open source and in alpha: github.com/leongreiner/hivegrid", 6)
    time.sleep(6.5)


SCENES = [scene_intro, scene_tasks, scene_grid, scene_switch, scene_panels, scene_explorer, scene_git,
          scene_web, scene_settings, scene_phone, scene_outro]


def run(*cmd, env=None, check=True):
    return subprocess.run(cmd, env=env, check=check, cwd=REPO)


# The preview gif of the README: (offset, length) in seconds from the start of each scene, played faster.
HIGHLIGHTS = {
    "scene_intro": [(6, 6)], "scene_tasks": [(10, 14)], "scene_grid": [(0.5, 11)], "scene_switch": [(0.7, 9)],
    "scene_panels": [(3, 5), (18, 4), (26, 4)], "scene_explorer": [(1.5, 9)], "scene_git": [(3, 7), (15, 6)],
    "scene_web": [(1.5, 15)], "scene_settings": [(2, 8)],
    "scene_phone": [(2, 6), (12, 11), (30, 5), (38, 6)],
}
GIF_SPEED = 1.7


def encode(raw, ass, out, starts):
    """Burn the captions into a strip below the picture; write the mp4 and the preview gif."""
    vf = f"pad=1920:{VIDEO_H}:0:0:color={BAR},ass={ass}"
    run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", raw, "-vf", vf, "-c:v", "libx264",
        "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", out)
    make_gif(out, starts)


def make_gif(out, starts):
    """The README's preview: the HIGHLIGHTS of the finished mp4, sped up, 10 fps, a small palette."""
    cuts = [(starts[name] + off, starts[name] + off + length)
            for name, parts in HIGHLIGHTS.items() if name in starts for off, length in parts]
    fc = "".join(f"[0:v]trim=start={a:.2f}:end={b:.2f},setpts=(PTS-STARTPTS)/{GIF_SPEED}[v{i}];"
                 for i, (a, b) in enumerate(cuts))
    fc += "".join(f"[v{i}]" for i in range(len(cuts)))
    fc += (f"concat=n={len(cuts)}:v=1:a=0,fps=10,scale=1000:-1:flags=lanczos,split[a][b];"
           "[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5")
    run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", out, "-filter_complex", fc,
        os.path.splitext(out)[0] + ".gif")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", type=int, default=20)
    ap.add_argument("--wezterm-dir", default=os.path.expanduser("~/.local/opt/hivegrid-wezterm"))
    ap.add_argument("--out", default=os.path.join(REPO, "assets", "demo", "hivegrid-demo.mp4"))
    ap.add_argument("--only", help="comma separated scene names (debugging: scenes build on each other)")
    args = ap.parse_args()
    env = dict(os.environ, HG_WEZTERM_DIR=args.wezterm_dir)
    for var in ("WEZTERM_PANE", "WEZTERM_UNIX_SOCKET"):
        env.pop(var, None)
    hgq = os.path.join(REPO, "dev", "qa", "hgq")
    slot = str(args.slot)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    run(hgq, "down", slot, env=env, check=False)
    run(os.path.join(HERE, "prepare.sh"), slot, env=env)
    run(hgq, "up", slot, "1920", "1080", env=env)
    d = Demo(args.slot, env)
    d.focus()
    d.cli("set-tab-title", "--pane-id", "0", "agents")
    time.sleep(1.5)
    raw = args.out + ".raw.mp4"
    ass = args.out + ".ass"
    starts = {}
    d.start(raw)
    try:
        wanted = set(args.only.split(",")) if args.only else None
        for scene in SCENES:
            if wanted and scene.__name__ not in wanted:
                continue
            starts[scene.__name__] = d.now()
            print(f"[{d.now():6.1f}s] {scene.__name__}", flush=True)
            scene(d)
    finally:
        if getattr(d, "phone", None):
            d.phone.close()
        d.stop()
    d.write_ass(ass, 1920, VIDEO_H)
    encode(raw, ass, args.out, starts)
    os.remove(raw)
    os.remove(ass)
    run(hgq, "down", slot, env=env)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
