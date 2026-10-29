#!/usr/bin/env bash
# Install Hivegrid into the current user's HOME. Never uses sudo.
# From a checkout: ./install.sh. Without one: curl -fsSL <raw install.sh URL> | bash
# (see the README); it then fetches the repository itself and runs the installer from there.
set -euo pipefail

# The checkout this script sits in; empty when it is piped into bash (no file to sit in).
src=${BASH_SOURCE[0]:-}
repo=
[ -f "$src" ] && repo=$(cd "$(dirname "$src")" && pwd)
cfg=$HOME/.config/wezterm
yazi_dst=$HOME/.config/hivegrid/yazi
bindir=$HOME/.local/bin
icon_dst=$HOME/.local/share/wezterm/hivegrid-icon-2.png
desktop_dst=$HOME/.local/share/applications/org.wezfurlong.wezterm.desktop
stamp=$(date +%Y%m%d-%H%M%S)
want_tools=0
wezterm_mode=auto # auto: download the Hivegrid WezTerm build when none is installed; force; skip
opt=$HOME/.local/opt/hivegrid-wezterm
wezterm_asset=hivegrid-wezterm-x86_64-linux.tar.gz
wezterm_url=${HIVEGRID_WEZTERM_URL:-https://github.com/leongreiner/hivegrid/releases/latest/download/$wezterm_asset}
src_url=${HIVEGRID_SRC_URL:-https://github.com/leongreiner/hivegrid/archive/refs/heads/main.tar.gz}

usage() {
  cat <<'EOF'
Usage: ./install.sh [--tools] [--wezterm | --no-wezterm]
  --tools        also download yazi, ya and lazygit into ~/.local/bin
  --wezterm      download the Hivegrid WezTerm build again (update it)
  --no-wezterm   never download it; use the stock wezterm already installed
Without either WezTerm flag the build is downloaded only when `hivegrid` is not installed yet.
EOF
}
for arg in "$@"; do
  case "$arg" in
    --tools) want_tools=1 ;;
    --wezterm) wezterm_mode=force ;;
    --no-wezterm) wezterm_mode=skip ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

# Tools the installer needs; missing ones are named with the command that installs them.
# curl and tar are fatal (every download goes through them); python3 runs the panels and
# unzip unpacks the font, so Hivegrid installs without them but not usefully.
check_tools() {
  local fatal='' soft='' c
  for c in curl tar; do command -v "$c" >/dev/null 2>&1 || fatal="$fatal $c"; done
  for c in python3 unzip; do command -v "$c" >/dev/null 2>&1 || soft="$soft $c"; done
  if [ -n "$soft" ]; then
    echo "warning: missing$soft; Hivegrid needs python3 to run its panels and unzip to install its font" >&2
  fi
  if [ -n "$fatal" ]; then
    echo "missing$fatal. Debian/Ubuntu: sudo apt install curl tar; Fedora: sudo dnf install curl tar; Arch: sudo pacman -S curl tar" >&2
    exit 1
  fi
}
check_tools

