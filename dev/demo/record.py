#!/usr/bin/env python3
"""Records the demo video of Hivegrid (the one in the README) from the QA sandbox.

It prepares and starts QA slot SLOT (dev/demo/prepare.sh: fixture repositories, fictional accounts and
usage, no real names or chats), drives the real window with synthetic keys and clicks, records the
Xvfb display with ffmpeg and burns the captions in below the picture. Real omp sessions run in the
panes, so it needs a logged-in omp profile (prepare.sh: DEMO_OMP_SRC) and uses a few trivial prompts of
that account's quota. Chrome is needed for the web pane scene.

  dev/demo/record.py [--slot 20] [--wezterm-dir DIR] [--out assets/demo/hivegrid-demo.mp4]

--wezterm-dir is the Hivegrid WezTerm build (default ~/.local/opt/hivegrid-wezterm). Output: the mp4,
and next to it a short animated preview (hivegrid-demo.gif) for the README.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from hgdemo import Demo  # noqa: E402

BAR = "0x070d19"  # the strip below the picture that carries the captions
PICTURE_H, VIDEO_H = 1080, 1144
PILL = {"work": (448, 23), "team": (717, 23), "home": (960, 23)}  # header chips (the tab title is fixed)
DOCK_X = 1670
EQUALIZE = (1886, 72)
GEAR = (22, 1009)
BAR_EXPLORER, BAR_GIT = (22, 106), (22, 157)
SHOTS = os.path.join(REPO, "assets", "screenshots")  # stills for the README, taken during the recording
TEAM_ROW = (1500, 156)  # the team row of the usage panel's account table


def dock_row(i):
    return DOCK_X, 105 + 51 * (i - 1)


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
    d.caption("The header shows each account's Claude usage and the machine's load", 5)
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
    d.caption("The overview dock lists every pane: state, task, directory, time", 5)
    d.move(*dock_row(2), 0.8)
    time.sleep(1.5)
    prompts = [
        "How many lines of Python are in bin/? Reply with just the number.",
        'Run `sleep 40` in the shell, then reply "done".',
        "Summarise this repo in one short sentence.",
        "What does run.sh do? One short sentence.",
    ]
    d.caption("Give every agent a task", 3.5)
    for i, text in enumerate(prompts, 1):
        d.click(*dock_row(i))
        time.sleep(0.5)
        d.type(text, delay=0.03)
        time.sleep(0.3)
    d.caption("Four agents work at once; the dock shows who works, waits or is done", 7)
    for i in range(1, 5):
        d.click(*dock_row(i), dur=0.35, settle=0.1)
        d.key("Return", gap=0.2)
    d.move(*dock_row(2), 0.6)
    time.sleep(8)
    still(d, "agents")


def scene_grid(d):
    d.caption("Split a pane: it stays where the split puts it; nothing re-tiles by itself", 5)
    d.click(*dock_row(4))
    time.sleep(0.6)
    d.key("ctrl+Right", gap=2.5)
    time.sleep(2.0)
    d.caption("Press Ctrl+Shift+A to arrange the grid on demand", 4)
    d.key("ctrl+shift+a", gap=2.5)
    time.sleep(2.0)
    d.caption("Drag a divider out of shape, then press ▦: every divider is even again", 6)
    d.drag(500, 549, 500, 790)
    time.sleep(1.8)
    d.click(*EQUALIZE)
    time.sleep(3.0)


def scene_switch(d):
    d.caption("Switch account mid-session: the same chat continues under another profile", 6.5)
    d.click(*dock_row(3))
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
    d.caption("Every pane of the window, and the agent hive", 4)
    d.click(288, 72)
    time.sleep(2.5)
    d.click(431, 72)
    time.sleep(4.0)
    d.key("q", gap=1.5)


def scene_explorer(d):
    d.caption("File explorer: browse, preview, open a terminal or an editor right there", 6)
    d.click(*dock_row(5))
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
    d.click(*dock_row(5))
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
    d.key("q", gap=2.5)


def scene_web(d):
    d.caption("Web pane: a real browser page as a pane of the grid", 5)
    d.click(*dock_row(5))
    time.sleep(0.8)
    d.leader("Tab", gap=1.5)  # hide the sidebar: the page gets the room
    d.leader("w", "n", gap=1.5)
    time.sleep(1.0)
    d.type("github.com/can1357/oh-my-pi", delay=0.06)
    time.sleep(0.6)
    d.key("Return", gap=1.0)
    time.sleep(7.0)
    d.caption("Zoom it like any pane, scroll, click; the dock lists it too", 5)
    d.key("alt+z", gap=3.0)
    still(d, "web")
    d.scroll(900, 600, "down", 4)
    time.sleep(1.5)
    d.scroll(900, 600, "up", 4)
    time.sleep(1.2)
    d.key("alt+z", gap=2.5)


def scene_settings(d):
    d.caption("Settings: colour themes with live preview, key bindings, accounts", 5)
    d.click(*GEAR)
    time.sleep(3.0)
    still(d, "settings")
    d.click(640, 343)  # Forest
    time.sleep(1.5)
    d.key("s", gap=3.5)
    d.click(640, 411)  # Forest
    time.sleep(1.2)
    d.key("s", gap=3.0)
    d.click(640, 208)  # Navy
    time.sleep(1.2)
    d.key("s", gap=2.5)
    for x, y in ((373, 156), (386, 173), (363, 190)):  # Keys, Accounts, Web
        d.click(x, y)
        time.sleep(2.2)
    d.key("q", gap=2.0)


def scene_outro(d):
    d.click(*dock_row(1))
    d.caption("Hivegrid is open source and in alpha: github.com/leongreiner/hivegrid", 6)
    time.sleep(6.5)


SCENES = [scene_intro, scene_tasks, scene_grid, scene_switch, scene_panels, scene_explorer, scene_git,
          scene_web, scene_settings, scene_outro]


def run(*cmd, env=None, check=True):
    return subprocess.run(cmd, env=env, check=check, cwd=REPO)


# The preview gif of the README: (offset, length) in seconds from the start of each scene, played faster.
HIGHLIGHTS = {
    "scene_intro": [(6, 8)], "scene_tasks": [(10, 14)], "scene_grid": [(0.5, 12)], "scene_switch": [(0.7, 10)],
    "scene_panels": [(3, 5), (18, 4), (26, 4)], "scene_explorer": [(1.5, 10)], "scene_git": [(3, 8), (15, 6)],
    "scene_web": [(1.5, 19)], "scene_settings": [(2, 14)],
}
GIF_SPEED = 1.7


def encode(raw, ass, out, starts):
    """Burn the captions into a strip below the picture; write the mp4 and the preview gif."""
    vf = f"pad=1920:{VIDEO_H}:0:0:color={BAR},ass={ass}"
    run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", raw, "-vf", vf, "-c:v", "libx264",
        "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", out)
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
        d.stop()
    d.write_ass(ass, 1920, VIDEO_H)
    encode(raw, ass, args.out, starts)
    os.remove(raw)
    os.remove(ass)
    run(hgq, "down", slot, env=env)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
