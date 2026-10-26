#!/usr/bin/env bash
# Maintenance: copy the live setup into this repo (live -> repo). No network, no sudo.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cfg=$HOME/.config/wezterm
bindir=$HOME/.local/bin
icon=$HOME/.local/share/wezterm/wezterm-icon.png

changed=0
copy() { # src dst
  if [ ! -f "$1" ]; then echo "missing   $1" >&2; return; fi
  if [ -f "$2" ] && cmp -s "$1" "$2"; then return; fi
  if [ -f "$2" ]; then echo "updated   ${2#"$repo"/} ($(diff "$2" "$1" | grep -c '^[<>]') lines differ)"
  else echo "added     ${2#"$repo"/}"; fi
  mkdir -p "$(dirname "$2")"
  cp "$1" "$2"
  changed=$((changed + 1))
}

for f in "$cfg"/*.lua; do copy "$f" "$repo/config/$(basename "$f")"; done
for f in omp-grid claude-quota; do copy "$bindir/$f" "$repo/bin/$f"; done
copy "$icon" "$repo/assets/icon.png"

# Files removed from the live config must not linger in the repo.
for f in "$repo"/config/*.lua; do
  [ -f "$cfg/$(basename "$f")" ] || { echo "orphaned  ${f#"$repo"/} (no live counterpart; remove it by hand)"; }
done

echo "$changed file(s) changed"