# Piped into bash (or copied away from its checkout): fetch the repository, run its
# installer with the same arguments, then remove the copy. Everything is copied into HOME,
# so nothing refers to the download afterwards.
if [ -z "$repo" ] || [ ! -f "$repo/config/wezterm.lua" ]; then
  tmp=$(mktemp -d)
  echo "downloading Hivegrid ($src_url)"
  curl -fsSL "$src_url" | tar -xz -C "$tmp" || true
  inner=
  for f in "$tmp"/*/install.sh; do [ -f "$f" ] && inner=$f; done
  if [ -z "$inner" ]; then
    echo "could not download Hivegrid; clone https://github.com/leongreiner/hivegrid and run ./install.sh" >&2
    rm -rf "$tmp"
    exit 1
  fi
  rc=0
  bash "$inner" "$@" || rc=$?
  rm -rf "$tmp"
  exit "$rc"
fi

# install_wezterm: unpacks the pre-built Hivegrid WezTerm (a GitHub release asset, see
# dev/package-wezterm) into $opt and writes the `hivegrid` launcher. Files are renamed into
# place, so a running copy keeps its old inode.
install_wezterm() {
  local tmp pkg f dest
  command -v curl >/dev/null || { echo "curl is required to download the Hivegrid WezTerm build" >&2; return 1; }
  case "$(uname -m)" in
    x86_64 | amd64) ;;
    *) echo "there is no pre-built Hivegrid WezTerm for $(uname -m)" >&2; return 1 ;;
  esac
  tmp=$(mktemp -d)
  echo "downloading the Hivegrid WezTerm build ($wezterm_url)"
  if ! curl -fsSL "$wezterm_url" -o "$tmp/$wezterm_asset" \
      || ! curl -fsSL "$wezterm_url.sha256" -o "$tmp/$wezterm_asset.sha256"; then
    echo "download failed (no network, or the release asset is missing)" >&2
    rm -rf "$tmp"
    return 1
  fi
  if ! (cd "$tmp" && sha256sum -c --status "$wezterm_asset.sha256") || ! tar -xzf "$tmp/$wezterm_asset" -C "$tmp"; then
    echo "the downloaded archive is corrupt" >&2
    rm -rf "$tmp"
    return 1
  fi
  pkg=$tmp/hivegrid-wezterm
  mkdir -p "$opt/lib" "$bindir"
  for f in "$pkg"/*; do
    [ -f "$f" ] || continue
    dest=$opt/$(basename "$f")
    install -m "$([ -x "$f" ] && echo 755 || echo 644)" "$f" "$dest.new"
    mv -f "$dest.new" "$dest"
  done
  for f in "$pkg"/lib/*; do
    [ -f "$f" ] || continue
    dest=$opt/lib/$(basename "$f")
    install -m 644 "$f" "$dest.new"
    mv -f "$dest.new" "$dest"
  done
  # Libraries of an older build that this one no longer ships would shadow the system's.
  for f in "$opt"/lib/*; do
    [ -e "$pkg/lib/$(basename "$f")" ] || rm -f "$f"
  done
  cat >"$bindir/hivegrid.new" <<WRAP
#!/usr/bin/env bash
# Hivegrid WezTerm fork. Put its directory first on PATH so \`wezterm cli\` inside
# panes finds the matching binary.
D="$opt"
export LD_LIBRARY_PATH="\$D/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export PATH="\$D:\$PATH"
exec "\$D/wezterm-gui" "\$@"
WRAP
  chmod 755 "$bindir/hivegrid.new"
  mv -f "$bindir/hivegrid.new" "$bindir/hivegrid"
  rm -rf "$tmp"
  echo "installed $opt (launcher $bindir/hivegrid)"
  local missing
  missing=$(LD_LIBRARY_PATH=$opt/lib ldd "$opt/wezterm-gui" 2>&1 | awk '/not found/ { print $1 }' | tr '\n' ' ')
  if [ -n "$missing" ]; then
    echo "warning: the Hivegrid WezTerm needs system libraries that are missing: $missing" >&2
    echo "  (Debian/Ubuntu: sudo apt install libxcb1 libx11-xcb1 libxkbcommon0 libfontconfig1 libwayland-client0 libwayland-egl1 libssl3)" >&2
  fi
}

if [ "$wezterm_mode" = force ] || { [ "$wezterm_mode" = auto ] && [ ! -x "$bindir/hivegrid" ]; }; then
  if ! install_wezterm; then
    [ "$wezterm_mode" = force ] && exit 1
    echo "continuing without it; build it yourself: README, \"Custom WezTerm build\"" >&2
  fi
fi

wezterm_bin=$(command -v wezterm || true)
if [ -z "$wezterm_bin" ] && [ ! -x "$bindir/hivegrid" ]; then
  echo "no WezTerm to run Hivegrid with, and the Hivegrid build could not be downloaded." >&2
  echo "Install stock WezTerm (reduced features): https://wezterm.org/install/linux.html" >&2
  exit 1
fi
# The Hivegrid build's launcher takes precedence for the desktop entry.
launcher_bin=$wezterm_bin
[ -x "$bindir/hivegrid" ] && launcher_bin=$bindir/hivegrid
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
while IFS= read -r f; do install_file "$f" "$yazi_dst/${f#"$repo"/config/yazi/}"; done \
  < <(find "$repo/config/yazi" -type f | sort)
for f in "$repo"/bin/*; do [ -f "$f" ] && install_file "$f" "$bindir/$(basename "$f")" 755; done
# omp extension publishing agent state for the hive panel (default agent dir + every profile)
for agent_dir in "$HOME/.omp/agent" "$HOME"/.omp/profiles/*/agent; do
  [ "$agent_dir" = "$HOME/.omp/agent" ] || [ -d "$agent_dir" ] || continue
  install_file "$repo/ext/hivegrid-agent-state.ts" "$agent_dir/extensions/hivegrid-agent-state.ts" 644
