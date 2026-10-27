#!/usr/bin/env bash
# Prepares QA slot SLOT (default 20) for recording the demo video (dev/demo/record.py): fixture
# repositories, three fictional accounts (work, team, home) with fictional usage numbers, a plain
# prompt, and omp profiles that share the real login but keep their own sessions, so no real
# account name, usage or chat history shows up, and `omp-switch` only copies files inside the
# sandbox. The profiles run this checkout's ext/hivegrid-agent-state.ts (the phone remote switches model
# and thinking through it), the web pane gets an empty browser profile of its own, and an earlier
# recording's session restore state is dropped. Run it with the slot stopped (dev/qa/hgq down SLOT), then start the slot.
#
#   dev/demo/prepare.sh [SLOT]
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
SLOT=${1:-20}
QA=${HGQ_ROOT:-/tmp/hivegrid-qa}
S=$QA/s$SLOT
H=$S/home
REAL=$HOME
# The omp installation whose login the demo profiles share: ~/.omp (the default profile) or
# ~/.omp/profiles/NAME, which must be logged in.
DEMO_OMP_SRC=${DEMO_OMP_SRC:-$REAL/.omp}
[ -f "$DEMO_OMP_SRC/agent/agent.db" ] || { echo "prepare: no omp login at $DEMO_OMP_SRC/agent (set DEMO_OMP_SRC to a logged-in profile)" >&2; exit 2; }
grep -q 'default:' "$DEMO_OMP_SRC/agent/config.yml" 2>/dev/null || { echo "prepare: $DEMO_OMP_SRC/agent/config.yml sets no default model (modelRoles); agents would answer 'No model selected'. Set DEMO_OMP_SRC to a profile that has one" >&2; exit 2; }
[ -e "$S/xvfb.pid" ] && { echo "prepare: slot $SLOT is running; dev/qa/hgq down $SLOT first" >&2; exit 2; }

mkdir -p "$H/.config/hivegrid" "$H/.local/bin" "$H/.cache" "$S/work" "$S/shots"

# Fixture repositories under the sandbox's own ~/Projects.
rm -rf "$H/Projects"
"$REPO/dev/demo/fixtures.sh" "$H/Projects" "$S/work/remotes"

# Nothing left over from an earlier run: agent state files are named by pane id, which restarts at 0,
# and the monitor would show a gap for the time the slot was down.
rm -rf "$S/run/hivegrid-agents" "$H/.cache/hivegrid/sysstats.jsonl" "$H/.cache/hivegrid/sysstats.jsonl.1"
# Session restore would rebuild the previous recording's layout: start from an empty one.
rm -rf "$H/.local/state/hivegrid"
# hgq links ~/.local/share/hivegrid to the real one, which holds the user's web pane browser profile (signed in
# to sites). The demo gets an empty directory of its own: a fresh, signed-out profile. (No trailing slash: rm
# removes the link, never the real directory.)
rm -rf "$H/.local/share/hivegrid"
mkdir -p "$H/.local/share/hivegrid"
# lazygit shows a welcome popup on its first run and a notice after an update; the sandbox's state says both were seen.
mkdir -p "$H/.local/state/lazygit"
printf 'startuppopupversion: 5\nlastversion: %s\n' "$(lazygit --version | sed -n 's/.*version=\([0-9.]*\),.*/\1/p')" >"$H/.local/state/lazygit/state.yml"

# A prompt without the real user and host name.
cat >"$H/.bashrc" <<'EOF'
case $- in *i*) ;; *) return ;; esac
[ -f ~/.bash-preexec.sh ] && source ~/.bash-preexec.sh
export PATH="$HOME/.local/bin:$PATH"
export OMP_PROFILE=work
export OMP_SKIP_SETUP=1
export EDITOR=nano
PS1='\[\e[1;32m\]demo@hive\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\]\$ '
EOF

# omp: ~/.omp holds links to the real installation (binary, natives, plugins), except profiles/,
# where each demo profile has its own sessions and shares the login and settings of one real profile.
rm -rf "$H/.omp"
mkdir -p "$H/.omp/profiles"
for f in "$REAL"/.omp/*; do
  case $(basename "$f") in profiles | *.tgz) ;; *) ln -s "$f" "$H/.omp/$(basename "$f")" ;; esac
done
for p in work team home; do
  a=$H/.omp/profiles/$p/agent
  mkdir -p "$a/sessions" "$a/terminal-sessions" "$a/blobs" "$H/.omp/profiles/$p/logs"
  for f in agent.db agent.db-shm agent.db-wal models.db models.db-shm models.db-wal cache; do
    [ -e "$DEMO_OMP_SRC/agent/$f" ] && ln -sfn "$DEMO_OMP_SRC/agent/$f" "$a/$f"
  done
  # The extensions of the source profile, except Hivegrid's own: the sandbox runs this checkout's
  # version (model and thinking switching for the phone remote), the real directory stays untouched.
  rm -rf "$a/extensions"
  mkdir -p "$a/extensions"
  for f in "$DEMO_OMP_SRC"/agent/extensions/*; do
    [ -e "$f" ] || continue
    case $(basename "$f") in hivegrid-agent-state.ts*) ;; *) ln -sfn "$f" "$a/extensions/$(basename "$f")" ;; esac
  done
  ln -sfn "$REPO/ext/hivegrid-agent-state.ts" "$a/extensions/hivegrid-agent-state.ts"
  # Settings of the source profile, without the collab relay (it prints connection errors offline) and
  # without the update notice.
  awk '/^collab:/ { skip = 1; next } skip && /^[ \t]/ { next } { skip = 0; print }' "$DEMO_OMP_SRC/agent/config.yml" >"$a/config.yml"
  printf 'startup:\n  checkUpdate: false\n' >>"$a/config.yml"
done

# The three accounts of the header: names and numbers are fictional (dev/demo/fake-claude-quota).
rm -rf "$H/.cache/wezterm-claude-quota" "$H/.local/bin/claude-quota"
cp "$REPO/dev/demo/fake-claude-quota" "$H/.local/bin/claude-quota"
HOME=$H "$H/.local/bin/claude-quota" refresh
python3 - "$H/.config/hivegrid/settings.json" <<'EOF'
import json, os, sys
p = sys.argv[1]
s = json.load(open(p)) if os.path.exists(p) else {"version": 1}
s.pop("theme", None)  # the default theme (a recording saves the ones it clicks)
s["accounts"] = [{"label": l, "profile": l, "dir": d, "auto": True, "hidden": False}
                 for l, d in (("work", "~/.claude"), ("team", "~/.claude-2"), ("home", "~/.claude-private"))]
json.dump(s, open(p, "w"), indent=2)
EOF
echo "slot $SLOT is ready for dev/demo/record.py (start it with dev/qa/hgq up $SLOT 1920 1080)"
