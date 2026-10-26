#!/usr/bin/env bash
# Install Hivegrid into the current user's HOME. Never uses sudo.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cfg=$HOME/.config/wezterm
bindir=$HOME/.local/bin
icon_dst=$HOME/.local/share/wezterm/wezterm-icon.png
desktop_dst=$HOME/.local/share/applications/org.wezfurlong.wezterm.desktop
stamp=$(date +%Y%m%d-%H%M%S)
want_tools=0

usage() { echo "Usage: $0 [--tools]   (--tools also downloads yazi and lazygit into ~/.local/bin)"; }
case "${1:-}" in
  "") ;;
  --tools) want_tools=1 ;;
  -h | --help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

wezterm_bin=$(command -v wezterm || true)
if [ -z "$wezterm_bin" ]; then
  echo "wezterm not found. Install it first: https://wezterm.org/install/linux.html" >&2
  echo "  Debian/Ubuntu: add the apt.fury.io/wez repository described there, then" >&2
  echo "  sudo apt install wezterm" >&2
  exit 1
fi

# install_file SRC DST [MODE]: copies, keeping one backup of a differing existing file.
install_file() {
  local src=$1 dst=$2 mode=${3:-644}
  mkdir -p "$(dirname "$dst")"
  if [ -e "$dst" ] && ! cmp -s "$src" "$dst"; then
    cp -p "$dst" "$dst.bak.$stamp"
    echo "backup    $dst.bak.$stamp"
  fi
  if [ -e "$dst" ] && cmp -s "$src" "$dst"; then
    chmod "$mode" "$dst"
    echo "unchanged $dst"
  else
    install -m "$mode" "$src" "$dst"
    echo "installed $dst"
  fi
}

for f in "$repo"/config/*.lua; do install_file "$f" "$cfg/$(basename "$f")"; done
for f in "$repo"/bin/*; do install_file "$f" "$bindir/$(basename "$f")" 755; done
install_file "$repo/assets/icon.png" "$icon_dst"

rendered=$(mktemp)
trap 'rm -f "$rendered"' EXIT
sed -e "s|@HOME@|$HOME|g" -e "s|@WEZTERM@|$wezterm_bin|g" "$repo/desktop/hivegrid.desktop.in" >"$rendered"
install_file "$rendered" "$desktop_dst"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$(dirname "$desktop_dst")" >/dev/null 2>&1 || true
  echo "ran       update-desktop-database"
fi

# fetch_release OWNER/REPO REGEX: prints the download URL of the first release asset matching REGEX.
fetch_release() {
  curl -fsSL "https://api.github.com/repos/$1/releases/latest" |
    grep -o '"browser_download_url": *"[^"]*"' | sed 's/.*: *"//; s/"$//' | grep -iE "$2" | head -n1 || true
}

install_tools() {
  command -v curl >/dev/null || { echo "curl is required for --tools" >&2; exit 1; }
  command -v python3 >/dev/null || { echo "python3 is required for --tools" >&2; exit 1; }
  local arch yazi_arch lg_arch tmp url
  arch=$(uname -m)
  case "$arch" in
    x86_64 | amd64) yazi_arch=x86_64; lg_arch=x86_64 ;;
    aarch64 | arm64) yazi_arch=aarch64; lg_arch=arm64 ;;
    *) echo "unsupported architecture for --tools: $arch" >&2; exit 1 ;;
  esac
  tmp=$(mktemp -d)
  mkdir -p "$bindir"

  url=$(fetch_release sxyazi/yazi "yazi-${yazi_arch}-unknown-linux-gnu\.zip$")
  [ -n "$url" ] || { echo "no yazi asset found for $arch" >&2; exit 1; }
  curl -fsSL "$url" -o "$tmp/yazi.zip"
  python3 - "$tmp/yazi.zip" "$tmp" <<'PY'
import sys, zipfile
zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])
PY
  install -m 755 "$tmp"/yazi-*/yazi "$bindir/yazi"
  install -m 755 "$tmp"/yazi-*/ya "$bindir/ya"
  echo "installed $bindir/yazi and $bindir/ya"

  url=$(fetch_release jesseduffield/lazygit "lazygit_[0-9.]+_linux_${lg_arch}\.tar\.gz$")
  [ -n "$url" ] || { echo "no lazygit asset found for $arch" >&2; exit 1; }
  curl -fsSL "$url" -o "$tmp/lazygit.tar.gz"
  tar -xzf "$tmp/lazygit.tar.gz" -C "$tmp" lazygit
  install -m 755 "$tmp/lazygit" "$bindir/lazygit"
  echo "installed $bindir/lazygit"
  rm -rf "$tmp"
}

((want_tools)) && install_tools

case ":$PATH:" in
  *":$bindir:"*) ;;
  *) echo "note      $bindir is not on PATH; omp-grid and the sidebar tools are looked up there" ;;
esac
echo "done. Restart WezTerm (or press CTRL+SHIFT+R) to load the config."