done
install_file "$repo/assets/icon.png" "$icon_dst"
# Icon font of the sidebar bar (hivegrid-bar draws its buttons from it; text glyphs without it).
font_src=$repo/wezterm/assets/fonts/SymbolsNerdFontMono-Regular.ttf
[ -f "$font_src" ] || font_src=$opt/SymbolsNerdFontMono-Regular.ttf # clone without the submodule
[ -f "$font_src" ] && install_file "$font_src" "$HOME/.local/share/hivegrid/SymbolsNerdFontMono-Regular.ttf" 644
# Terminal text font: DejaVu Sans Mono patched with Nerd Font icons (family "DejaVuSansM Nerd Font Mono").
# Without it WezTerm falls back to plain DejaVu Sans Mono and Nerd Font icons show as boxes.
nerd_dir=$HOME/.local/share/fonts/DejaVuSansMNerdFont
install_nerd_font() {
  [ -f "$nerd_dir/DejaVuSansMNerdFontMono-Regular.ttf" ] && return 0
  local tmp
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/font.zip" https://github.com/ryanoasis/nerd-fonts/releases/latest/download/DejaVuSansMono.zip \
    && unzip -q -o "$tmp/font.zip" 'DejaVuSansMNerdFontMono-*.ttf' LICENSE.txt -d "$tmp" \
    && mkdir -p "$nerd_dir" \
    && cp "$tmp"/DejaVuSansMNerdFontMono-*.ttf "$tmp/LICENSE.txt" "$nerd_dir/" \
    && { ! command -v fc-cache >/dev/null 2>&1 || fc-cache -f "$nerd_dir"; }
  local rc=$?
  rm -rf "$tmp"
  return $rc
}
install_nerd_font || echo "warning: could not install DejaVuSansM Nerd Font Mono; see README (Install)" >&2

# Web panes (the Hivegrid WezTerm build) drive Chrome or Chromium headless; nothing is installed for them.
have_chrome() {
  local c
  [ -n "${HIVEGRID_CHROME:-}" ] && [ -x "$HIVEGRID_CHROME" ] && return 0
  [ -x /opt/google/chrome/chrome ] && return 0
  for c in google-chrome-stable google-chrome chromium chromium-browser; do
    command -v "$c" >/dev/null 2>&1 && return 0
  done
  return 1
}
have_chrome || echo "warning: web panes need a Chromium-family browser such as Google Chrome or Chromium (Settings, Web, or HIVEGRID_CHROME, names another one); the rest of Hivegrid works without it" >&2

rendered=$(mktemp)
trap 'rm -f "$rendered"' EXIT
sed -e "s|@HOME@|$HOME|g" -e "s|@WEZTERM@|$launcher_bin|g" "$repo/desktop/hivegrid.desktop.in" >"$rendered"
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
