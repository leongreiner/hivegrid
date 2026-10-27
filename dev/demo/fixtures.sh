#!/usr/bin/env bash
# Builds the fixture workspace of the demo video under DIR (default
# /tmp/hivegrid-qa/s20/home/Projects), the ~/Projects of QA slot 20, so
# the recording never shows a real home directory. Repositories:
#   hivegrid     a clone of this repository with one modified file (the CI mark comes from GitHub)
#   omp-notes    clean, two commits behind its bare remote (lazygit pulls them in the demo)
#   site         modified, staged and untracked files
#   scripts      one commit ahead of its bare remote
#   experiments  clean
#
#   dev/demo/fixtures.sh [DIR [REMOTES-DIR]]
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
DIR=${1:-/tmp/hivegrid-qa/s20/home/Projects}
REMOTES=${2:-/tmp/hivegrid-qa/s20/work/remotes}
rm -rf "$DIR" "$REMOTES"
mkdir -p "$DIR" "$REMOTES"

export GIT_AUTHOR_NAME="Demo" GIT_AUTHOR_EMAIL="demo@example.com"
export GIT_COMMITTER_NAME="Demo" GIT_COMMITTER_EMAIL="demo@example.com"
g() { git -C "$1" -c init.defaultBranch=main "${@:2}"; }

# A repository with a bare remote: new NAME [BEHIND] [AHEAD], files written by the caller's hook.
new_repo() {
  local name=$1 behind=${2:-0} ahead=${3:-0}
  local w=$DIR/$name b=$REMOTES/$name.git
  git init -q --bare -b main "$b"
  git init -q -b main "$w"
  g "$w" remote add origin "$b"
  seed "$name" "$w"
  g "$w" add -A
  g "$w" commit -q -m "Initial commit"
  g "$w" push -q -u origin main
  for i in $(seq "$behind"); do # commits only the remote has: written from a scratch clone
    local t
    t=$(mktemp -d)
    git clone -q "$b" "$t/c"
    printf 'Entry %s: notes added from another machine.\n' "$i" >>"$t/c/NOTES.md"
    g "$t/c" commit -q -am "Add notes entry $i"
    g "$t/c" push -q origin main
    rm -rf "$t"
  done
  g "$w" fetch -q origin
  for i in $(seq "$ahead"); do
    printf '# change %s\n' "$i" >>"$w/run.sh"
    g "$w" commit -q -am "Tweak run.sh ($i)"
  done
}

seed() {
  case $1 in
    omp-notes)
      printf '# Notes\n\nIdeas for the agents.\n' >"$2/NOTES.md"
      mkdir -p "$2/prompts"
      printf 'Summarise the diff in three lines.\n' >"$2/prompts/summary.txt" ;;
    site)
      mkdir -p "$2/src" "$2/public"
      printf '<h1>Hello</h1>\n' >"$2/public/index.html"
      printf 'body { margin: 0; }\n' >"$2/src/style.css"
      printf '# site\n' >"$2/README.md" ;;
    scripts)
      printf '#!/bin/sh\necho run\n' >"$2/run.sh"
      chmod +x "$2/run.sh"
      printf '#!/bin/sh\necho backup\n' >"$2/backup.sh" ;;
    experiments)
      mkdir -p "$2/notebooks" "$2/data"
      printf 'print("hi")\n' >"$2/train.py"
      printf 'a,b\n1,2\n' >"$2/data/sample.csv" ;;
  esac
}

new_repo omp-notes 2 0
new_repo site
new_repo scripts 0 1
new_repo experiments

# Dirty working tree of "site": one modified, one staged, one untracked file.
printf 'a { color: #4f9dff; }\n' >>"$DIR/site/src/style.css"
printf '<p>New section</p>\n' >>"$DIR/site/public/index.html"
g "$DIR/site" add public/index.html
printf 'TODO: dark mode\n' >"$DIR/site/TODO.txt"

# hivegrid: a clone of this repository (committed files only) whose origin is the GitHub repository,
# so the git view reads its real CI state.
git clone -q "$REPO" "$DIR/hivegrid"
g "$DIR/hivegrid" remote set-url origin https://github.com/leongreiner/hivegrid.git
g "$DIR/hivegrid" update-ref refs/remotes/origin/main HEAD
g "$DIR/hivegrid" branch -q -u origin/main
printf '\nA note.\n' >>"$DIR/hivegrid/README.md"

echo "fixtures ready in $DIR"
